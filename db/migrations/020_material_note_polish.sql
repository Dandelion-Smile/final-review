CREATE TABLE material_note_polish_jobs (
  job_id text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  document_id text NOT NULL,
  source_hash text NOT NULL,
  snapshot_revision_id text NOT NULL,
  generation integer NOT NULL CHECK (generation > 0),
  purpose text NOT NULL DEFAULT 'initial' CHECK (purpose IN ('initial', 'regenerate')),
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  stage text,
  batches_total integer NOT NULL DEFAULT 0 CHECK (batches_total >= 0),
  batches_done integer NOT NULL DEFAULT 0 CHECK (batches_done >= 0),
  model_config jsonb NOT NULL CHECK (jsonb_typeof(model_config)='object'),
  input_hash text NOT NULL,
  attempts integer NOT NULL DEFAULT 0,
  phase_attempts integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 3,
  lease_token text,
  lease_until timestamptz,
  available_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz,
  error_code text,
  error_message text,
  metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, document_id, generation),
  FOREIGN KEY (user_id, course_id, document_id)
    REFERENCES documents(user_id, course_id, document_id) ON DELETE CASCADE,
  FOREIGN KEY (user_id, course_id, document_id, snapshot_revision_id)
    REFERENCES material_note_revisions(user_id, course_id, document_id, revision_id)
);
CREATE INDEX material_note_polish_jobs_queue_idx
  ON material_note_polish_jobs (status, available_at)
  WHERE status IN ('queued', 'running');

CREATE TABLE material_note_polish_batches (
  user_id uuid NOT NULL,
  job_id text NOT NULL REFERENCES material_note_polish_jobs(job_id) ON DELETE CASCADE,
  batch_index integer NOT NULL CHECK (batch_index >= 0),
  cache_key text NOT NULL,
  block_ids jsonb NOT NULL CHECK (jsonb_typeof(block_ids)='array'),
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'running', 'done', 'failed', 'superseded')),
  result jsonb,
  review jsonb,
  attempts integer NOT NULL DEFAULT 0,
  error text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (job_id, batch_index),
  UNIQUE (user_id, cache_key)
);
CREATE INDEX material_note_polish_batches_cache_idx
  ON material_note_polish_batches (cache_key) WHERE status = 'done';

ALTER TABLE material_note_heads
  ADD COLUMN polish_status text NOT NULL DEFAULT 'none'
    CHECK (polish_status IN
      ('none', 'queued', 'running', 'ready', 'candidate', 'applied', 'failed', 'not_required')),
  ADD COLUMN polish_job_id text,
  ADD COLUMN candidate_revision_id text,
  ADD COLUMN polish_generation integer NOT NULL DEFAULT 0;
ALTER TABLE material_note_heads
  ADD FOREIGN KEY (user_id, course_id, document_id, candidate_revision_id)
    REFERENCES material_note_revisions(user_id, course_id, document_id, revision_id)
    DEFERRABLE INITIALLY DEFERRED;
