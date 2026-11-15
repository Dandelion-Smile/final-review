-- Additive course knowledge pipeline. Legacy knowledge_points payloads are retained.
ALTER TABLE documents ADD CONSTRAINT documents_knowledge_scope_key
  UNIQUE(user_id,course_id,document_id);
ALTER TABLE material_versions ADD CONSTRAINT versions_knowledge_scope_key
  UNIQUE(user_id,course_id,document_id,material_version_id);
ALTER TABLE material_locators ADD CONSTRAINT locators_knowledge_scope_key
  UNIQUE(user_id,course_id,material_version_id,locator_id);

ALTER TABLE knowledge_points
  ADD COLUMN id uuid NOT NULL DEFAULT gen_random_uuid(),
  ADD COLUMN name text,
  ADD COLUMN normalized_name text,
  ADD COLUMN description text,
  ADD COLUMN parent_id uuid,
  ADD COLUMN embedding vector,
  ADD COLUMN importance_score double precision CHECK(importance_score BETWEEN 0 AND 1),
  ADD COLUMN status text NOT NULL DEFAULT 'legacy'
    CHECK(status IN ('legacy','active','inactive')),
  ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
  ADD CONSTRAINT knowledge_points_id_key UNIQUE(id),
  ADD CONSTRAINT points_knowledge_scope_key UNIQUE(user_id,course_id,id),
  ADD CONSTRAINT points_knowledge_course_fk FOREIGN KEY(user_id,course_id)
    REFERENCES courses(user_id,course_id),
  ADD CONSTRAINT points_knowledge_parent_fk FOREIGN KEY(user_id,course_id,parent_id)
    REFERENCES knowledge_points(user_id,course_id,id),
  ADD CONSTRAINT points_knowledge_no_self_parent CHECK(parent_id IS DISTINCT FROM id),
  ADD CONSTRAINT points_knowledge_active_check CHECK(status <> 'active' OR
    (length(trim(name))>0 AND length(trim(normalized_name))>0
     AND length(trim(description))>0 AND embedding IS NOT NULL
     AND name IS NOT NULL AND normalized_name IS NOT NULL AND description IS NOT NULL));
CREATE INDEX points_knowledge_name_idx ON knowledge_points(user_id,course_id,normalized_name)
  WHERE status='active';
CREATE INDEX points_knowledge_parent_idx ON knowledge_points(user_id,course_id,parent_id);

CREATE TABLE knowledge_build_jobs (
  job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL, document_id text NOT NULL, material_version_id text NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed')),
  stage text CHECK(stage IN ('splitting','extracting','verifying','dedup_internal','matching','merging')),
  attempts int NOT NULL DEFAULT 0 CHECK(attempts>=0),
  max_attempts int NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10),
  available_at timestamptz NOT NULL DEFAULT now(), lease_until timestamptz,
  extractor_version text NOT NULL, config_json jsonb NOT NULL,
  error_code text, error_message text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz, finished_at timestamptz,
  UNIQUE(user_id,material_version_id,extractor_version),
  UNIQUE(user_id,course_id,job_id),
  FOREIGN KEY(user_id,course_id) REFERENCES courses(user_id,course_id),
  FOREIGN KEY(user_id,course_id,document_id) REFERENCES documents(user_id,course_id,document_id),
  FOREIGN KEY(user_id,course_id,document_id,material_version_id)
    REFERENCES material_versions(user_id,course_id,document_id,material_version_id)
);
CREATE INDEX knowledge_jobs_claim_idx ON knowledge_build_jobs(available_at,created_at)
  WHERE status IN ('queued','running');
CREATE INDEX knowledge_jobs_course_idx ON knowledge_build_jobs(user_id,course_id,created_at);

CREATE TABLE material_extraction_blocks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL, build_job_id uuid NOT NULL,
  block_index int NOT NULL CHECK(block_index>=0), title text NOT NULL, content text NOT NULL,
  page_start int, page_end int, source_locator_ids jsonb NOT NULL,
  content_hash text NOT NULL,
  extract_status text NOT NULL DEFAULT 'pending'
    CHECK(extract_status IN ('pending','succeeded','failed')),
  error_message text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK(jsonb_typeof(source_locator_ids)='array'),
  CHECK(page_start IS NULL OR (page_start>0 AND page_end>=page_start)),
  UNIQUE(build_job_id,block_index), UNIQUE(user_id,course_id,build_job_id,id),
  FOREIGN KEY(user_id,course_id) REFERENCES courses(user_id,course_id),
  FOREIGN KEY(user_id,course_id,build_job_id)
    REFERENCES knowledge_build_jobs(user_id,course_id,job_id)
);
CREATE INDEX knowledge_blocks_status_idx ON material_extraction_blocks(build_job_id,extract_status);

CREATE TABLE knowledge_candidates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL, build_job_id uuid NOT NULL, block_id uuid NOT NULL,
  candidate_index int NOT NULL CHECK(candidate_index>=0),
  name text NOT NULL CHECK(length(trim(name))>0), normalized_name text NOT NULL,
  description text NOT NULL, importance_score double precision CHECK(importance_score BETWEEN 0 AND 1),
  facts_json jsonb NOT NULL, embedding vector,
  status text NOT NULL DEFAULT 'pending' CHECK(status IN
    ('pending','internal_merged','new','merged','review_needed','rejected')),
  matched_candidate_id uuid, matched_point_id uuid,
  match_relation text CHECK(match_relation IN ('equivalent','broader','narrower','related','different')),
  match_method text CHECK(match_method IN ('exact','embedding','llm')),
  match_score double precision, match_reason text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK(jsonb_typeof(facts_json)='object'),
  CHECK(matched_candidate_id IS DISTINCT FROM id),
  UNIQUE(block_id,candidate_index), UNIQUE(user_id,course_id,build_job_id,id),
  FOREIGN KEY(user_id,course_id) REFERENCES courses(user_id,course_id),
  FOREIGN KEY(user_id,course_id,build_job_id,block_id)
    REFERENCES material_extraction_blocks(user_id,course_id,build_job_id,id),
  FOREIGN KEY(user_id,course_id,build_job_id,matched_candidate_id)
    REFERENCES knowledge_candidates(user_id,course_id,build_job_id,id),
  FOREIGN KEY(user_id,course_id,matched_point_id)
    REFERENCES knowledge_points(user_id,course_id,id)
);
CREATE INDEX knowledge_candidates_status_idx ON knowledge_candidates(build_job_id,status);
CREATE INDEX knowledge_candidates_name_idx ON knowledge_candidates(build_job_id,normalized_name);

CREATE TABLE knowledge_facts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL, point_id uuid NOT NULL,
  fact_text text NOT NULL CHECK(length(trim(fact_text))>0),
  normalized_text text NOT NULL, fact_hash text NOT NULL,
  status text NOT NULL CHECK(status IN ('supported','pending','inactive')),
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(user_id,course_id,point_id,fact_hash), UNIQUE(user_id,course_id,id),
  FOREIGN KEY(user_id,course_id) REFERENCES courses(user_id,course_id),
  FOREIGN KEY(user_id,course_id,point_id) REFERENCES knowledge_points(user_id,course_id,id)
);
CREATE INDEX knowledge_facts_status_idx ON knowledge_facts(user_id,course_id,point_id,status);

CREATE TABLE fact_evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL, fact_id uuid NOT NULL,
  material_version_id text NOT NULL, locator_id text NOT NULL,
  quote text NOT NULL CHECK(length(trim(quote))>0), quote_hash text NOT NULL,
  quote_verified boolean NOT NULL DEFAULT false,
  support_status text NOT NULL CHECK(support_status IN ('supported','uncertain','unsupported')),
  verification_method text NOT NULL CHECK(verification_method IN ('rule','llm','manual')),
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK(support_status <> 'supported' OR quote_verified),
  UNIQUE(user_id,course_id,fact_id,material_version_id,locator_id,quote_hash),
  FOREIGN KEY(user_id,course_id) REFERENCES courses(user_id,course_id),
  FOREIGN KEY(user_id,course_id,fact_id) REFERENCES knowledge_facts(user_id,course_id,id),
  FOREIGN KEY(user_id,course_id,material_version_id,locator_id)
    REFERENCES material_locators(user_id,course_id,material_version_id,locator_id)
);
CREATE INDEX fact_evidence_locator_idx ON fact_evidence(user_id,material_version_id,locator_id);
