ALTER TABLE public.app_users ADD COLUMN deletion_due_at timestamptz;
CREATE INDEX app_users_deletion_due_idx ON public.app_users(deletion_due_at)
  WHERE deletion_due_at IS NOT NULL;

ALTER TABLE public.auth_email_challenges DROP CONSTRAINT auth_email_challenges_purpose_check;
ALTER TABLE public.auth_email_challenges ADD CHECK (purpose IN (
  'register', 'reset_password', 'change_password', 'verify_email',
  'change_email_old', 'change_email_new', 'delete_account', 'cancel_deletion'
));

CREATE TABLE public.user_notifications (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
  event_key text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('quiz_ready', 'quiz_failed', 'grading_ready', 'calendar')),
  title text NOT NULL,
  target jsonb NOT NULL,
  visible_at timestamptz NOT NULL DEFAULT now(),
  read_at timestamptz,
  UNIQUE (user_id, event_key)
);
CREATE INDEX user_notifications_feed_idx ON public.user_notifications(user_id, visible_at DESC, id DESC);
CREATE INDEX user_notifications_unread_idx ON public.user_notifications(user_id, visible_at)
  WHERE read_at IS NULL;

CREATE FUNCTION public.notify_quiz_job() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  state text := NEW.data->>'status';
  notification_kind text;
  notification_title text;
BEGIN
  IF state NOT IN ('succeeded','failed') OR
    (TG_OP='UPDATE' AND OLD.data->>'status'=state
     AND OLD.data->>'attempts' IS NOT DISTINCT FROM NEW.data->>'attempts') THEN
    RETURN NEW;
  END IF;
  IF state='succeeded' THEN
    notification_kind := 'quiz_ready';
    notification_title := '试卷草稿已生成，待确认';
  ELSE
    notification_kind := 'quiz_failed';
    notification_title := '试卷生成失败';
  END IF;
  INSERT INTO public.user_notifications(user_id,event_key,kind,title,target)
  VALUES(NEW.user_id, concat('quiz:',NEW.record_key,':',state,':',NEW.data->>'attempts'),
    notification_kind, notification_title, jsonb_build_object(
      'course_id',NEW.course_id,'conversation_id',NEW.conversation_id,'job_id',NEW.record_key,
      'asset_id',NEW.data #>> '{result,asset_id}','revision_id',NEW.data #>> '{result,revision_id}'))
  ON CONFLICT (user_id,event_key) DO NOTHING;
  RETURN NEW;
END $$;
CREATE TRIGGER quiz_job_notifications AFTER INSERT OR UPDATE OF data ON public.quiz_jobs
  FOR EACH ROW EXECUTE FUNCTION public.notify_quiz_job();

CREATE FUNCTION public.notify_attempt_grading() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.data #>> '{grading_snapshot,model_id}' IS NOT NULL
    AND OLD.data #>> '{grading_snapshot,model_id}' IS NULL THEN
    INSERT INTO public.user_notifications(user_id,event_key,kind,title,target)
    VALUES(NEW.user_id,'grade:' || NEW.record_key,'grading_ready','AI 评分已完成',
      jsonb_build_object('course_id',NEW.course_id,'attempt_id',NEW.record_key))
    ON CONFLICT (user_id,event_key) DO NOTHING;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER attempt_grading_notifications AFTER UPDATE OF data ON public.attempts
  FOR EACH ROW EXECUTE FUNCTION public.notify_attempt_grading();

CREATE FUNCTION public.schedule_calendar_notification() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  reminder_at timestamptz;
BEGIN
  IF TG_OP='DELETE' THEN
    DELETE FROM public.user_notifications WHERE user_id=OLD.user_id
      AND event_key='calendar:' || OLD.record_key;
    RETURN OLD;
  END IF;
  reminder_at := (NEW.scheduled_on + coalesce(NEW.start_time,time '09:00'))
    AT TIME ZONE 'Asia/Shanghai';
  IF reminder_at IS NULL OR NEW.completed_at IS NOT NULL THEN
    DELETE FROM public.user_notifications WHERE user_id=NEW.user_id
      AND event_key='calendar:' || NEW.record_key AND visible_at > now();
  ELSIF reminder_at >= NEW.created_at THEN
    INSERT INTO public.user_notifications(user_id,event_key,kind,title,target,visible_at)
    VALUES(NEW.user_id,'calendar:' || NEW.record_key,'calendar',NEW.title,
      jsonb_build_object('date',NEW.scheduled_on,'course_id',NEW.course_id),reminder_at)
    ON CONFLICT (user_id,event_key) DO UPDATE SET
      title=EXCLUDED.title,target=EXCLUDED.target,visible_at=EXCLUDED.visible_at,
      read_at=CASE WHEN user_notifications.visible_at=EXCLUDED.visible_at
                   THEN user_notifications.read_at END;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER calendar_notifications AFTER INSERT OR UPDATE OR DELETE ON public.calendar_entries
  FOR EACH ROW EXECUTE FUNCTION public.schedule_calendar_notification();

UPDATE public.calendar_entries SET scheduled_on=scheduled_on
  WHERE scheduled_on >= (now() AT TIME ZONE 'Asia/Shanghai')::date AND completed_at IS NULL;

CREATE FUNCTION public.is_account_purge(owner_id uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT current_setting('final_review.purging_account',true)=owner_id::text
    AND NOT EXISTS (SELECT 1 FROM public.app_users WHERE id=owner_id)
$$;

-- Keep individual revision and grade records immutable during normal operations.

CREATE OR REPLACE FUNCTION public.enforce_revision_immutability() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' AND public.is_account_purge(OLD.user_id) THEN
    RETURN OLD;
  END IF;
  IF TG_OP = 'DELETE' AND OLD.state IN ('confirmed','superseded') THEN RAISE EXCEPTION 'confirmed revision history is immutable'; END IF;
  IF TG_OP = 'UPDATE' AND OLD.state IN ('confirmed','superseded') THEN
    IF OLD.asset_id IS DISTINCT FROM NEW.asset_id OR OLD.revision_no IS DISTINCT FROM NEW.revision_no
      OR OLD.content_hash IS DISTINCT FROM NEW.content_hash
      OR (OLD.data - 'state' - 'updated_at' - 'confirmed_at') IS DISTINCT FROM (NEW.data - 'state' - 'updated_at' - 'confirmed_at')
      OR (OLD.state = 'superseded' AND NEW.state IS DISTINCT FROM 'superseded')
      OR (OLD.state = 'confirmed' AND NEW.state NOT IN ('confirmed','superseded')) THEN
      RAISE EXCEPTION 'confirmed revision history is immutable';
    END IF;
    IF OLD.state = 'confirmed' AND NEW.state = 'superseded' AND EXISTS (
      SELECT 1 FROM public.learning_assets a WHERE a.user_id=OLD.user_id AND a.current_revision_id=OLD.revision_id
    ) THEN RAISE EXCEPTION 'asset must point to a replacement before superseding its revision'; END IF;
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$;

CREATE OR REPLACE FUNCTION public.enforce_question_revision_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' AND public.is_account_purge(OLD.user_id) THEN
    RETURN OLD;
  END IF;
  IF TG_OP='UPDATE' AND OLD.question_revision_id IS NOT NULL AND OLD.data IS DISTINCT FROM NEW.data THEN
    RAISE EXCEPTION 'question revision contents are immutable';
  END IF;
  IF TG_OP='DELETE' THEN
    IF EXISTS (SELECT 1 FROM public.quiz_revision_payloads q WHERE q.user_id=OLD.user_id
      AND q.quiz_revision_id=OLD.quiz_revision_id AND q.state IN ('confirmed','superseded')) THEN
      RAISE EXCEPTION 'confirmed question history is immutable';
    END IF;
    RETURN OLD;
  END IF;
  IF NEW.question_revision_id IS NOT NULL AND (
    NEW.question_revision_id IS DISTINCT FROM NEW.record_key OR NOT EXISTS (
      SELECT 1 FROM public.quiz_revision_payloads q WHERE q.user_id=NEW.user_id
        AND q.quiz_revision_id=NEW.quiz_revision_id AND q.course_id=NEW.course_id
    )
  ) THEN RAISE EXCEPTION 'question must belong to its owner paper course'; END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.enforce_quiz_payload_immutability() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' AND public.is_account_purge(OLD.user_id) THEN
    RETURN OLD;
  END IF;
  IF TG_OP='DELETE' AND OLD.state IN ('confirmed','superseded') THEN
    RAISE EXCEPTION 'confirmed quiz history is immutable';
  END IF;
  IF TG_OP='UPDATE' AND OLD.state IN ('confirmed','superseded') AND (
    (OLD.data - 'state' - 'updated_at') IS DISTINCT FROM (NEW.data - 'state' - 'updated_at')
    OR (OLD.state='superseded' AND NEW.state<>'superseded')
    OR (OLD.state='confirmed' AND NEW.state NOT IN ('confirmed','superseded'))
  ) THEN RAISE EXCEPTION 'confirmed quiz history is immutable'; END IF;
  RETURN COALESCE(NEW, OLD);
END $$;

CREATE OR REPLACE FUNCTION public.prevent_question_grade_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' AND public.is_account_purge(OLD.user_id) THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'question grade snapshot is immutable';
END $$;
