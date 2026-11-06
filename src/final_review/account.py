"""Email verification, personal profile, and sensitive account changes."""

import hmac
import secrets
import smtplib
import ssl
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.message import EmailMessage
from hashlib import sha256
from io import BytesIO
from uuid import UUID, uuid4

from fastapi import HTTPException
from PIL import Image, ImageOps

from .auth import CurrentUser, DatabaseAuth

PUBLIC_PURPOSES = {"register", "reset_password"}
AVATAR_LIMIT = 2 * 1024 * 1024
CODE_TTL_MINUTES = 10
RESEND_SECONDS = 60
DELETION_GRACE_DAYS = 14
CODE_LABELS = {
    "register": "注册账号",
    "reset_password": "重置密码",
    "change_password": "修改密码",
    "verify_email": "验证绑定邮箱",
    "change_email_old": "确认更换邮箱",
    "change_email_new": "验证新邮箱",
    "delete_account": "申请注销账号",
    "cancel_deletion": "撤销账号注销",
}


def email_address(value: str) -> str:
    value = value.strip().lower()
    try:
        parsed = Address(addr_spec=value)
        if parsed.addr_spec == value and "." in parsed.domain and value.isascii():
            return value
    except (ValueError, HeaderParseError, IndexError):
        pass
    raise HTTPException(422, "请输入有效的邮箱地址")


def profile_data(user: CurrentUser, row: dict | None = None) -> dict:
    profile = row or {}
    avatar_key = profile.get("avatar_key")
    return {
        "id": user.id,
        "email": profile.get("email", user.email),
        "local": user.is_local,
        "nickname": profile.get("nickname"),
        "avatar_url": f"/api/account/avatar?v={avatar_key}" if avatar_key else None,
        "email_verified": bool(profile.get("email_verified_at")),
        "deletion_due_at": user.deletion_due_at,
    }


def avatar_image(payload: bytes) -> bytes:
    if len(payload) > AVATAR_LIMIT:
        raise HTTPException(413, "头像图片不能超过 2 MB")
    try:
        with Image.open(BytesIO(payload), formats=["JPEG", "PNG", "WEBP"]) as image:
            if image.width * image.height > 16_000_000:
                raise HTTPException(422, "图片分辨率过大，请缩小后重试")
            framed = ImageOps.fit(
                ImageOps.exif_transpose(image).convert("RGBA"), (256, 256)
            )
            output = BytesIO()
            framed.save(output, format="WEBP", quality=88)
            return output.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "请上传有效的 JPG、PNG 或 WebP 图片") from exc


class AccountService:
    def __init__(self, auth: DatabaseAuth):
        self.auth = auth
        self.settings = auth.settings

    def _mail_secret(self) -> bytes:
        key = self.settings.auth_email_code_secret.get_secret_value().encode()
        if len(key) < 32:
            raise HTTPException(503, "邮箱验证服务尚未配置")
        return key

    def _mail_settings(self):
        settings = self.settings
        mode = settings.smtp_security
        if (
            not settings.smtp_host or not settings.smtp_sender
            or mode not in {"ssl", "starttls", "none"}
            or (mode == "none" and settings.smtp_host not in {"localhost", "127.0.0.1", "::1"})
        ):
            raise HTTPException(503, "邮箱验证服务尚未配置")
        return settings

    def _send(self, email: str, purpose: str, code: str):
        settings = self._mail_settings()
        context = ssl.create_default_context()
        message = EmailMessage()
        message["From"] = settings.smtp_sender
        message["To"] = email
        message["Subject"] = f"Final Review｜{CODE_LABELS[purpose]}验证码"
        message.set_content(f"验证码：{code}\n\n{CODE_TTL_MINUTES} 分钟内有效，请勿转发。")
        try:
            if settings.smtp_security == "ssl":
                smtp = smtplib.SMTP_SSL(
                    settings.smtp_host, settings.smtp_port, timeout=10, context=context
                )
            else:
                smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
            with smtp:
                if settings.smtp_security == "starttls":
                    smtp.starttls(context=context)
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise HTTPException(503, "验证码邮件发送失败，请稍后重试") from exc

    @staticmethod
    def _locked_user(cursor, user: CurrentUser, *, recovery: bool = False) -> dict:
        cursor.execute(
            """SELECT id,email,deletion_due_at FROM app_users WHERE id=%s
               AND (deletion_due_at IS NULL OR deletion_due_at > now()) FOR UPDATE""",
            (user.id,),
        )
        row = cursor.fetchone()
        cursor.execute(
            """SELECT 1 FROM auth_sessions
               WHERE user_id=%s AND session_hash=%s AND revoked_at IS NULL
                 AND expires_at > now() FOR UPDATE""",
            (user.id, user.session_hash),
        )
        if not row or not cursor.fetchone() or row["email"] != user.email:
            raise HTTPException(401, "登录已失效，请重新登录")
        if bool(row["deletion_due_at"]) != recovery:
            raise HTTPException(403, "当前账户状态无法执行此操作")
        return row

    def _check_send_limit(self, cursor, email: str, ip: str | None, purpose: str):
        self.auth.lock_key(cursor, f"email-code:{email}")
        if ip:
            self.auth.lock_key(cursor, f"email-code-ip:{ip}")
        cursor.execute(
            """SELECT count(*) AS recent,
                 max(created_at) FILTER (WHERE (purpose='cancel_deletion')=%s)
                   > now() - interval '60 seconds' AS hot
               FROM auth_email_challenges
               WHERE email=%s AND created_at > now() - interval '1 hour'""",
            (purpose == "cancel_deletion", email),
        )
        count = cursor.fetchone()
        if count["hot"] or count["recent"] >= 5:
            raise HTTPException(429, "验证码发送过于频繁，请稍后重试")
        if ip:
            cursor.execute(
                """SELECT count(*) AS recent FROM auth_email_challenges
                   WHERE request_ip=%s AND created_at > now() - interval '1 hour'""",
                (ip,),
            )
            if cursor.fetchone()["recent"] >= 30:
                raise HTTPException(429, "验证码发送过于频繁，请稍后重试")

    def issue_code(
        self, purpose: str, email: str | None, ip: str | None, *,
        user: CurrentUser | None = None, old_proof: str | None = None,
    ) -> dict:
        key = self._mail_secret()
        self._mail_settings()
        target = email_address(
            user.email if user and purpose != "change_email_new" else email or ""
        )
        challenge_id = uuid4()
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_digest = hmac.digest(key, f"{challenge_id}:{code}".encode(), "sha256").hex()
        ip = self.auth.request_ip(ip)
        with self.auth.transaction() as cursor:
            if user:
                self._locked_user(cursor, user, recovery=purpose == "cancel_deletion")
            self._check_send_limit(cursor, target, ip, purpose)
            cursor.execute("SELECT id,deletion_due_at FROM app_users WHERE email=%s", (target,))
            existing = cursor.fetchone()
            if purpose == "change_email_new":
                self._proof(cursor, old_proof, "change_email_old", user=user, email=user.email)
                if existing:
                    raise HTTPException(409, "请使用尚未绑定的其他邮箱")
            owner = user.id if user else (
                existing["id"] if existing and purpose == "reset_password" else None
            )
            send = not (
                (purpose == "register" and existing)
                or (purpose == "reset_password" and (not existing or existing["deletion_due_at"]))
            )
            cursor.execute(
                "DELETE FROM auth_email_challenges WHERE created_at < now() - interval '1 day'"
            )
            cursor.execute(
                """UPDATE auth_email_challenges SET consumed_at=now()
                   WHERE email=%s AND purpose=%s AND session_hash IS NOT DISTINCT FROM %s
                     AND proof_digest IS NULL AND consumed_at IS NULL""",
                (target, purpose, user.session_hash if user else None),
            )
            cursor.execute(
                """INSERT INTO auth_email_challenges
                   (id, purpose, email, user_id, session_hash, request_ip, code_digest, expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,now() + %s * interval '1 minute')""",
                (challenge_id, purpose, target, owner, user.session_hash if user else None,
                 ip, code_digest, CODE_TTL_MINUTES),
            )
        if send:
            self._send(target, purpose, code)
        return {"challenge_id": str(challenge_id), "retry_after": RESEND_SECONDS}

    def verify_code(
        self, purpose: str, challenge_id: UUID, code: str, user: CurrentUser | None = None,
    ) -> str:
        key = self._mail_secret()
        code_digest = hmac.digest(key, f"{challenge_id}:{code}".encode(), "sha256").hex()
        proof = secrets.token_urlsafe(32)
        with self.auth.transaction() as cursor:
            if user:
                self._locked_user(cursor, user, recovery=purpose == "cancel_deletion")
            cursor.execute(
                """SELECT code_digest, user_id FROM auth_email_challenges
                   WHERE id=%s AND purpose=%s AND session_hash IS NOT DISTINCT FROM %s
                     AND expires_at > now() AND attempts < 5 AND consumed_at IS NULL
                     AND proof_digest IS NULL FOR UPDATE""",
                (challenge_id, purpose, user.session_hash if user else None),
            )
            row = cursor.fetchone()
            if row and (user is None or str(row["user_id"]) == user.id):
                if hmac.compare_digest(row["code_digest"], code_digest):
                    cursor.execute(
                        """UPDATE auth_email_challenges SET proof_digest=%s,
                           proof_expires_at=now() + interval '15 minutes' WHERE id=%s""",
                        (sha256(proof.encode()).hexdigest(), challenge_id),
                    )
                    return proof
                cursor.execute(
                    "UPDATE auth_email_challenges SET attempts=attempts+1 WHERE id=%s",
                    (challenge_id,),
                )
        raise HTTPException(400, "验证码无效或已过期，请检查后重试或重新发送")

    @staticmethod
    def _proof(
        cursor, proof: str | None, purpose: str, *, user: CurrentUser | None = None,
        email: str | None = None, owner=None, consume: bool = False,
    ) -> dict:
        if not proof:
            raise HTTPException(400, "请先完成邮箱验证")
        digest = sha256(proof.encode()).hexdigest()
        cursor.execute(
            """SELECT id, email FROM auth_email_challenges
               WHERE proof_digest=%s AND purpose=%s AND user_id IS NOT DISTINCT FROM %s
                 AND session_hash IS NOT DISTINCT FROM %s
                 AND proof_expires_at > now() AND consumed_at IS NULL FOR UPDATE""",
            (digest, purpose, user.id if user else owner, user.session_hash if user else None),
        )
        row = cursor.fetchone()
        if not row or (email is not None and row["email"] != email):
            raise HTTPException(400, "邮箱验证已失效，请重新获取验证码")
        if consume:
            cursor.execute(
                "UPDATE auth_email_challenges SET consumed_at=now() WHERE id=%s", (row["id"],)
            )
        return row

    def register(self, email: str, password: str, proof: str) -> CurrentUser:
        email = email_address(email)
        with self.auth.transaction() as cursor:
            self._proof(cursor, proof, "register", email=email, consume=True)
            password_hash = self.auth.passwords.hash(password)
            return self.auth.create_user(cursor, email, password_hash, verified=True)

    @staticmethod
    def _revoke_account_access(cursor, user_id):
        cursor.execute(
            "UPDATE auth_sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL",
            (user_id,),
        )
        cursor.execute(
            """UPDATE auth_email_challenges SET consumed_at=now()
               WHERE user_id=%s AND consumed_at IS NULL""",
            (user_id,),
        )

    def reset_password(self, email: str, password: str, proof: str):
        email = email_address(email)
        with self.auth.transaction() as cursor:
            cursor.execute(
                "SELECT id FROM app_users WHERE email=%s AND deletion_due_at IS NULL FOR UPDATE",
                (email,),
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(400, "邮箱验证已失效，请重新获取验证码")
            self._proof(cursor, proof, "reset_password", email=email, owner=row["id"], consume=True)
            self._save_password(cursor, row["id"], self.auth.passwords.hash(password))

    def _save_password(self, cursor, user_id, password_hash: str):
        cursor.execute(
            """UPDATE app_users SET password_hash=%s,
               email_verified_at=coalesce(email_verified_at,now()) WHERE id=%s""",
            (password_hash, user_id),
        )
        self._revoke_account_access(cursor, user_id)

    def change_password(self, user: CurrentUser, password: str, proof: str):
        password_hash = self.auth.passwords.hash(password)
        with self.auth.transaction() as cursor:
            self._locked_user(cursor, user)
            self._proof(cursor, proof, "change_password", user=user, email=user.email, consume=True)
            self._save_password(cursor, user.id, password_hash)

    def change_email(self, user: CurrentUser, old_proof: str, new_proof: str) -> str:
        with self.auth.transaction() as cursor:
            self._locked_user(cursor, user)
            self._proof(
                cursor, old_proof, "change_email_old", user=user, email=user.email, consume=True
            )
            new_address = self._proof(
                cursor, new_proof, "change_email_new", user=user, consume=True
            )
            email = new_address["email"]
            cursor.execute(
                "UPDATE app_users SET email=%s, email_verified_at=now() WHERE id=%s",
                (email, user.id),
            )
            self._revoke_account_access(cursor, user.id)
            return email

    def verify_bound_email(self, user: CurrentUser, proof: str):
        with self.auth.transaction() as cursor:
            self._locked_user(cursor, user)
            self._proof(cursor, proof, "verify_email", user=user, email=user.email, consume=True)
            cursor.execute(
                "UPDATE app_users SET email_verified_at=coalesce(email_verified_at,now()) "
                "WHERE id=%s",
                (user.id,),
            )

    def profile(self, user: CurrentUser) -> dict:
        if user.is_local or user.deletion_due_at:
            return profile_data(user)
        with self.auth.transaction() as cursor:
            cursor.execute(
                "SELECT email, nickname, avatar_key, email_verified_at FROM app_users WHERE id=%s",
                (user.id,),
            )
            return profile_data(user, cursor.fetchone())

    def deletion_impact(self, user: CurrentUser) -> dict:
        with self.auth.transaction() as cursor:
            self._locked_user(cursor, user)
            cursor.execute(
                """SELECT (SELECT count(*) FROM courses WHERE user_id=%(owner)s) AS courses,
                   (SELECT count(*) FROM documents WHERE user_id=%(owner)s) AS materials,
                   (SELECT count(*) FROM learning_assets WHERE user_id=%(owner)s
                      AND data->>'asset_type'='note') AS notes,
                   (SELECT count(*) FROM learning_assets WHERE user_id=%(owner)s
                      AND data->>'asset_type'='quiz') AS quizzes,
                   (SELECT count(*) FROM attempts WHERE user_id=%(owner)s) AS attempts""",
                {"owner": user.id},
            )
            return {"grace_days": DELETION_GRACE_DAYS, "impact": cursor.fetchone()}

    def request_deletion(self, user: CurrentUser, proof: str):
        with self.auth.transaction() as cursor:
            self._locked_user(cursor, user)
            self._proof(cursor, proof, "delete_account", user=user, email=user.email, consume=True)
            cursor.execute(
                """UPDATE app_users SET deletion_due_at=now() + %s * interval '1 day'
                   WHERE id=%s RETURNING deletion_due_at""",
                (DELETION_GRACE_DAYS, user.id),
            )
            due_at = cursor.fetchone()["deletion_due_at"]
            self._revoke_account_access(cursor, user.id)
            return due_at

    def cancel_deletion(self, user: CurrentUser, proof: str):
        with self.auth.transaction() as cursor:
            self._locked_user(cursor, user, recovery=True)
            self._proof(
                cursor, proof, "cancel_deletion", user=user, email=user.email, consume=True
            )
            cursor.execute("UPDATE app_users SET deletion_due_at=NULL WHERE id=%s", (user.id,))
            self._revoke_account_access(cursor, user.id)

    def save_nickname(self, user: CurrentUser, nickname: str):
        nickname = nickname.strip()
        if not nickname or any(ord(character) < 32 for character in nickname):
            raise HTTPException(422, "请输入 1–40 个字符的昵称")
        with self.auth.transaction() as cursor:
            self._locked_user(cursor, user)
            cursor.execute("UPDATE app_users SET nickname=%s WHERE id=%s", (nickname, user.id))

    def save_avatar(self, user: CurrentUser, payload: bytes):
        image = avatar_image(payload)
        with self.auth.transaction() as cursor:
            self._locked_user(cursor, user)
            cursor.execute(
                "UPDATE app_users SET avatar_key=%s, avatar_image=%s WHERE id=%s",
                (uuid4(), image, user.id),
            )

    def avatar(self, user: CurrentUser, version: UUID) -> bytes:
        with self.auth.transaction() as cursor:
            cursor.execute(
                "SELECT avatar_image FROM app_users WHERE id=%s AND avatar_key=%s",
                (user.id, version),
            )
            row = cursor.fetchone()
            if not row or not row["avatar_image"]:
                raise HTTPException(404, "尚未上传头像")
            return row["avatar_image"]
