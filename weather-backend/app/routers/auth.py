"""회원 API: 회원가입, 로그인, 관심 지역, 내 정보 수정, 탈퇴, 비밀번호 변경. (공통 영역)"""
import os

import bcrypt
from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel

from app.database import get_conn
from app.core.auth_attempts import credential_attempt
from app.core.auth import (
    AUTH_TOKEN_TTL_SECONDS, SignUpRequest, LoginRequest,
    _validate_new_username, validate_new_password, password_needs_update, _lock_username_changes,
    _lock_admin_account_changes, _protect_last_superadmin,
    _auth_signing_key, create_member_session, revoke_member_session, _store_password_change, _get_token_user, _hash_pw, _verify_user,
)
from app.core.schema import ACTIVE_SUSPENSION, _ensure_admin_tables, _ensure_user_favorites_table, _ensure_hiking_tables, ensure_member_sessions

router = APIRouter()


@router.get('/api/auth/username-availability')
def username_availability(username: str):
    username = _validate_new_username(username)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM users WHERE username = %s', (username,))
            return {'username': username, 'available': cur.fetchone() is None}
    finally:
        conn.close()

@router.get("/api/auth/status")
def auth_status():
    """Expose whether authentication is configured without revealing its secret."""
    secret = os.getenv("AUTH_SECRET_KEY", "")
    return {
        "configured": len(secret) >= 32,
        "minimum_length": 32,
        "message": "인증을 사용할 수 있습니다" if len(secret) >= 32 else "서버 인증 설정이 완료되지 않았습니다",
    }

@router.post("/api/signup")
def signup(req: SignUpRequest):
    # Do not create accounts that cannot sign in because the token key is missing.
    _auth_signing_key()
    req.username = _validate_new_username(req.username)
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="입력값을 확인해주세요")
    validate_new_password(req.username, req.password)

    hashed = _hash_pw(req.password)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            _lock_username_changes(cur)
            cur.execute("SELECT id FROM users WHERE username = %s", (req.username,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다")
            cur.execute(
                "INSERT INTO users (username, password_hash, name) VALUES (%s, %s, %s)",
                (req.username, hashed, req.name),
            )
            user_id = cur.lastrowid
            cur.execute("INSERT INTO admin_user_metadata (user_id, registered_at) VALUES (%s, CURRENT_TIMESTAMP)", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return {"message": "회원가입이 완료되었습니다"}

@router.post("/api/login")
def login(req: LoginRequest, request: Request):
    with credential_attempt(request, req.username):
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                _ensure_admin_tables(cur)
                cur.execute(f"SELECT u.*, {ACTIVE_SUSPENSION} AS suspended, COALESCE(m.token_version, 0) AS token_version FROM users u LEFT JOIN admin_user_metadata m ON m.user_id = u.id WHERE u.username = %s", (req.username,))
                user = cur.fetchone()
                password = req.password.encode("utf-8")
                if not user or len(password) > 72 or not bcrypt.checkpw(password, user["password_hash"].encode("utf-8")):
                    raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다")
                if user['suspended']:
                    raise HTTPException(status_code=403, detail="이용이 정지된 계정입니다. 관리자에게 문의해주세요")
                ensure_member_sessions(cur)
                token = create_member_session(cur, user["id"], user["token_version"])
            conn.commit()
        finally:
            conn.close()
    return {
        "message": "로그인 성공",
        "name": user["name"],
        "username": user["username"],
        "user_id": user["id"],
        "password_needs_update": password_needs_update(user["username"], req.password),
        "access_token": token,
        "token_type": "bearer",
        "expires_in": AUTH_TOKEN_TTL_SECONDS,
    }


@router.post("/api/logout")
def logout(authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            revoke_member_session(cur, authorization)
        conn.commit()
    finally:
        conn.close()
    return {"message": "로그아웃되었습니다"}


class FavoriteLocation(BaseModel):
    name: str
    lat: float
    lon: float

@router.get('/api/users/me/role')
def get_my_role(authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            return {'role': user['role']}
    finally:
        conn.close()

class ReplaceFavoritesRequest(BaseModel):
    favorites: list[FavoriteLocation]

@router.get("/api/users/me/favorites")
def get_my_favorites(authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_user_favorites_table(cur)
            cur.execute(
                "SELECT name, latitude AS lat, longitude AS lon FROM user_favorites WHERE user_id = %s ORDER BY sort_order",
                (user["id"],),
            )
            rows = cur.fetchall()
            return [{"name": row["name"], "lat": float(row["lat"]), "lon": float(row["lon"])} for row in rows]
    finally:
        conn.close()

@router.put("/api/users/me/favorites")
def replace_my_favorites(req: ReplaceFavoritesRequest, authorization: str | None = Header(default=None)):
    if len(req.favorites) > 5:
        raise HTTPException(status_code=400, detail="관심 지역은 최대 5곳까지 저장할 수 있습니다")
    seen_names = set()
    for favorite in req.favorites:
        name = favorite.name.strip()
        if not name or len(name) > 120:
            raise HTTPException(status_code=400, detail="관심 지역 이름을 확인해주세요")
        if not (-90 <= favorite.lat <= 90 and -180 <= favorite.lon <= 180):
            raise HTTPException(status_code=400, detail="관심 지역 좌표를 확인해주세요")
        if name.casefold() in seen_names:
            raise HTTPException(status_code=400, detail="같은 관심 지역이 중복되었습니다")
        seen_names.add(name.casefold())

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_user_favorites_table(cur)
            cur.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user["id"],))
            cur.execute("DELETE FROM user_favorites WHERE user_id = %s", (user["id"],))
            if req.favorites:
                cur.executemany(
                    "INSERT INTO user_favorites (user_id, name, latitude, longitude, sort_order) VALUES (%s, %s, %s, %s, %s)",
                    [(user["id"], item.name.strip(), item.lat, item.lon, index) for index, item in enumerate(req.favorites)],
                )
        conn.commit()
    finally:
        conn.close()
    return {"message": "관심 지역이 계정에 저장되었습니다", "count": len(req.favorites)}

class UpdateUserRequest(BaseModel):
    username: str
    current_password: str
    name: str
    new_password: str | None = None

class DeleteUserRequest(BaseModel):
    username: str
    password: str

@router.put("/api/users/update")
def update_user(req: UpdateUserRequest, request: Request):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="이름을 입력해주세요")
    if req.new_password:
        validate_new_password(req.username, req.new_password)

    with credential_attempt(request, req.username):
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                user = _verify_user(cur, req.username, req.current_password)
                if req.new_password:
                    _store_password_change(cur, user, req.new_password, name)
                else:
                    cur.execute("UPDATE users SET name = %s WHERE username = %s", (name, req.username))
            conn.commit()
        finally:
            conn.close()
    return {"message": "수정되었습니다", "name": name}

@router.post("/api/users/delete")
def delete_user(req: DeleteUserRequest, request: Request):
    with credential_attempt(request, req.username):
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                _ensure_admin_tables(cur)
                _ensure_hiking_tables(cur)
                _lock_admin_account_changes(cur)
                user = _verify_user(cur, req.username, req.password, lock=True)
                _protect_last_superadmin(cur, user['id'])
                cur.execute("SELECT 1 FROM hiking_party_members WHERE user_id = %s LIMIT 1", (user['id'],))
                if cur.fetchone():
                    raise HTTPException(status_code=409, detail="회원 탈퇴 전에 참여 중인 파티에서 나가거나 파티장 권한을 넘겨 주세요")
                cur.execute("DELETE FROM users WHERE username = %s", (req.username,))
            conn.commit()
        finally:
            conn.close()
    return {"message": "탈퇴되었습니다"}

class ResetPasswordRequest(BaseModel):
    username: str
    current_password: str
    new_password: str

@router.post("/api/password/reset")
def reset_password(req: ResetPasswordRequest, request: Request):
    validate_new_password(req.username, req.new_password)
    with credential_attempt(request, req.username):
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                user = _verify_user(cur, req.username, req.current_password)
                _store_password_change(cur, user, req.new_password)
            conn.commit()
        finally:
            conn.close()
    return {"message": "비밀번호가 변경되었습니다"}
