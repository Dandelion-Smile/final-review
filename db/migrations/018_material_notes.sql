CREATE TABLE material_note_heads (
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL,
  document_id text NOT NULL,
  latest_saved_revision_id text NOT NULL,
  initial_revision_id text NOT NULL,
  indexed_revision_id text,
  knowledge_revision_id text,
  save_sequence integer NOT NULL CHECK (save_sequence > 0),
  text_generation integer NOT NULL DEFAULT 1 CHECK (text_generation > 0),
  processing_generation integer NOT NULL DEFAULT 1 CHECK (processing_generation > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(user_id,course_id,document_id),
  FOREIGN KEY(user_id,course_id,document_id)
    REFERENCES documents(user_id,course_id,document_id) ON DELETE CASCADE
);
CREATE TABLE material_note_revisions (
  user_id uuid NOT NULL,
  course_id text NOT NULL,
  document_id text NOT NULL,
  revision_id text NOT NULL,
  revision_no integer NOT NULL CHECK (revision_no > 0),
  parent_revision_id text,
  schema_version integer NOT NULL DEFAULT 1 CHECK (schema_version = 1),
  body jsonb NOT NULL CHECK (jsonb_typeof(body)='object'),
  input_hash text NOT NULL,
  edited_by uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE CHECK (edited_by=user_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(user_id,course_id,document_id,revision_id),
  UNIQUE(user_id,course_id,document_id,revision_no),
  FOREIGN KEY(user_id,course_id,document_id)
    REFERENCES documents(user_id,course_id,document_id) ON DELETE CASCADE,
  FOREIGN KEY(user_id,course_id,document_id,parent_revision_id)
    REFERENCES material_note_revisions(user_id,course_id,document_id,revision_id)
    DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE material_note_heads
  ADD FOREIGN KEY(user_id,course_id,document_id,latest_saved_revision_id)
    REFERENCES material_note_revisions(user_id,course_id,document_id,revision_id)
    DEFERRABLE INITIALLY DEFERRED,
  ADD FOREIGN KEY(user_id,course_id,document_id,initial_revision_id)
    REFERENCES material_note_revisions(user_id,course_id,document_id,revision_id)
    DEFERRABLE INITIALLY DEFERRED,
  ADD FOREIGN KEY(user_id,course_id,document_id,indexed_revision_id)
    REFERENCES material_note_revisions(user_id,course_id,document_id,revision_id)
    DEFERRABLE INITIALLY DEFERRED,
  ADD FOREIGN KEY(user_id,course_id,document_id,knowledge_revision_id)
    REFERENCES material_note_revisions(user_id,course_id,document_id,revision_id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE material_note_save_requests (
  user_id uuid NOT NULL,
  course_id text NOT NULL,
  document_id text NOT NULL,
  idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 200),
  request_hash text NOT NULL,
  revision_id text NOT NULL,
  sync_status text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(user_id,course_id,document_id,idempotency_key),
  FOREIGN KEY(user_id,course_id,document_id,revision_id)
    REFERENCES material_note_revisions(user_id,course_id,document_id,revision_id)
    ON DELETE CASCADE
);
CREATE FUNCTION enforce_material_note_immutability() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' AND NOT EXISTS (
    SELECT 1 FROM documents WHERE user_id=OLD.user_id AND document_id=OLD.document_id
  ) THEN RETURN OLD; END IF;
  RAISE EXCEPTION 'material note revisions are immutable';
END $$;
CREATE TRIGGER material_note_revision_immutability
  BEFORE UPDATE OR DELETE ON material_note_revisions
  FOR EACH ROW EXECUTE FUNCTION enforce_material_note_immutability();
