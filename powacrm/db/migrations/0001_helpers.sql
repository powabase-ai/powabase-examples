CREATE EXTENSION IF NOT EXISTS unaccent;

-- Plain unaccent() is STABLE; generated columns need IMMUTABLE. Pinning the
-- dictionary makes it safe. [Twenty]
CREATE OR REPLACE FUNCTION public.unaccent_immutable(text)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS
$$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN NEW.updated_at = now(); RETURN NEW; END $$;
