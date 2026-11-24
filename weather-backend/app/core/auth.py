"""공통: 인증 토큰, 비밀번호, 아이디 검증.

여러 라우터(auth, hiking, admin)가 함께 사용합니다. 수정할 때는 팀에 먼저 공유해 주세요.
"""
import os
import re
import hmac
import secrets
import time
import unicodedata

import bcrypt
from fastapi import HTTPException
from pydantic import BaseModel

from app.core.schema import _ensure_admin_tables, ensure_member_sessions

AUTH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 7


class SignUpRequest(BaseModel):
    username: str
    password: str
    name: str

class LoginRequest(BaseModel):
    username: str
    password: str

def _validate_username(username: str) -> str:
    username = username.strip()
    if len(username) < 4 or len(username) > 80:
        raise HTTPException(status_code=400, detail='아이디는 4~80자로 입력해주세요')
    return username

def _validate_new_username(username: str) -> str:
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9]{4,20}", username) or not re.search(r"[A-Za-z]", username) or not re.search(r"[0-9]", username):
        raise HTTPException(status_code=400, detail="아이디는 영문과 숫자를 모두 포함한 4~20자여야 합니다")
    return username


def _validate_password_username(username: str, password: str):
    normalized_id = unicodedata.normalize('NFKC', username).casefold()
    normalized_password = unicodedata.normalize('NFKC', password).casefold()
    if normalized_id and normalized_id in normalized_password:
        raise HTTPException(status_code=400, detail='비밀번호에 아이디를 포함할 수 없습니다')

MIN_NEW_PASSWORD_LENGTH = 15
COMMON_PASSWORDS = {
    "123456789012345", "1234567890123456", "12345678901234567890",
    "passwordpassword", "passwordpassword1", "password12345678",
    "qwertyuiopasdfgh", "qwertyuiopasdfghjkl", "1q2w3e4r5t6y7u8i",
    "iloveyouiloveyou", "letmeinletmeinletmein",
}


def validate_new_password(username: str, password: str):
    if len(password) < MIN_NEW_PASSWORD_LENGTH or len(password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="새 비밀번호는 15자 이상, UTF-8 기준 72바이트 이하여야 합니다")
    normalized = unicodedata.normalize("NFKC", password).strip().casefold()
    repeated = any(normalized == normalized[:size] * (len(normalized) // size) for size in range(1, 5))
    if not normalized or repeated or normalized in COMMON_PASSWORDS:
        raise HTTPException(status_code=400, detail="흔하거나 반복되는 비밀번호는 사용할 수 없습니다")
    _validate_password_username(username, password)


def _lock_username_changes(cur):
    # Serialize account creation and username changes even when requests race.
    # MySQL releases this connection-scoped lock when the connection closes.
    cur.execute("SELECT GET_LOCK('weather_users_username_v1', 10) AS acquired")
    if cur.fetchone()['acquired'] != 1:
        raise HTTPException(status_code=503, detail='아이디 확인이 지연되고 있습니다. 잠시 후 다시 시도해주세요')

def _auth_signing_key() -> bytes:
    secret = os.getenv("AUTH_SECRET_KEY", "")
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="서버 인증 설정이 완료되지 않았습니다")
    return secret.encode("utf-8")

def _session_digest(authorization: str | None):
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if not re.fullmatch(r"v2_[A-Za-z0-9_-]{43}", token):
        raise HTTPException(status_code=401, detail="다시 로그인해 주세요")
    return hmac.digest(_auth_signing_key(), token.encode("ascii"), "sha256")


def create_member_session(cur, user_id, token_version):
    token = "v2_" + secrets.token_urlsafe(32)
    now = int(time.time())
    cur.execute("DELETE FROM member_sessions WHERE expires_at <= %s ORDER BY expires_at LIMIT 100", (now,))
    cur.execute(
        "INSERT INTO member_sessions (token_digest, user_id, token_version, expires_at) VALUES (%s, %s, %s, %s)",
        (_session_digest("Bearer " + token), user_id, token_version, now + AUTH_TOKEN_TTL_SECONDS),
    )
    return token


def revoke_member_session(cur, authorization):
    digest = _session_digest(authorization)
    ensure_member_sessions(cur)
    cur.execute("DELETE FROM member_sessions WHERE token_digest = %s", (digest,))


def _get_token_user(cur, authorization: str | None):
    digest = _session_digest(authorization)
    _ensure_admin_tables(cur)
    ensure_member_sessions(cur)
    cur.execute("""
        SELECT u.id, u.username, u.name, s.token_version, COALESCE(m.role, 'member') AS role
        FROM member_sessions s
        JOIN users u ON u.id = s.user_id
        LEFT JOIN admin_user_metadata m ON m.user_id = u.id
        WHERE s.token_digest = %s AND s.expires_at > %s
          AND s.token_version = COALESCE(m.token_version, 0) AND COALESCE(m.suspended, 0) = 0
    """, (digest, int(time.time())))
    user = cur.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="로그인 정보가 만료되었거나 계정 상태가 변경되었습니다. 다시 로그인해 주세요")
    return user


def _hash_pw(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > 72:
        raise HTTPException(status_code=400, detail="비밀번호가 너무 깁니다")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")

def _verify_user(cur, username: str, password: str, *, lock=False):
    query = "SELECT * FROM users WHERE username = %s"
    cur.execute(query + (" FOR UPDATE" if lock else ""), (username,))
    user = cur.fetchone()
    if not user or len(password.encode("utf-8")) > 72 or not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다")
    _ensure_admin_tables(cur)
    cur.execute("SELECT suspended FROM admin_user_metadata WHERE user_id = %s", (user['id'],))
    metadata = cur.fetchone()
    if metadata and metadata['suspended']:
        raise HTTPException(status_code=403, detail="이용이 정지된 계정입니다")
    return user


def _store_password_change(cur, user, new_password, name=None):
    new_hash = _hash_pw(new_password)
    if name is None:
        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s AND BINARY password_hash = %s",
                    (new_hash, user["id"], user["password_hash"]))
    else:
        cur.execute("UPDATE users SET name = %s, password_hash = %s WHERE id = %s AND BINARY password_hash = %s",
                    (name, new_hash, user["id"], user["password_hash"]))
    if cur.rowcount != 1:
        raise HTTPException(status_code=409, detail="비밀번호가 이미 변경되었습니다. 현재 비밀번호를 다시 확인해 주세요")
    cur.execute("INSERT INTO admin_user_metadata (user_id, token_version) VALUES (%s,1) ON DUPLICATE KEY UPDATE token_version=token_version+1", (user["id"],))
