import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from io import BytesIO

import psycopg
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from PIL import Image
from test_postgres_migrations import _seed_course, _sql

from final_review.api import create_app
from final_review.config import Settings
from final_review.migrations import apply_migrations

pytestmark = pytest.mark.integration
pytest_plugins = ["test_postgres_migrations"]


@pytest.fixture
def account_clients(database_url, monkeypatch):
    mailbox = {}

    class TestSMTP:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("127.0.0.1", 1025, 10)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def send_message(self, message):
            mailbox[message["To"].addresses[0].addr_spec] = re.search(
                r"[0-9]{6}", message.get_content()
            ).group()

    monkeypatch.setattr("final_review.account.smtplib.SMTP", TestSMTP)
    settings = Settings(
        _env_file=None, database_url=database_url, auth_cookie_secure=False,
        auth_email_code_secret="account-test-key-only-for-testing", smtp_host="127.0.0.1",
        smtp_port=1025, smtp_security="none", smtp_sender="sender@example.test",
    )
    with ExitStack() as stack:
        def start():
            app = create_app(settings)
            return stack.enter_context(TestClient(app)), TestClient(app), mailbox
        yield start


def get_challenge(client, purpose, email=None, old_proof=None):
    path = "/api/auth" if purpose in {"register", "reset_password"} else "/api/account"
    data = {"purpose": purpose, "email": email, "old_proof": old_proof}
    response = client.post(f"{path}/email-code", json=data)
    assert response.status_code == 200, response.text
    return f"{path}/verify-code", {
        "purpose": purpose, "challenge_id": response.json()["challenge_id"],
    }


def verify(client, purpose, email, mailbox, *, old_proof=None):
    target = email if purpose in {"register", "reset_password", "change_email_new"} else None
    path, challenge = get_challenge(client, purpose, target, old_proof)
    response = client.post(path, json={**challenge, "code": mailbox[email]})
    assert response.status_code == 200, response.text
    return response.json()["proof"]


def allow_resend(database_url, email):
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """UPDATE auth_email_challenges SET created_at=created_at - interval '61 seconds'
               WHERE email=%s""", (email,),
        )


def test_registration_profiles_and_security_changes(database_url, account_clients):
    apply_migrations(database_url)
    owner, other, mailbox = account_clients()
    email = "owner@example.test"
    new_email = "changed@example.test"
    other_email = "other@example.test"
    credentials = {"email": email, "password": "secret-password"}
    assert owner.get("/api/auth/me").status_code == 401
    assert owner.post("/api/auth/sign-up", json=credentials).status_code == 422
    assert owner.post(
        "/api/auth/email-code", json={"purpose": "register", "email": email},
        headers={"Origin": "https://unrelated.example.test"},
    ).status_code == 403
    assert owner.post(
        "/api/auth/email-code", json={"purpose": "register", "email": "a@"},
    ).status_code == 422

    path, challenge = get_challenge(owner, "register", email)
    assert owner.post(
        "/api/auth/email-code", json={"purpose": "register", "email": email}
    ).status_code == 429
    code = mailbox[email]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda client: client.post(path, json={**challenge, "code": code}), (owner, other)
        ))
    assert sorted(result.status_code for result in results) == [200, 400]
    proof = next(result.json()["proof"] for result in results if result.is_success)
    assert owner.post("/api/auth/sign-up", json={**credentials, "proof": proof}).status_code == 200
    assert owner.post("/api/auth/sign-up", json={**credentials, "proof": proof}).status_code == 400
    assert owner.get("/api/auth/me").status_code == 401
    assert owner.post("/api/auth/sign-in", json=credentials).status_code == 200
    profile = owner.get("/api/auth/me").json()
    assert profile["email_verified"] is True
    course = owner.post("/api/courses", json={"name": "隔离测试"}).json()

    assert owner.patch("/api/account/profile", json={"nickname": " 复习同学 "}).json()[
        "nickname"
    ] == "复习同学"
    rejected_image = owner.put("/api/account/avatar", files={"file": ("fake.png", b"<svg/>")})
    assert rejected_image.status_code == 422
    buffer = BytesIO()
    Image.new("RGB", (120, 80), "navy").save(buffer, "PNG")
    avatar = owner.put("/api/account/avatar", files={"file": ("avatar.png", buffer.getvalue())})
    assert avatar.status_code == 200, avatar.text
    image = owner.get(avatar.json()["avatar_url"])
    assert image.headers["Content-Type"] == "image/webp"
    assert Image.open(BytesIO(image.content)).size == (256, 256)
    assert other.get(avatar.json()["avatar_url"]).status_code == 401

    other_credentials = {**credentials, "email": other_email}
    other_proof = verify(other, "register", other_email, mailbox)
    assert other.post(
        "/api/auth/sign-up", json={**other_credentials, "proof": other_proof}
    ).is_success
    assert other.post("/api/auth/sign-in", json=other_credentials).is_success
    assert other.get(avatar.json()["avatar_url"]).status_code == 404
    assert other.get(f'/api/courses/{course["course_id"]}/documents').status_code == 404

    allow_resend(database_url, email)
    old_proof = verify(owner, "change_email_old", email, mailbox)
    new_proof = verify(owner, "change_email_new", new_email, mailbox, old_proof=old_proof)
    assert other.post(
        "/api/account/email", json={"old_proof": old_proof, "new_proof": new_proof}
    ).status_code == 400
    before_change = owner.cookies.get("final_review_access")
    assert owner.post(
        "/api/account/email", json={"old_proof": old_proof, "new_proof": new_proof}
    ).json() == {"email": new_email}
    assert owner.get(
        "/api/auth/me", headers={"Cookie": f"final_review_access={before_change}"}
    ).status_code == 401
    assert owner.post("/api/auth/sign-in", json=credentials).status_code == 401
    credentials["email"] = new_email
    assert owner.post("/api/auth/sign-in", json=credentials).is_success
    assert owner.get("/api/auth/me").json()["id"] == profile["id"]
    assert owner.get("/api/courses").json()["items"][0]["course_id"] == course["course_id"]

    allow_resend(database_url, new_email)
    password_proof = verify(owner, "change_password", new_email, mailbox)
    assert owner.post(
        "/api/account/password", json={"password": "changed-password", "proof": password_proof}
    ).is_success
    assert owner.get("/api/auth/me").status_code == 401
    assert owner.post("/api/auth/sign-in", json=credentials).status_code == 401
    credentials["password"] = "changed-password"
    assert owner.post("/api/auth/sign-in", json=credentials).is_success

    allow_resend(database_url, new_email)
    reset_proof = verify(other, "reset_password", new_email, mailbox)
    credentials["password"] = "reset-password"
    assert other.post(
        "/api/auth/reset-password", json={**credentials, "proof": reset_proof}
    ).is_success
    assert owner.get("/api/auth/me").status_code == 401
    assert owner.post("/api/auth/sign-in", json=credentials).is_success
    assert owner.post("/api/auth/sign-out").is_success
    assert owner.get("/api/auth/me").status_code == 401


def test_legacy_login_verification_and_expired_codes(database_url, account_clients):
    # Create the account and learning data before any profile columns exist.
    email, password = "legacy@example.test", "legacy-password"
    with psycopg.connect(database_url) as connection:
        _sql(connection, "001_database_auth.sql")
        _sql(connection, "002_m0_domain_contracts.sql")
        _seed_course(connection)
        connection.execute("UPDATE app_users SET email=%s, password_hash=%s",
                           (email, PasswordHasher().hash(password)))
    assert "013_account_profile.sql" in apply_migrations(database_url)
    assert not apply_migrations(database_url)

    owner, _, mailbox = account_clients()
    assert owner.post("/api/auth/sign-in", json={"email": email, "password": password}).is_success
    assert owner.get("/api/auth/me").json()["email_verified"] is False
    assert owner.get("/api/courses").json()["items"]
    path, challenge = get_challenge(owner, "verify_email")
    code = mailbox[email]
    wrong = "000000" if code != "000000" else "999999"
    for _ in range(5):
        assert owner.post(path, json={**challenge, "code": wrong}).status_code == 400
    assert owner.post(path, json={**challenge, "code": code}).status_code == 400

    allow_resend(database_url, email)
    path, challenge = get_challenge(owner, "verify_email")
    with psycopg.connect(database_url) as connection:
        connection.execute("UPDATE auth_email_challenges SET expires_at=now() WHERE id=%s",
                           (challenge["challenge_id"],))
    assert owner.post(path, json={**challenge, "code": mailbox[email]}).status_code == 400
    allow_resend(database_url, email)
    proof = verify(owner, "verify_email", email, mailbox)
    assert owner.post("/api/account/verify-email", json={"proof": proof}).json()["email_verified"]


def test_notifications_and_deletion_recovery(database_url, account_clients):
    from test_postgres_migrations import sign_in_test_account

    apply_migrations(database_url)
    owner, other, mailbox = account_clients()
    email = "recovery@example.test"
    credentials = {"email": email, "password": "recovery-password"}
    assert sign_in_test_account(owner, database_url, credentials).is_success
    assert sign_in_test_account(
        other, database_url, {**credentials, "email": "unrelated@example.test"}
    ).is_success
    user_id = owner.get("/api/auth/me").json()["id"]
    course_id = owner.post("/api/courses", json={"name": "恢复期保留"}).json()["course_id"]
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """INSERT INTO quiz_jobs(record_key,user_id,course_id,data)
               VALUES('notification-test-job',%s,%s,'{"status":"queued","attempts":1}')""",
            (user_id, course_id),
        )
        connection.execute(
            "UPDATE quiz_jobs SET data=jsonb_set(data,'{status}','\"failed\"'::jsonb)"
        )
        connection.execute("UPDATE quiz_jobs SET data=data")
        connection.execute(
            """INSERT INTO user_notifications(user_id,event_key,kind,title,target,visible_at)
               VALUES(%s,'future','calendar','未到时间','{}',now() + interval '1 day')""",
            (user_id,),
        )
    path = "/api/account/notifications"
    feed = owner.get(path).json()
    assert feed["unread"] == 1
    item = feed["items"][0]
    assert item["kind"] == "quiz_failed"
    assert other.get(path).json()["items"] == []
    assert other.post(f"{path}/{item['id']}/read").status_code == 404
    assert owner.post(f"{path}/{item['id']}/read").is_success
    assert owner.get(path).json()["unread"] == 0
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """INSERT INTO calendar_entries(record_key,user_id,kind,title,scheduled_on,all_day,
                 created_at,updated_at,data)
               VALUES('test-calendar',%s,'study','复习安排',
                 (now() AT TIME ZONE 'Asia/Shanghai')::date - 1,true,
                 now() - interval '2 days',now(),'{}')""",
            (user_id,),
        )
    feed = owner.get(path).json()
    assert feed["unread"] == 1
    assert any(
        item["kind"] == "calendar" and item["title"] == "复习安排" and item["target"]["date"]
        for item in feed["items"]
    )
    assert owner.post(f"{path}/read-all").is_success
    assert owner.get(path).json()["unread"] == 0

    preview = owner.get("/api/account/deletion").json()
    assert (preview["grace_days"], preview["impact"]["courses"]) == (14, 1)
    proof = verify(owner, "delete_account", email, mailbox)
    assert owner.post(
        "/api/account/deletion", json={"proof": proof, "confirmation": "继续"}
    ).status_code == 422
    assert owner.post(
        "/api/account/deletion", json={"proof": proof, "confirmation": "注销账号"}
    ).json()["deletion_due_at"]
    assert owner.get("/api/auth/me").status_code == 401
    assert owner.post("/api/auth/sign-in", json=credentials).is_success
    assert owner.get("/api/auth/me").json()["deletion_due_at"]
    assert owner.get("/api/courses").status_code == 403
    assert owner.get(path).status_code == 403
    assert owner.patch("/api/account/profile", json={"nickname": "blocked"}).status_code == 403
    recovery = "/api/account/recovery"
    assert other.post(f"{recovery}/code").status_code == 403
    code = owner.post(f"{recovery}/code").json()
    proof = owner.post(
        f"{recovery}/verify", json={"challenge_id": code["challenge_id"], "code": mailbox[email]}
    ).json()["proof"]
    assert owner.post(f"{recovery}/cancel", json={"proof": proof}).is_success
    assert owner.get("/api/auth/me").status_code == 401
    assert owner.post("/api/auth/sign-in", json=credentials).is_success
    assert owner.get("/api/auth/me").json()["deletion_due_at"] is None
    assert owner.get("/api/courses").json()["items"][0]["course_id"] == course_id
