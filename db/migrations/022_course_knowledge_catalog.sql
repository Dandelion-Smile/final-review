-- Independent course catalogs; no writes or schema changes to knowledge assets.
CREATE TABLE course_knowledge_catalog_heads (
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  current_version_id uuid,
  revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(user_id,course_id),
  FOREIGN KEY(user_id,course_id) REFERENCES courses(user_id,course_id) ON DELETE CASCADE
);

CREATE TABLE course_knowledge_catalog_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  parent_version_id uuid,
  build_mode text NOT NULL CHECK(build_mode IN ('initial','incremental')),
  knowledge_snapshot_hash text NOT NULL CHECK(length(trim(knowledge_snapshot_hash))>0),
  publication_hash text NOT NULL CHECK(length(trim(publication_hash))>0),
  rules_version text NOT NULL CHECK(length(trim(rules_version))>0),
  model_config jsonb NOT NULL CHECK(jsonb_typeof(model_config)='object'),
  chapter_count integer NOT NULL CHECK(chapter_count>=0),
  point_count integer NOT NULL CHECK(point_count>=0),
  created_at timestamptz NOT NULL DEFAULT now(),
  -- A transaction seal also prevents adding rows to historical versions.
  publication_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
  UNIQUE(user_id,course_id,id),
  CHECK(parent_version_id IS DISTINCT FROM id),
  CHECK((build_mode='initial') = (parent_version_id IS NULL)),
  FOREIGN KEY(user_id,course_id) REFERENCES courses(user_id,course_id) ON DELETE CASCADE,
  FOREIGN KEY(user_id,course_id,parent_version_id)
    REFERENCES course_knowledge_catalog_versions(user_id,course_id,id)
    DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE course_knowledge_catalog_heads ADD CONSTRAINT catalog_head_version_fk
  FOREIGN KEY(user_id,course_id,current_version_id)
  REFERENCES course_knowledge_catalog_versions(user_id,course_id,id)
  DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX catalog_versions_course_idx
  ON course_knowledge_catalog_versions(user_id,course_id,created_at DESC);

CREATE TABLE course_knowledge_catalog_chapters (
  version_id uuid NOT NULL,
  chapter_id uuid NOT NULL,
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  title text NOT NULL CHECK(length(trim(title))>0),
  normalized_title text NOT NULL CHECK(length(trim(normalized_title))>0),
  description text NOT NULL DEFAULT '',
  sort_order integer NOT NULL CHECK(sort_order>=0),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(version_id,chapter_id),
  UNIQUE(user_id,course_id,version_id,chapter_id),
  UNIQUE(version_id,normalized_title),
  UNIQUE(version_id,sort_order),
  FOREIGN KEY(user_id,course_id) REFERENCES courses(user_id,course_id) ON DELETE CASCADE,
  FOREIGN KEY(user_id,course_id,version_id)
    REFERENCES course_knowledge_catalog_versions(user_id,course_id,id) ON DELETE CASCADE
);

CREATE TABLE course_knowledge_catalog_assignments (
  version_id uuid NOT NULL,
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  point_id uuid NOT NULL,
  chapter_id uuid,
  classification_input_hash text NOT NULL CHECK(length(trim(classification_input_hash))>0),
  assignment_method text NOT NULL CHECK(assignment_method IN ('llm','carried','unclassified')),
  reason text NOT NULL DEFAULT '',
  sort_order integer NOT NULL CHECK(sort_order>=0),
  PRIMARY KEY(version_id,point_id),
  CHECK((chapter_id IS NULL) = (assignment_method='unclassified')),
  FOREIGN KEY(user_id,course_id) REFERENCES courses(user_id,course_id) ON DELETE CASCADE,
  FOREIGN KEY(user_id,course_id,version_id)
    REFERENCES course_knowledge_catalog_versions(user_id,course_id,id) ON DELETE CASCADE,
  FOREIGN KEY(user_id,course_id,version_id,chapter_id)
    REFERENCES course_knowledge_catalog_chapters(user_id,course_id,version_id,chapter_id),
  FOREIGN KEY(user_id,course_id,point_id) REFERENCES knowledge_points(user_id,course_id,id)
    DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX catalog_assignments_chapter_idx
  ON course_knowledge_catalog_assignments(user_id,course_id,version_id,chapter_id,sort_order);
CREATE INDEX catalog_assignments_point_idx
  ON course_knowledge_catalog_assignments(user_id,course_id,point_id);

CREATE TABLE course_knowledge_catalog_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  mode text NOT NULL CHECK(mode IN ('initial','incremental')),
  status text NOT NULL DEFAULT 'queued'
    CHECK(status IN ('queued','running','retry_wait','succeeded','failed')),
  stage text,
  base_version_id uuid,
  snapshot_hash text NOT NULL CHECK(length(trim(snapshot_hash))>0),
  attempt integer NOT NULL DEFAULT 0 CHECK(attempt>=0),
  lease_token uuid,
  lease_until timestamptz,
  next_run_at timestamptz NOT NULL DEFAULT now(),
  checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(checkpoint)='object'),
  error_code text,
  error_message text,
  result_version_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT(user_id,course_id,mode,base_version_id,snapshot_hash),
  CHECK((mode='initial') = (base_version_id IS NULL)),
  CHECK((status='running') = (lease_token IS NOT NULL AND lease_until IS NOT NULL)),
  CHECK((lease_token IS NULL) = (lease_until IS NULL)),
  CHECK((status='succeeded') = (result_version_id IS NOT NULL)),
  FOREIGN KEY(user_id,course_id) REFERENCES courses(user_id,course_id) ON DELETE CASCADE,
  FOREIGN KEY(user_id,course_id,base_version_id)
    REFERENCES course_knowledge_catalog_versions(user_id,course_id,id)
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(user_id,course_id,result_version_id)
    REFERENCES course_knowledge_catalog_versions(user_id,course_id,id)
    DEFERRABLE INITIALLY DEFERRED
);
CREATE UNIQUE INDEX catalog_jobs_one_live_idx ON course_knowledge_catalog_jobs(user_id,course_id)
  WHERE status IN ('queued','running','retry_wait');
CREATE INDEX catalog_jobs_claim_idx ON course_knowledge_catalog_jobs(next_run_at,created_at,id)
  WHERE status IN ('queued','retry_wait');
CREATE INDEX catalog_jobs_expired_idx ON course_knowledge_catalog_jobs(lease_until,id)
  WHERE status='running';
CREATE INDEX catalog_jobs_course_idx
  ON course_knowledge_catalog_jobs(user_id,course_id,created_at DESC);

CREATE FUNCTION prevent_catalog_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- Permit existing account purge and physical course cascade, including history.
  IF TG_OP='DELETE' AND (NOT EXISTS(SELECT 1 FROM app_users WHERE id=OLD.user_id)
    OR NOT EXISTS(SELECT 1 FROM courses WHERE user_id=OLD.user_id AND course_id=OLD.course_id))
  THEN RETURN OLD; END IF;
  RAISE EXCEPTION 'published catalog history is immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER catalog_versions_immutable BEFORE UPDATE OR DELETE
  ON course_knowledge_catalog_versions FOR EACH ROW EXECUTE FUNCTION prevent_catalog_mutation();
CREATE TRIGGER catalog_chapters_immutable BEFORE UPDATE OR DELETE
  ON course_knowledge_catalog_chapters FOR EACH ROW EXECUTE FUNCTION prevent_catalog_mutation();
CREATE TRIGGER catalog_assignments_immutable BEFORE UPDATE OR DELETE
  ON course_knowledge_catalog_assignments FOR EACH ROW EXECUTE FUNCTION prevent_catalog_mutation();

CREATE FUNCTION check_catalog_insert_transaction() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS(SELECT 1 FROM course_knowledge_catalog_versions
    WHERE id=NEW.version_id AND publication_xid=pg_current_xact_id()) THEN
    RAISE EXCEPTION 'cannot append to published catalog history' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER catalog_chapters_sealed BEFORE INSERT ON course_knowledge_catalog_chapters
  FOR EACH ROW EXECUTE FUNCTION check_catalog_insert_transaction();
CREATE TRIGGER catalog_assignments_sealed BEFORE INSERT ON course_knowledge_catalog_assignments
  FOR EACH ROW EXECUTE FUNCTION check_catalog_insert_transaction();
