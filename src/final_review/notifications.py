"""Read and acknowledge the signed-in account's notification feed."""

from fastapi import HTTPException

from .auth import DatabaseAuth


def read_notifications(auth: DatabaseAuth, user_id: str, limit: int, before: int | None):
    with auth.transaction() as cursor:
        cursor.execute(
            """SELECT n.id,n.kind,n.title,n.target,n.visible_at,n.read_at
               FROM user_notifications n
               WHERE n.user_id=%(owner)s AND n.visible_at<=now()
                 AND (%(before)s::bigint IS NULL OR (n.visible_at,n.id)<(
                   SELECT prior.visible_at,prior.id FROM user_notifications prior
                   WHERE prior.id=%(before)s AND prior.user_id=%(owner)s
                     AND prior.visible_at<=now()))
               ORDER BY n.visible_at DESC,n.id DESC LIMIT %(limit)s""",
            {"owner": user_id, "before": before, "limit": limit + 1},
        )
        items = cursor.fetchall()
        cursor.execute(
            """SELECT count(*) AS unread FROM user_notifications
               WHERE user_id=%s AND visible_at<=now() AND read_at IS NULL""",
            (user_id,),
        )
        return {
            "items": items[:limit], "unread": cursor.fetchone()["unread"],
            "next_before": items[limit - 1]["id"] if len(items) > limit else None,
        }


def acknowledge(auth: DatabaseAuth, user_id: str, notification_id: int | None = None):
    with auth.transaction() as cursor:
        cursor.execute(
            """UPDATE user_notifications SET read_at=coalesce(read_at,now())
               WHERE user_id=%s AND visible_at<=now()
                 AND (%s::bigint IS NULL AND read_at IS NULL OR id=%s)""",
            (user_id, notification_id, notification_id),
        )
        if notification_id is not None and cursor.rowcount == 0:
            raise HTTPException(404, "未找到这条通知")
