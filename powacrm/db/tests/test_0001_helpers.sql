-- unaccent_immutable exists, is immutable, and strips accents
DO $$
DECLARE v text; vol char;
BEGIN
  SELECT provolatile INTO vol FROM pg_proc WHERE proname = 'unaccent_immutable';
  IF vol IS DISTINCT FROM 'i' THEN RAISE EXCEPTION 'unaccent_immutable missing or not IMMUTABLE (got %)', vol; END IF;
  SELECT public.unaccent_immutable('Café Zürich') INTO v;
  IF v <> 'Cafe Zurich' THEN RAISE EXCEPTION 'unaccent failed: %', v; END IF;
END $$;

-- set_updated_at works when attached to a table
DO $$
DECLARE t1 timestamptz; t2 timestamptz;
BEGIN
  CREATE TEMP TABLE _t (id int, updated_at timestamptz NOT NULL DEFAULT now());
  CREATE TRIGGER _t_upd BEFORE UPDATE ON _t FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
  INSERT INTO _t VALUES (1, '2000-01-01');
  UPDATE _t SET id = 2;
  SELECT updated_at INTO t2 FROM _t;
  IF t2 < now() - interval '1 minute' THEN RAISE EXCEPTION 'set_updated_at did not fire'; END IF;
  DROP TABLE _t;
END $$;
SELECT 'test_0001 OK' AS result;
