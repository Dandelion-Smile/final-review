from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

from .account import AVATAR_LIMIT, AccountService
from .auth import CurrentUser, DatabaseAuth
from .notifications import acknowledge, read_notifications
from .schemas import Credentials

Email = Annotated[str, Field(min_length=3, max_length=320)]
Proof = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{43}$")]
Password = Annotated[str, Field(min_length=8, max_length=128)]
PublicPurpose = Literal["register", "reset_password"]
AccountPurpose = Literal[
    "change_password", "verify_email", "change_email_old", "change_email_new", "delete_account"
]


class PublicCodeRequest(BaseModel):
    purpose: PublicPurpose
    email: Email


class AccountCodeRequest(BaseModel):
    purpose: AccountPurpose
    email: Email | None = None
    old_proof: Proof | None = None


class CodeVerification(BaseModel):
    challenge_id: UUID
    code: Annotated[str, Field(pattern=r"^[0-9]{6}$")]


class PublicCodeVerification(CodeVerification):
    purpose: PublicPurpose


class AccountCodeVerification(CodeVerification):
    purpose: AccountPurpose


class EmailProof(BaseModel):
    proof: Proof


class DeletionConfirmation(EmailProof):
    confirmation: Literal["注销账号"]


class PasswordProof(EmailProof):
    password: Password


class EmailPasswordProof(PasswordProof):
    email: Email


class ChangeEmail(BaseModel):
    old_proof: Proof
    new_proof: Proof


class ChangeNickname(BaseModel):
    nickname: Annotated[str, Field(min_length=1, max_length=40)]


def same_origin_write(request: Request):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    try:
        different_origin = origin and (
            urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower()
        )
    except ValueError:
        different_origin = True
    if request.headers.get("sec-fetch-site") == "cross-site" or different_origin:
        raise HTTPException(403, "请从本站操作")


def account_router(auth: DatabaseAuth, identify, authorize, *, enabled: bool) -> APIRouter:
    router = APIRouter(prefix="/api", dependencies=[Depends(same_origin_write)])
    service = AccountService(auth)

    def database_required():
        if not enabled:
            raise HTTPException(503, "当前服务尚未配置本地认证数据库")

    database = [Depends(database_required)]

    @router.post("/auth/email-code", dependencies=database)
    def send_public_code(data: PublicCodeRequest, request: Request):
        remote_addr = request.client.host if request.client else None
        return service.issue_code(data.purpose, data.email, remote_addr)

    @router.post("/auth/verify-code", dependencies=database)
    def verify_public_code(data: PublicCodeVerification):
        return {"proof": service.verify_code(data.purpose, data.challenge_id, data.code)}

    @router.post("/auth/sign-up", dependencies=database)
    def sign_up(data: EmailPasswordProof):
        user = service.register(data.email, data.password, data.proof)
        return {"user": {"email": user.email}}

    @router.post("/auth/sign-in", dependencies=database)
    def sign_in(data: Credentials, response: Response, request: Request):
        remote_addr = request.client.host if request.client else None
        user, session_id = auth.sign_in(data.email, data.password, remote_addr)
        auth.set_session_cookie(response, session_id)
        return {"user": {"email": user.email}}

    @router.post("/auth/sign-out")
    def sign_out(request: Request, response: Response):
        if enabled:
            auth.revoke(request)
        auth.clear_session_cookie(response)
        return {"signed_out": True}

    @router.post("/auth/reset-password", dependencies=database)
    def reset_password(data: EmailPasswordProof, response: Response):
        service.reset_password(data.email, data.password, data.proof)
        auth.clear_session_cookie(response)
        return {"updated": True}

    @router.get("/auth/me")
    def who_am_i(response: Response, user: CurrentUser = Depends(identify)):
        response.headers["Cache-Control"] = "no-store"
        return service.profile(user)

    @router.patch("/account/profile", dependencies=database)
    def save_profile(data: ChangeNickname, user: CurrentUser = Depends(authorize)):
        service.save_nickname(user, data.nickname)
        return service.profile(user)

    @router.put("/account/avatar", dependencies=database)
    def save_avatar(file: Annotated[UploadFile, File()], user: CurrentUser = Depends(authorize)):
        service.save_avatar(user, file.file.read(AVATAR_LIMIT + 1))
        return service.profile(user)

    @router.get("/account/avatar", dependencies=database)
    def read_avatar(v: UUID, user: CurrentUser = Depends(authorize)):
        return Response(
            service.avatar(user, v), media_type="image/webp",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.post("/account/email-code", dependencies=database)
    def send_account_code(
        data: AccountCodeRequest, request: Request, user: CurrentUser = Depends(authorize),
    ):
        return service.issue_code(
            data.purpose, data.email, request.client.host if request.client else None,
            user=user, old_proof=data.old_proof,
        )

    @router.post("/account/verify-code", dependencies=database)
    def verify_account_code(data: AccountCodeVerification, user: CurrentUser = Depends(authorize)):
        return {"proof": service.verify_code(data.purpose, data.challenge_id, data.code, user)}

    @router.post("/account/verify-email", dependencies=database)
    def verify_email(data: EmailProof, user: CurrentUser = Depends(authorize)):
        service.verify_bound_email(user, data.proof)
        return service.profile(user)

    @router.post("/account/password", dependencies=database)
    def change_password(
        data: PasswordProof, response: Response, user: CurrentUser = Depends(authorize),
    ):
        service.change_password(user, data.password, data.proof)
        auth.clear_session_cookie(response)
        return {"updated": True}

    @router.post("/account/email", dependencies=database)
    def change_email(data: ChangeEmail, response: Response, user: CurrentUser = Depends(authorize)):
        email = service.change_email(user, data.old_proof, data.new_proof)
        auth.clear_session_cookie(response)
        return {"email": email}

    @router.get("/account/deletion", dependencies=database)
    def deletion_impact(user: CurrentUser = Depends(authorize)):
        return service.deletion_impact(user)

    @router.post("/account/deletion", dependencies=database)
    def request_deletion(
        data: DeletionConfirmation, response: Response, user: CurrentUser = Depends(authorize),
    ):
        due_at = service.request_deletion(user, data.proof)
        auth.clear_session_cookie(response)
        return {"deletion_due_at": due_at}

    @router.post("/account/recovery/code", dependencies=database)
    def send_recovery_code(request: Request, user: CurrentUser = Depends(identify)):
        ip = request.client.host if request.client else None
        return service.issue_code("cancel_deletion", None, ip, user=user)

    @router.post("/account/recovery/verify", dependencies=database)
    def verify_recovery_code(data: CodeVerification, user: CurrentUser = Depends(identify)):
        return {"proof": service.verify_code("cancel_deletion", data.challenge_id, data.code, user)}

    @router.post("/account/recovery/cancel", dependencies=database)
    def cancel_deletion(
        data: EmailProof, response: Response, user: CurrentUser = Depends(identify),
    ):
        service.cancel_deletion(user, data.proof)
        auth.clear_session_cookie(response)
        return {"restored": True}

    @router.get("/account/notifications", dependencies=database)
    def notifications(
        response: Response, user: CurrentUser = Depends(authorize),
        limit: int = Query(25, ge=1, le=50),
        before: int | None = Query(None, ge=1, le=9223372036854775807),
    ):
        response.headers["Cache-Control"] = "no-store"
        return read_notifications(auth, user.id, limit, before)

    @router.post("/account/notifications/read-all", dependencies=database)
    def mark_all_read(user: CurrentUser = Depends(authorize)):
        acknowledge(auth, user.id)
        return {"updated": True}

    @router.post("/account/notifications/{notification_id}/read", dependencies=database)
    def mark_read(notification_id: int, user: CurrentUser = Depends(authorize)):
        if not 1 <= notification_id <= 9223372036854775807:
            raise HTTPException(404, "未找到这条通知")
        acknowledge(auth, user.id, notification_id)
        return {"updated": True}

    return router
