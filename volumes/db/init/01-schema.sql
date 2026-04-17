-- ---------------------------------------------------------------------------
-- Application schema
--
-- Runs after 00-init.sql (which creates roles and the auth/storage schemas).
-- GoTrue and storage-api will run their own migrations on first boot to
-- populate tables inside `auth.*` / `storage.*`; we wait for that before
-- adding references from the public schema at request time via RLS rather
-- than hard foreign keys into `auth.users`.
-- ---------------------------------------------------------------------------

-- ===========================================================================
-- profiles — one row per auth.users, carries role + display info
-- ===========================================================================
CREATE TABLE IF NOT EXISTS public.profiles (
  id            uuid PRIMARY KEY,                    -- matches auth.users.id
  display_name  text,
  role          text NOT NULL DEFAULT 'member'
                CHECK (role IN ('admin', 'member')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

-- Everyone authenticated can read profiles (needed to show uploaders'
-- display names on lab/public images).  Writes are locked down.
DROP POLICY IF EXISTS "profiles readable by authenticated" ON public.profiles;
CREATE POLICY "profiles readable by authenticated"
  ON public.profiles FOR SELECT
  TO authenticated
  USING (true);

DROP POLICY IF EXISTS "profiles owner can update" ON public.profiles;
CREATE POLICY "profiles owner can update"
  ON public.profiles FOR UPDATE
  TO authenticated
  USING (id = auth.uid())
  WITH CHECK (id = auth.uid() AND role = (SELECT role FROM public.profiles WHERE id = auth.uid()));
  -- users cannot escalate their own role; admins change roles via service_role.

-- ===========================================================================
-- images — one row per uploaded microscope image
-- ===========================================================================
CREATE TABLE IF NOT EXISTS public.images (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id            uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  visibility          text NOT NULL DEFAULT 'private'
                      CHECK (visibility IN ('private', 'lab', 'public')),
  source              text NOT NULL DEFAULT 'manual_upload',
                      -- 'manual_upload' | 'device:<serial>' (future)
  storage_path        text NOT NULL UNIQUE,          -- path inside `images` bucket
  original_filename   text,
  content_type        text,
  width_px            integer,
  height_px           integer,
  scale_um_per_px     double precision,              -- NULL until calibrated
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS images_owner_idx       ON public.images(owner_id);
CREATE INDEX IF NOT EXISTS images_visibility_idx  ON public.images(visibility);

ALTER TABLE public.images ENABLE ROW LEVEL SECURITY;

-- ---- image read policies ----------------------------------------------
-- public rows: anyone (including anon) can read
DROP POLICY IF EXISTS "images public read"  ON public.images;
CREATE POLICY "images public read"
  ON public.images FOR SELECT
  TO anon, authenticated
  USING (visibility = 'public');

-- lab rows: any authenticated user can read
DROP POLICY IF EXISTS "images lab read"     ON public.images;
CREATE POLICY "images lab read"
  ON public.images FOR SELECT
  TO authenticated
  USING (visibility = 'lab');

-- private rows: only the owner
DROP POLICY IF EXISTS "images owner read"   ON public.images;
CREATE POLICY "images owner read"
  ON public.images FOR SELECT
  TO authenticated
  USING (owner_id = auth.uid());

-- ---- image write policies ---------------------------------------------
DROP POLICY IF EXISTS "images owner insert" ON public.images;
CREATE POLICY "images owner insert"
  ON public.images FOR INSERT
  TO authenticated
  WITH CHECK (owner_id = auth.uid());

DROP POLICY IF EXISTS "images owner update" ON public.images;
CREATE POLICY "images owner update"
  ON public.images FOR UPDATE
  TO authenticated
  USING (owner_id = auth.uid())
  WITH CHECK (owner_id = auth.uid());

DROP POLICY IF EXISTS "images owner delete" ON public.images;
CREATE POLICY "images owner delete"
  ON public.images FOR DELETE
  TO authenticated
  USING (owner_id = auth.uid());

-- updated_at bumping
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END$$;

DROP TRIGGER IF EXISTS images_set_updated_at ON public.images;
CREATE TRIGGER images_set_updated_at
  BEFORE UPDATE ON public.images
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS profiles_set_updated_at ON public.profiles;
CREATE TRIGGER profiles_set_updated_at
  BEFORE UPDATE ON public.profiles
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ===========================================================================
-- analyses — pipeline results (scale/leaf-region/measurement, and later
-- tissue-segmentation, water-transport, …).  One row per run per kind.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS public.analyses (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  image_id      uuid NOT NULL REFERENCES public.images(id) ON DELETE CASCADE,
  kind          text NOT NULL,                 -- e.g. 'basic_measurement'
  status        text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'done', 'error')),
  parameters    jsonb,
  result        jsonb,
  error         text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS analyses_image_idx ON public.analyses(image_id);
CREATE INDEX IF NOT EXISTS analyses_kind_idx  ON public.analyses(kind);

ALTER TABLE public.analyses ENABLE ROW LEVEL SECURITY;

-- Delegate visibility/writability to the linked image's own RLS.  EXISTS
-- against public.images re-enters the image policies with the caller's JWT,
-- so the access rules stay in one place.
DROP POLICY IF EXISTS "analyses read"         ON public.analyses;
CREATE POLICY "analyses read"
  ON public.analyses FOR SELECT
  TO anon, authenticated
  USING (EXISTS (SELECT 1 FROM public.images WHERE id = analyses.image_id));

DROP POLICY IF EXISTS "analyses owner insert" ON public.analyses;
CREATE POLICY "analyses owner insert"
  ON public.analyses FOR INSERT
  TO authenticated
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM public.images
      WHERE id = analyses.image_id AND owner_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "analyses owner update" ON public.analyses;
CREATE POLICY "analyses owner update"
  ON public.analyses FOR UPDATE
  TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.images
      WHERE id = analyses.image_id AND owner_id = auth.uid()
    )
  )
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM public.images
      WHERE id = analyses.image_id AND owner_id = auth.uid()
    )
  );

DROP POLICY IF EXISTS "analyses owner delete" ON public.analyses;
CREATE POLICY "analyses owner delete"
  ON public.analyses FOR DELETE
  TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.images
      WHERE id = analyses.image_id AND owner_id = auth.uid()
    )
  );

DROP TRIGGER IF EXISTS analyses_set_updated_at ON public.analyses;
CREATE TRIGGER analyses_set_updated_at
  BEFORE UPDATE ON public.analyses
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- NOTE: public.annotations (PR #4) is defined in volumes/db/init/02-after-services.sql.tmpl
-- — Postgres's initdb runs /docker-entrypoint-initdb.d/*.sql only on the
-- first-ever boot of a data volume, so anything added to this file later
-- would silently skip existing deployments.  Schema extensions after the
-- initial release therefore live in files applied by the supabase-bootstrap
-- service, which psql-applies them idempotently on every `docker compose up`.
