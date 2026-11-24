"""Verified recovery email and one-time password recovery codes."""
import hmac
import logging
import os
import re
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import (
    _auth_signing_key, _get_token_user, _store_password_change, _verify_user,
    validate_new_password,
)
from app.core.auth_attempts import credential_attempt
from app.core.schema import _ensure_admin_tables, ensure_member_recovery
from app.database import get_conn

router = APIRouter(prefix="/api/auth", tags=["account recovery"])
logger = logging.getLogger(__name__)
CODE_LIFETIME = 900
SEND_COOLDOWN = 60
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


class EmailRequest(BaseModel):
    email: str
    current_password: str


class CodeRequest(BaseModel):
    code: str


class RecoverRequest(BaseModel):
    username: str


class ResetRequest(RecoverRequest, CodeRequest):
    new_password: str


def mail_enabled():
    return bool(os.getenv("RECOVERY_SMTP_HOST") and os.getenv("RECOVERY_SMTP_FROM"))


def require_mail():
    if not mail_enabled():
        raise HTTPException(status_code=503, detail="이메일 계정 복구를 준비 중입니다")


def send_code(email, code, purpose):
    message = EmailMessage()
    message["Subject"] = "Weather+ 이메일 확인" if purpose == "verify" else "Weather+ 비밀번호 재설정"
    message["From"] = os.environ["RECOVERY_SMTP_FROM"]
    message["To"] = email
    message.set_content(f"Weather+의 요청 화면에 다음 일회용 코드를 입력하세요. 15분 뒤 만료됩니다.\n\n{code}\n")
    with smtplib.SMTP_SSL(os.environ["RECOVERY_SMTP_HOST"], int(os.getenv("RECOVERY_SMTP_PORT", "465")),
                          timeout=10, context=ssl.create_default_context()) as smtp:
        if user := os.getenv("RECOVERY_SMTP_USER"):
            smtp.login(user, os.environ["RECOVERY_SMTP_PASSWORD"])
        smtp.send_message(message)


def deliver_code(email, code, purpose):
    try:
        send_code(email, code, purpose)
    except (OSError, ValueError, KeyError):
        logger.warning("Recovery email delivery failed")


def prepare_tables(cur):
    _ensure_admin_tables(cur)
    ensure_member_recovery(cur)


def locked_account(cur, value, by_id=False):
    column = "u.id" if by_id else "u.username"
    cur.execute(f"""
        SELECT u.id, u.username, u.password_hash, COALESCE(m.token_version, 0) AS token_version,
               COALESCE(m.suspended, 0) AS suspended, e.email
        FROM users u
        LEFT JOIN admin_user_metadata m ON m.user_id=u.id
        LEFT JOIN member_recovery_email e ON e.user_id=u.id
        WHERE {column}=%s FOR UPDATE
    """, (value,))
    return cur.fetchone()


def lock_session_account(cur, user):
    account = locked_account(cur, user["id"], by_id=True)
    if not account or account["suspended"] or account["token_version"] != user["token_version"]:
        raise HTTPException(status_code=401, detail="계정 상태가 변경되었습니다. 다시 로그인해 주세요")
    return account


def code_digest(code):
    return hmac.digest(_auth_signing_key(), code.encode("ascii"), "sha256")


def issue_code(cur, account, email, purpose):
    now = int(time.time())
    key = (account["id"], purpose)
    cur.execute("SELECT expires_at FROM member_recovery_codes WHERE user_id=%s AND purpose=%s FOR UPDATE", key)
    previous = cur.fetchone()
    if previous and previous["expires_at"] > now + CODE_LIFETIME - SEND_COOLDOWN:
        return None
    code = secrets.token_urlsafe(32)
    digest = code_digest(code)
    cur.execute("""
        INSERT INTO member_recovery_codes (user_id, purpose, email, code_digest, token_version, expires_at)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE email=%s, code_digest=%s, token_version=%s, expires_at=%s
    """, (*key, email, digest, account["token_version"], now + CODE_LIFETIME,
          email, digest, account["token_version"], now + CODE_LIFETIME))
    return code


def match_code(cur, account, code, purpose):
    code = code.strip()
    if account and not account["suspended"] and re.fullmatch(r"[A-Za-z0-9_-]{43}", code):
        cur.execute("SELECT * FROM member_recovery_codes WHERE user_id=%s AND purpose=%s FOR UPDATE",
                    (account["id"], purpose))
        entry = cur.fetchone()
        if (entry and entry["expires_at"] > int(time.time())
                and entry["token_version"] == account["token_version"]
                and hmac.compare_digest(code_digest(code), entry["code_digest"])):
            return entry
    raise HTTPException(status_code=400, detail="코드가 올바르지 않거나 만료되었습니다")


@router.get("/recovery/status")
def status():
    return {"available": mail_enabled()}


@router.get("/recovery/email")
def email_status(authorization: str | None = Header(default=None)):
    require_mail()
    with get_conn() as conn, conn.cursor() as cur:
        user = _get_token_user(cur, authorization)
        ensure_member_recovery(cur)
        cur.execute("SELECT email FROM member_recovery_email WHERE user_id=%s", (user["id"],))
        row = cur.fetchone()
    return {"email": row["email"] if row else None}


@router.post("/recovery/email")
def request_email(req: EmailRequest, request: Request, tasks: BackgroundTasks,
                  authorization: str | None = Header(default=None)):
    require_mail()
    email = req.email.strip()
    if len(email) > 254 or not EMAIL.fullmatch(email):
        raise HTTPException(status_code=400, detail="이메일 주소를 확인해 주세요")
    with get_conn() as conn, conn.cursor() as cur:
        user = _get_token_user(cur, authorization)
        ensure_member_recovery(cur)
        account = lock_session_account(cur, user)
        with credential_attempt(request, account["username"]):
            _verify_user(cur, account["username"], req.current_password, lock=True)
        code = issue_code(cur, account, email, "verify")
        conn.commit()
    if not code:
        raise HTTPException(status_code=429, detail="코드는 1분에 한 번 요청할 수 있습니다")
    tasks.add_task(deliver_code, email, code, "verify")
    return {"message": "이메일 확인 코드를 요청했습니다"}


@router.post("/recovery/email/verify")
def verify_email(req: CodeRequest, authorization: str | None = Header(default=None)):
    require_mail()
    with get_conn() as conn, conn.cursor() as cur:
        user = _get_token_user(cur, authorization)
        ensure_member_recovery(cur)
        account = lock_session_account(cur, user)
        entry = match_code(cur, account, req.code, "verify")
        email = entry["email"]
        cur.execute("INSERT INTO member_recovery_email (user_id,email) VALUES (%s,%s) ON DUPLICATE KEY UPDATE email=%s",
                    (account["id"], email, email))
        cur.execute("DELETE FROM member_recovery_codes WHERE user_id=%s", (account["id"],))
        conn.commit()
    return {"email": email}


def send_recovery(username):
    # The anonymous response is independent of account existence or delivery.
    try:
        with get_conn() as conn, conn.cursor() as cur:
            prepare_tables(cur)
            account = locked_account(cur, username)
            if not account or account["suspended"] or not account["email"]:
                return
            email = account["email"]
            code = issue_code(cur, account, email, "reset")
            conn.commit()
        if code:
            deliver_code(email, code, "reset")
    except Exception:
        logger.warning("Password recovery request could not be processed")


@router.post("/recovery/request")
def request_recovery(req: RecoverRequest, request: Request, tasks: BackgroundTasks):
    require_mail()
    username = req.username.strip()
    with credential_attempt(request, username[:80], scope="recovery-mail", count_request=True):
        if 1 <= len(username) <= 80:
            tasks.add_task(send_recovery, username)
    return {"message": "등록된 복구 이메일이 있다면 재설정 코드가 전송됩니다"}


@router.post("/recovery/reset")
def reset_with_code(req: ResetRequest):
    require_mail()
    username = req.username.strip()
    with get_conn() as conn, conn.cursor() as cur:
        prepare_tables(cur)
        account = locked_account(cur, username)
        entry = match_code(cur, account, req.code, "reset")
        if entry["email"] != account["email"]:
            raise HTTPException(status_code=400, detail="코드가 올바르지 않거나 만료되었습니다")
        validate_new_password(account["username"], req.new_password)
        _store_password_change(cur, account, req.new_password)
        cur.execute("DELETE FROM member_recovery_codes WHERE user_id=%s", (account["id"],))
        conn.commit()
    return {"message": "비밀번호가 변경되었습니다. 다시 로그인해 주세요"}
