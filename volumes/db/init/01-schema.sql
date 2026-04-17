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
