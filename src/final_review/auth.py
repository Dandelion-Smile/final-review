"""Database-backed password authentication and opaque browser sessions."""

import ipaddress
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request, Response

from .config import Settings
from .postgres import PostgresStore


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None = None
    is_local: bool = False
    session_hash: str | None = None
    deletion_due_at: datetime | None = None


class DatabaseAuth:
    """Own users and sessions; the browser only ever receives an opaque ID."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.database_url = settings.database_url.get_secret_value()
        self._store: PostgresStore | None = None
        self.passwords = PasswordHasher()

    @property
    def enabled(self) -> bool:
        return self.settings.auth_mode == "database" and bool(self.database_url)

    @property
    def store(self) -> PostgresStore:
        if self._store is None:
            raise HTTPException(503, "认证服务暂时不可用")
        return self._store

    def setup(self):
        if not self.enabled:
            return
        if self._store is None:
            self._store = PostgresStore(self.database_url)
        try:
            with self.store.connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('public.app_users') AS table_name")
                if cursor.fetchone()["table_name"] is None:
                    raise RuntimeError("missing app_users")
                cursor.execute("SELECT to_regclass('public.auth_sessions') AS table_name")
                if cursor.fetchone()["table_name"] is None:
                    raise RuntimeError("missing auth_sessions")
                cursor.execute("SELECT to_regclass('public.auth_email_challenges') AS table_name")
                if cursor.fetchone()["table_name"] is None:
                    raise RuntimeError("missing auth_email_challenges")
        except (psycopg.Error, RuntimeError) as exc:
            self.close()
            raise RuntimeError(
                "认证数据库未初始化；请先运行数据库迁移"
            ) from exc

    def close(self):
        store, self._store = self._store, None
        if store is not None:
            store.close()

    @contextmanager
    def transaction(self):
        try:
            with self.store.transaction(), self.store.connection.cursor() as cursor:
                yield cursor
        except psycopg.errors.UniqueViolation as exc:
            raise HTTPException(409, "该邮箱已绑定其他账户") from exc
        except psycopg.Error as exc:
            raise HTTPException(503, "认证服务暂时不可用") from exc

    @staticmethod
    def _session_hash(session_id: str) -> str:
        return sha256(session_id.encode("ascii")).hexdigest()

    @staticmethod
    def _email(email: str) -> str:
        return email.strip().lower()

    def _new_session(self, cursor, user: CurrentUser) -> str:
        session_id = secrets.token_urlsafe(48)
        expires_at = datetime.now(UTC) + timedelta(days=self.settings.auth_session_days)
        cursor.execute(
            "INSERT INTO auth_sessions (session_hash, user_id, expires_at) VALUES (%s, %s, %s)",
            (self._session_hash(session_id), user.id, expires_at),
        )
        return session_id

    def sign_up(self, email: str, password: str) -> tuple[CurrentUser, str]:
        """Provision a local fixture or maintenance account without email delivery."""
        password_hash = self.passwords.hash(password)
        with self.transaction() as cursor:
            user = self.create_user(cursor, self._email(email), password_hash, verified=False)
            return user, self._new_session(cursor, user)

    @staticmethod
    def create_user(cursor, email: str, password_hash: str, *, verified: bool) -> CurrentUser:
        user = CurrentUser(id=str(uuid4()), email=email)
        cursor.execute(
            """INSERT INTO app_users (id, email, password_hash, email_verified_at)
               VALUES (%s, %s, %s, CASE WHEN %s THEN now() END)""",
            (user.id, user.email, password_hash, verified),
        )
        return user

    def sign_in(
        self, email: str, password: str, remote_addr: str | None
    ) -> tuple[CurrentUser, str]:
        normalized_email = self._email(email)
        remote_addr = self.request_ip(remote_addr)
        with self.transaction() as cursor:
            self.lock_key(cursor, f"login:{normalized_email}")
            cursor.execute(
                """SELECT count(*) AS failures FROM auth_login_attempts
                   WHERE email = %s AND ip_address IS NOT DISTINCT FROM %s
                     AND successful = false AND created_at > now() - interval '15 minutes'""",
                (normalized_email, remote_addr),
            )
            if cursor.fetchone()["failures"] >= 5:
                raise HTTPException(429, "登录尝试过于频繁，请稍后再试")
            cursor.execute(
                """SELECT id, email, password_hash, deletion_due_at FROM app_users
                   WHERE email = %s AND (deletion_due_at IS NULL OR deletion_due_at > now())
                   FOR UPDATE""",
                (normalized_email,),
            )
            row = cursor.fetchone()
            valid = False
            if row:
                try:
                    valid = self.passwords.verify(row["password_hash"], password)
                except (VerifyMismatchError, VerificationError, InvalidHashError):
                    pass
            cursor.execute(
                "INSERT INTO auth_login_attempts (email, ip_address, successful) VALUES (%s,%s,%s)",
                (normalized_email, remote_addr, valid),
            )
            if valid:
                user = CurrentUser(
                    id=str(row["id"]), email=row["email"], deletion_due_at=row["deletion_due_at"]
                )
                return user, self._new_session(cursor, user)
        raise HTTPException(401, "邮箱或密码不正确")

    @staticmethod
    def request_ip(address: str | None) -> str | None:
        try:
            return str(ipaddress.ip_address(address)) if address else None
        except ValueError:
            return None

    @staticmethod
    def lock_key(cursor, key: str):
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))

    def current_user(self, request: Request) -> CurrentUser:
        session_id = request.cookies.get(self.settings.auth_cookie_name)
        if not session_id:
            raise HTTPException(401, "请先登录")
        if not session_id.isascii():
            raise HTTPException(401, "登录已失效，请重新登录")
        session_hash = self._session_hash(session_id)
        try:
            row = self.store.connection.execute(
                """SELECT u.id, u.email, u.deletion_due_at FROM auth_sessions s
                   JOIN app_users u ON u.id = s.user_id
                   WHERE s.session_hash = %s AND s.revoked_at IS NULL AND s.expires_at > now()
                     AND (u.deletion_due_at IS NULL OR u.deletion_due_at > now())""",
                (session_hash,),
            ).fetchone()
        except psycopg.Error as exc:
            raise HTTPException(503, "认证服务暂时不可用") from exc
        if row is None:
            raise HTTPException(401, "登录已失效，请重新登录")
        return CurrentUser(
            id=str(row["id"]), email=row["email"], session_hash=session_hash,
            deletion_due_at=row["deletion_due_at"],
        )

    def revoke(self, request: Request):
        session_id = request.cookies.get(self.settings.auth_cookie_name)
        if not session_id or not session_id.isascii():
            return
        with self.transaction() as cursor:
            cursor.execute(
                "UPDATE auth_sessions SET revoked_at = now() "
                "WHERE session_hash = %s AND revoked_at IS NULL",
                (self._session_hash(session_id),),
            )

    def set_session_cookie(self, response: Response, session_id: str):
        response.set_cookie(
            self.settings.auth_cookie_name,
            session_id,
            max_age=self.settings.auth_session_days * 86400,
            httponly=True,
            secure=self.settings.auth_cookie_secure,
            samesite="lax",
            domain=self.settings.auth_cookie_domain or None,
            path="/",
        )

    def clear_session_cookie(self, response: Response):
        response.delete_cookie(
            self.settings.auth_cookie_name,
            domain=self.settings.auth_cookie_domain or None,
            path="/",
        )
