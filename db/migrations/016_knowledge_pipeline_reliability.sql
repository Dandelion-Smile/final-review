ALTER TABLE knowledge_build_jobs DROP CONSTRAINT knowledge_build_jobs_stage_check;
ALTER TABLE knowledge_build_jobs ADD CONSTRAINT knowledge_build_jobs_stage_check
  CHECK(stage IN ('splitting','extracting','normalizing','verifying','embedding',
                  'dedup_internal','matching','merging'));

CREATE TABLE knowledge_match_decisions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  course_id text NOT NULL, build_job_id uuid NOT NULL, candidate_id uuid NOT NULL,
  phase text NOT NULL CHECK(phase IN ('dedup_internal','matching')),
  target_key text NOT NULL, target_candidate_id uuid, target_point_id uuid,
  input_hash text NOT NULL, matcher_version text NOT NULL,
  input_json jsonb NOT NULL, decision_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  attempt_history jsonb NOT NULL DEFAULT '[]'::jsonb,
  status text NOT NULL CHECK(status IN ('succeeded','invalid','review_needed')),
  attempts int NOT NULL DEFAULT 1 CHECK(attempts>0), error_code text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK(NOT(target_candidate_id IS NOT NULL AND target_point_id IS NOT NULL)),
  UNIQUE(build_job_id,candidate_id,phase,target_key,input_hash,matcher_version),
  FOREIGN KEY(user_id,course_id) REFERENCES courses(user_id,course_id),
  FOREIGN KEY(user_id,course_id,build_job_id,candidate_id)
    REFERENCES knowledge_candidates(user_id,course_id,build_job_id,id),
  FOREIGN KEY(user_id,course_id,build_job_id,target_candidate_id)
    REFERENCES knowledge_candidates(user_id,course_id,build_job_id,id),
  FOREIGN KEY(user_id,course_id,target_point_id)
    REFERENCES knowledge_points(user_id,course_id,id)
);
CREATE INDEX knowledge_decisions_candidate_idx
  ON knowledge_match_decisions(user_id,course_id,candidate_id,updated_at DESC);
CREATE INDEX knowledge_decisions_job_idx ON knowledge_match_decisions(build_job_id,status);
