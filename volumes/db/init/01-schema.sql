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

-- ===========================================================================
-- annotations — manual polygon labels for tissue classes.  Used as
-- training data for the deep-learning segmentation that arrives in PR #5.
--
-- NB: clients talk to PostgREST directly with the anon/JWT key, so DB-level
-- validation is the only thing stopping a malicious caller from writing an
-- unknown class or a malformed polygon that later crashes the renderer
-- (and poisons training data).  Keep these checks in sync with
-- frontend/lib/tissue-classes.ts + backend/app/pipeline/classes.py.
-- ===========================================================================
CREATE OR REPLACE FUNCTION public.is_valid_polygon(p jsonb) RETURNS boolean
  LANGUAGE sql IMMUTABLE
AS $$
  SELECT p IS NOT NULL
     AND jsonb_typeof(p) = 'array'
     AND jsonb_array_length(p) >= 3
     AND NOT EXISTS (
       SELECT 1
       FROM jsonb_array_elements(p) AS elem
       WHERE jsonb_typeof(elem) <> 'array'
          OR jsonb_array_length(elem) <> 2
          OR jsonb_typeof(elem -> 0) <> 'number'
          OR jsonb_typeof(elem -> 1) <> 'number'
     )
$$;

CREATE TABLE IF NOT EXISTS public.annotations (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  image_id    uuid NOT NULL REFERENCES public.images(id)    ON DELETE CASCADE,
  owner_id    uuid NOT NULL REFERENCES public.profiles(id)  ON DELETE CASCADE,
  class       text NOT NULL CHECK (class IN (
    'upper_epidermis', 'lower_epidermis', 'palisade', 'spongy',
    'bundle_sheath', 'xylem', 'phloem', 'stomata', 'intercellular', 'other'
  )),
  polygon     jsonb NOT NULL CHECK (public.is_valid_polygon(polygon)),
  note        text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS annotations_image_idx ON public.annotations(image_id);
CREATE INDEX IF NOT EXISTS annotations_owner_idx ON public.annotations(owner_id);

ALTER TABLE public.annotations ENABLE ROW LEVEL SECURITY;

-- Anyone who can read the image can read its annotations.  That keeps
-- lab-shared and public images collaboratively annotatable without extra
-- policy plumbing.
DROP POLICY IF EXISTS "annotations read"        ON public.annotations;
CREATE POLICY "annotations read"
  ON public.annotations FOR SELECT
  TO anon, authenticated
  USING (EXISTS (SELECT 1 FROM public.images WHERE id = annotations.image_id));

-- Any authenticated user can label an image they can read.  Each annotator
-- stores their own rows — good for tracking who labelled what.
DROP POLICY IF EXISTS "annotations insert"      ON public.annotations;
CREATE POLICY "annotations insert"
  ON public.annotations FOR INSERT
  TO authenticated
  WITH CHECK (
    owner_id = auth.uid()
    AND EXISTS (SELECT 1 FROM public.images WHERE id = annotations.image_id)
  );

-- Updates must keep the row both owned by the caller AND attached to an
-- image the caller can still read — otherwise an annotator could re-point
-- their row at an image they have no access to and poison its training
-- data (PostgREST accepts direct column updates by default).
DROP POLICY IF EXISTS "annotations owner update" ON public.annotations;
CREATE POLICY "annotations owner update"
  ON public.annotations FOR UPDATE
  TO authenticated
  USING (
    owner_id = auth.uid()
    AND EXISTS (SELECT 1 FROM public.images WHERE id = annotations.image_id)
  )
  WITH CHECK (
    owner_id = auth.uid()
    AND EXISTS (SELECT 1 FROM public.images WHERE id = annotations.image_id)
  );

DROP POLICY IF EXISTS "annotations owner delete" ON public.annotations;
CREATE POLICY "annotations owner delete"
  ON public.annotations FOR DELETE
  TO authenticated
  USING (owner_id = auth.uid());

DROP TRIGGER IF EXISTS annotations_set_updated_at ON public.annotations;
CREATE TRIGGER annotations_set_updated_at
  BEFORE UPDATE ON public.annotations
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
