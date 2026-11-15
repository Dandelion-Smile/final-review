ALTER TABLE material_jobs
  ADD COLUMN purpose text,
  ADD COLUMN source_hash text,
  ADD COLUMN parse_strategy text,
  ADD COLUMN target_revision_id text,
  ADD COLUMN text_generation integer,
  ADD COLUMN input_hash text,
  ADD COLUMN target_pages jsonb,
  ADD COLUMN internal_key text,
  ADD FOREIGN KEY(user_id,course_id,document_id)
    REFERENCES documents(user_id,course_id,document_id) ON DELETE CASCADE,
  ADD FOREIGN KEY(user_id,course_id,document_id,target_revision_id)
    REFERENCES material_note_revisions(user_id,course_id,document_id,revision_id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX material_jobs_internal_key_idx
  ON material_jobs(user_id,document_id,internal_key) WHERE internal_key IS NOT NULL;
