CREATE TABLE public.calendar_entries (
  record_key text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
  course_id text,
  kind text NOT NULL CHECK (kind IN ('study', 'exam', 'class', 'other', 'todo')),
  title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 80),
  description text NOT NULL DEFAULT '',
  scheduled_on date,
  all_day boolean NOT NULL,
  start_time time,
  end_time time,
  completed_at timestamptz,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL,
  data jsonb NOT NULL,
  FOREIGN KEY (user_id, course_id) REFERENCES public.courses(user_id, course_id) ON DELETE CASCADE,
  CHECK (kind <> 'exam' OR course_id IS NOT NULL),
  CHECK (kind <> 'other' OR course_id IS NULL),
  CHECK (kind = 'todo' OR scheduled_on IS NOT NULL),
  CHECK (kind = 'todo' OR completed_at IS NULL),
  CHECK ((all_day AND start_time IS NULL AND end_time IS NULL) OR
         (NOT all_day AND scheduled_on IS NOT NULL AND start_time IS NOT NULL
          AND end_time IS NOT NULL AND end_time > start_time))
);

CREATE INDEX calendar_entries_owner_date_idx ON public.calendar_entries(user_id, scheduled_on);
