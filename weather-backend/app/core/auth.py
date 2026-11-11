"""공통: 인증 토큰, 비밀번호, 아이디 검증.

여러 라우터(auth, hiking, admin)가 함께 사용합니다. 수정할 때는 팀에 먼저 공유해 주세요.
"""
import os
import hmac
import base64
import json
import time
import unicodedata

import bcrypt
from fastapi import HTTPException
from pydantic import BaseModel

from app.core.schema import _ensure_admin_tables

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

def _validate_password_username(username: str, password: str):
    normalized_id = unicodedata.normalize('NFKC', username).casefold()
    normalized_password = unicodedata.normalize('NFKC', password).casefold()
    if normalized_id and normalized_id in normalized_password:
        raise HTTPException(status_code=400, detail='비밀번호에 아이디를 포함할 수 없습니다')

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

def _encode_token_part(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

def _create_access_token(username: str, version: int = 0) -> str:
    payload = json.dumps({"sub": username, "ver": version, "exp": int(time.time()) + AUTH_TOKEN_TTL_SECONDS}, separators=(",", ":")).encode("utf-8")
    encoded_payload = _encode_token_part(payload)
    signature = hmac.new(_auth_signing_key(), encoded_payload.encode("ascii"), "sha256").digest()
    return f"{encoded_payload}.{_encode_token_part(signature)}"

def _decode_access_token(authorization: str | None, include_payload: bool = False):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    try:
        encoded_payload, encoded_signature = authorization[7:].split(".", 1)
        padding = "=" * (-len(encoded_payload) % 4)
        payload_bytes = base64.urlsafe_b64decode(encoded_payload + padding)
        signature_padding = "=" * (-len(encoded_signature) % 4)
        signature = base64.urlsafe_b64decode(encoded_signature + signature_padding)
        expected = hmac.new(_auth_signing_key(), encoded_payload.encode("ascii"), "sha256").digest()
        payload = json.loads(payload_bytes)
        if not hmac.compare_digest(signature, expected) or int(payload.get("exp", 0)) <= int(time.time()):
            raise ValueError("invalid token")
        username = payload.get("sub")
        if not isinstance(username, str) or not username:
            raise ValueError("invalid subject")
        return payload if include_payload else username
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="로그인 정보가 만료되었거나 올바르지 않습니다")

def _get_token_user(cur, authorization: str | None):
    payload = _decode_access_token(authorization, include_payload=True)
    _ensure_admin_tables(cur)
    cur.execute("SELECT u.id, u.username, u.name, COALESCE(m.suspended, 0) AS suspended, COALESCE(m.token_version, 0) AS token_version, COALESCE(m.role, 'member') AS role FROM users u LEFT JOIN admin_user_metadata m ON m.user_id = u.id WHERE u.username = %s", (payload['sub'],))
    user = cur.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="계정을 확인할 수 없습니다. 다시 로그인해주세요")
    if user['suspended'] or payload.get('ver', 0) != user['token_version']:
        raise HTTPException(status_code=401, detail="계정 상태가 변경되었습니다. 다시 로그인해주세요")
    return user

def _hash_pw(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > 72:
        raise HTTPException(status_code=400, detail="비밀번호가 너무 깁니다")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")

def _verify_user(cur, username: str, password: str):
    cur.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = cur.fetchone()
    if not user or len(password.encode("utf-8")) > 72 or not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다")
    _ensure_admin_tables(cur)
    cur.execute("SELECT suspended FROM admin_user_metadata WHERE user_id = %s", (user['id'],))
    metadata = cur.fetchone()
    if metadata and metadata['suspended']:
        raise HTTPException(status_code=403, detail="이용이 정지된 계정입니다")
    return user
