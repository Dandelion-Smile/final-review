"""Permanently remove expired accounts and their application-owned files."""

import logging
import re
import shutil
from pathlib import Path
from uuid import UUID

from anyio import sleep, to_thread

from .config import Settings
from .postgres import PostgresStore

logger = logging.getLogger(__name__)


def _remove_files(settings: Settings, owner: UUID, exports: list[str]):
    uploads = Path(settings.uploads_dir).resolve()
    directory = uploads / str(owner)
    if directory.resolve() != directory or directory.parent != uploads:
        raise ValueError("Account upload directory is redirected")
    if directory.exists():
        shutil.rmtree(directory)
    root = Path(settings.exports_dir).resolve()
    for export_id in exports:
        if not re.fullmatch(r"export-[0-9a-f]+", export_id):
            raise ValueError("Invalid stored export identifier")
        for file in root.glob(f"{export_id}-*"):
            if file.is_file() or file.is_symlink():
                file.unlink()


def _purge_owner(store: PostgresStore, settings: Settings, owner: UUID) -> bool:
    with store.transaction(), store.connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS acquired",
            (f"account-files:{owner}",),
        )
        if not cursor.fetchone()["acquired"]:
            return False
        cursor.execute(
            "SELECT email FROM app_users WHERE id=%s AND deletion_due_at<=now() FOR UPDATE",
            (owner,),
        )
        row = cursor.fetchone()
        if row is None:
            return False
        cursor.execute("SELECT record_key FROM export_jobs WHERE user_id=%s", (owner,))
        exports = [item["record_key"] for item in cursor.fetchall()]
        cursor.execute("DELETE FROM auth_login_attempts WHERE email=%s", (row["email"],))
        cursor.execute("DELETE FROM auth_email_challenges WHERE email=%s", (row["email"],))
        cursor.execute("SELECT set_config('final_review.purging_account',%s,true)", (str(owner),))
        cursor.execute("DELETE FROM app_users WHERE id=%s", (owner,))
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        _remove_files(settings, owner, exports)
        return True


def purge_expired_accounts(store: PostgresStore, settings: Settings) -> int:
    rows = store.connection.execute(
        """SELECT id FROM app_users WHERE deletion_due_at<=now()
           ORDER BY deletion_due_at,id LIMIT 100"""
    ).fetchall()
    count = 0
    for row in rows:
        try:
            count += _purge_owner(store, settings, row["id"])
        except Exception:
            logger.exception("账号到期清理失败，将自动重试，账号 ID %s", row["id"])
    return count


async def run_account_purge(store: PostgresStore, settings: Settings):
    while True:
        try:
            await to_thread.run_sync(purge_expired_accounts, store, settings)
        except Exception:
            logger.exception("账号清理任务暂时不可用，将自动重试")
        await sleep(60)
