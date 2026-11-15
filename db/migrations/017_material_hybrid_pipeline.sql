-- Existing jobs/versions retain their original processing path.
ALTER TABLE material_jobs
  ADD COLUMN phase text NOT NULL DEFAULT 'parse'
    CHECK (phase IN ('preview','parse','images')),
  ADD COLUMN phase_attempts integer NOT NULL DEFAULT 0 CHECK (phase_attempts >= 0),
  ADD COLUMN lease_token text,
  ADD COLUMN metrics jsonb NOT NULL DEFAULT '{}'::jsonb;
UPDATE material_jobs SET phase_attempts=attempts;
CREATE INDEX material_jobs_phase_claim_idx ON material_jobs(phase,available_at,created_at)
  WHERE status IN ('queued','running');

CREATE TABLE material_page_results (
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  document_id text NOT NULL,
  source_hash text NOT NULL,
  page_number integer NOT NULL CHECK (page_number > 0),
  strategy_version text NOT NULL,
  data jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(user_id,document_id,source_hash,page_number,strategy_version),
  FOREIGN KEY(user_id,course_id) REFERENCES courses(user_id,course_id),
  FOREIGN KEY(user_id,document_id) REFERENCES documents(user_id,document_id) ON DELETE CASCADE
);
