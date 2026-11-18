"""관리자 페이지 API: 관리자 인증, 회원 관리, 감사 로그, 해양 API 점검."""
import os
import hmac
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel, Field

from app.database import get_conn
from app.core.auth import (
    SignUpRequest, _validate_username, _validate_password_username,
    _lock_username_changes, _get_token_user, _hash_pw,
)
from app.core.schema import _ensure_admin_tables, _ensure_user_favorites_table, _ensure_hiking_tables
from app.marine_integrations import check_marine_integrations

router = APIRouter()


def require_admin(x_admin_password: str | None, authorization: str | None = None, scope: str = 'admin'):
    if authorization:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                user = _get_token_user(cur, authorization)
                allowed = {'superadmin': {'admin', 'members', 'notices', 'system'}, 'member_manager': {'admin', 'members'}, 'notice_manager': {'admin', 'notices'}}
                if scope not in allowed.get(user['role'], set()):
                    raise HTTPException(status_code=403, detail='관리자 권한이 없습니다')
                return {'name': user['username'], 'role': user['role']}
        finally:
            conn.close()
    expected = os.getenv("ADMIN_PASSWORD")
    if (not expected or not x_admin_password
            or not hmac.compare_digest(x_admin_password.encode("utf-8"), expected.encode("utf-8"))):
        raise HTTPException(status_code=401, detail="관리자 인증에 실패했습니다")
    return {'name': '공용 관리자 비밀번호', 'role': 'superadmin'}

class AdminUserUpdate(BaseModel):
    username: str
    name: str
    password: str | None = None

class AdminUserState(BaseModel):
    suspended: bool | None = None
    role: str | None = None

def _require_manage_target(cur, actor, user_id):
    cur.execute("SELECT u.username, COALESCE(m.role, 'member') AS role FROM users u LEFT JOIN admin_user_metadata m ON m.user_id = u.id WHERE u.id = %s FOR UPDATE", (user_id,))
    target = cur.fetchone()
    if not target:
        raise HTTPException(status_code=404, detail='회원을 찾을 수 없습니다')
    if actor['role'] != 'superadmin' and target['role'] != 'member':
        raise HTTPException(status_code=403, detail='관리자 계정은 최고 관리자만 관리할 수 있습니다')
    return target

@router.post("/api/admin/verify")
def admin_verify(x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization)
    return {"ok": True, "role": actor['role']}

@router.post("/api/admin/integrations/marine/check")
def admin_check_marine_integrations(x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    """Privileged, secret-safe live connectivity test for configured marine APIs."""
    require_admin(x_admin_password, authorization, 'system')
    return check_marine_integrations()

@router.get("/api/admin/users")
def admin_list_users(x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            cur.execute("SHOW COLUMNS FROM users LIKE 'created_at'")
            has_created_at = bool(cur.fetchone())
            date_expr = "COALESCE(u.created_at, m.registered_at)" if has_created_at else "m.registered_at"
            cur.execute("SELECT u.id, u.username, u.name, " + date_expr + " AS created_at, COALESCE(m.suspended, 0) AS suspended, COALESCE(m.role, 'member') AS role FROM users u LEFT JOIN admin_user_metadata m ON m.user_id = u.id ORDER BY u.id")
            return cur.fetchall()
    finally:
        conn.close()

@router.get("/api/admin/users/{user_id}/summary")
def admin_user_summary(user_id: int, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username, name FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="회원을 찾을 수 없습니다")
            _ensure_hiking_tables(cur)
            cur.execute("SELECT COUNT(*) AS total FROM hiking_plans WHERE user_id = %s", (user_id,))
            plans = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) AS total FROM hiking_party_members WHERE user_id = %s", (user_id,))
            parties = cur.fetchone()["total"]
            return {**user, "plan_count": plans, "party_count": parties}
    finally:
        conn.close()

@router.post("/api/admin/users")
def admin_create_user(req: SignUpRequest, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    req.username = _validate_username(req.username)
    if len(req.password) < 4 or not req.name.strip():
        raise HTTPException(status_code=400, detail="입력값을 확인해주세요")
    _validate_password_username(req.username, req.password)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            _lock_username_changes(cur)
            cur.execute("SELECT id FROM users WHERE username = %s", (req.username,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다")
            cur.execute("INSERT INTO users (username, password_hash, name) VALUES (%s, %s, %s)",
                        (req.username, _hash_pw(req.password), req.name.strip()))
            user_id = cur.lastrowid
            cur.execute("INSERT INTO admin_user_metadata (user_id, registered_at) VALUES (%s, CURRENT_TIMESTAMP)", (user_id,))
            cur.execute("INSERT INTO admin_audit_log (actor, action, target_id, target_username) VALUES (%s, 'create', %s, %s)", (actor['name'], user_id, req.username))
        conn.commit()
    finally:
        conn.close()
    return {"message": "회원이 추가되었습니다"}

@router.put("/api/admin/users/{user_id}")
def admin_update_user(user_id: int, req: AdminUserUpdate, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    req.username = _validate_username(req.username)
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="입력값을 확인해주세요")
    if req.password and len(req.password) < 4:
        raise HTTPException(status_code=400, detail="비밀번호는 4자 이상이어야 합니다")
    if req.password:
        _validate_password_username(req.username, req.password)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            _lock_username_changes(cur)
            target = _require_manage_target(cur, actor, user_id)
            if req.username != target['username'] and not req.password:
                raise HTTPException(status_code=400, detail='아이디를 변경할 때 새 비밀번호도 입력해주세요')
            cur.execute("SELECT id FROM users WHERE username = %s AND id <> %s", (req.username, user_id))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다")
            if req.password:
                cur.execute("UPDATE users SET username = %s, name = %s, password_hash = %s WHERE id = %s",
                            (req.username, req.name.strip(), _hash_pw(req.password), user_id))
            else:
                cur.execute("UPDATE users SET username = %s, name = %s WHERE id = %s",
                            (req.username, req.name.strip(), user_id))
            if req.password or req.username != target['username']:
                cur.execute("INSERT INTO admin_user_metadata (user_id, token_version) VALUES (%s, 1) ON DUPLICATE KEY UPDATE token_version = token_version + 1", (user_id,))
            cur.execute("INSERT INTO admin_audit_log (actor, action, target_id, target_username) VALUES (%s, %s, %s, %s)", (actor['name'], 'update_password' if req.password else 'update', user_id, target['username']))
        conn.commit()
    finally:
        conn.close()
    return {"message": "수정되었습니다"}

@router.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            target = _require_manage_target(cur, actor, user_id)
            if target['username'] == actor['name']:
                raise HTTPException(status_code=400, detail='현재 관리자 계정은 삭제할 수 없습니다')
            _ensure_user_favorites_table(cur)
            cur.execute("DELETE FROM user_favorites WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            cur.execute("INSERT INTO admin_audit_log (actor, action, target_id, target_username) VALUES (%s, 'delete', %s, %s)", (actor['name'], user_id, target['username']))
        conn.commit()
    finally:
        conn.close()
    return {"message": "삭제되었습니다"}

@router.patch('/api/admin/users/{user_id}/state')
def admin_user_state(user_id: int, req: AdminUserState, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    if req.suspended is None and req.role is None:
        raise HTTPException(status_code=400, detail='변경할 상태를 선택해주세요')
    if req.role is not None and (actor['role'] != 'superadmin' or req.role not in ('member', 'member_manager', 'notice_manager', 'superadmin')):
        raise HTTPException(status_code=403, detail='권한 변경은 최고 관리자만 할 수 있습니다')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            target = _require_manage_target(cur, actor, user_id)
            if target['username'] == actor['name']:
                raise HTTPException(status_code=400, detail='현재 관리자 계정의 상태나 권한은 변경할 수 없습니다')
            cur.execute('INSERT INTO admin_user_metadata (user_id) VALUES (%s) ON DUPLICATE KEY UPDATE user_id = user_id', (user_id,))
            if req.suspended is not None:
                cur.execute('UPDATE admin_user_metadata SET suspended = %s, token_version = token_version + 1 WHERE user_id = %s', (req.suspended, user_id))
                cur.execute('INSERT INTO admin_audit_log (actor, action, target_id, target_username) VALUES (%s, %s, %s, %s)', (actor['name'], 'suspend' if req.suspended else 'restore', user_id, target['username']))
            if req.role is not None:
                cur.execute('UPDATE admin_user_metadata SET role = %s WHERE user_id = %s', (req.role, user_id))
                cur.execute('INSERT INTO admin_audit_log (actor, action, target_id, target_username) VALUES (%s, %s, %s, %s)', (actor['name'], 'role:' + req.role, user_id, target['username']))
        conn.commit()
        return {'message': '회원 상태가 변경되었습니다'}
    finally:
        conn.close()

@router.get('/api/admin/audit')
def admin_audit(x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization, 'members')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            cur.execute('SELECT id, actor, action, target_username, created_at FROM admin_audit_log ORDER BY id DESC LIMIT 100')
            return cur.fetchall()
    finally:
        conn.close()

SEARCH_TABLES = {
    "mountain": "mountain_search_events",
    "weather": "weather_search_events",
    "fishing": "fishing_search_events",
}

class MountainSearchEvent(BaseModel):
    service: Literal["mountain", "weather", "fishing"] = "mountain"
    term: str = Field(max_length=40)
    outcome: Literal["course", "no_course", "not_found", "error", "found"]


def _prepare_search_events(cur, table):
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            term VARCHAR(40) NOT NULL,
            outcome VARCHAR(16) NOT NULL,
            searched_at DATETIME NOT NULL,
            INDEX ix_mountain_search_time (searched_at)
        ) DEFAULT CHARSET=utf8mb4
    """)
    cur.execute(f"DELETE FROM {table} WHERE searched_at < UTC_TIMESTAMP() - INTERVAL 30 DAY")


@router.post("/api/search-events", status_code=204)
@router.post("/api/mountain/search-events", status_code=204)
def record_mountain_search(event: MountainSearchEvent):
    term = " ".join(event.term.split())
    if event.service == "mountain":
        if not re.fullmatch(r"[가-힣 ]{2,24}", term):
            return
        if event.outcome in ("not_found", "error") and not term.endswith(("산", "봉", "악", "오름")):
            return
    elif event.service == "weather":
        if not re.fullmatch(r"[가-힣 ]{2,24}", term):
            return
        if event.outcome != "found" and not term.endswith(("시", "군", "구", "읍", "면", "동", "리", "도")):
            return
    else:
        if event.outcome != "found" or not re.fullmatch(r"[가-힣 ]{2,24}(?:\([0-9]{1,3}km\))?", term):
            return
    table = SEARCH_TABLES[event.service]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _prepare_search_events(cur, table)
            cur.execute(
                f"INSERT INTO {table} (term, outcome, searched_at) VALUES (%s, %s, UTC_TIMESTAMP())",
                (term, event.outcome),
            )
        conn.commit()
    finally:
        conn.close()


@router.get("/api/admin/searches")
@router.get("/api/admin/mountain-searches")
def list_mountain_searches(
    service: Literal["mountain", "weather", "fishing"] = "mountain",
    days: int = Query(default=7),
    outcome: Literal["all", "course", "no_course", "not_found", "error", "found"] = "all",
    x_admin_password: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    require_admin(x_admin_password, authorization, "system")
    table = SEARCH_TABLES[service]
    if days not in (7, 30):
        raise HTTPException(status_code=422, detail="7일 또는 30일을 선택하세요.")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _prepare_search_events(cur, table)
            window = "searched_at >= UTC_TIMESTAMP() - INTERVAL %s DAY"
            cur.execute(f"SELECT outcome, COUNT(*) AS count FROM {table} WHERE {window} GROUP BY outcome", (days,))
            totals = {row["outcome"]: row["count"] for row in cur.fetchall()}
            rankings = {}
            for status in ("not_found", "no_course", "found"):
                cur.execute(f"""
                    SELECT term, COUNT(*) AS count FROM {table}
                    WHERE {window} AND outcome = %s
                    GROUP BY term ORDER BY count DESC, term LIMIT 10
                """, (days, status))
                rankings[status] = cur.fetchall()
            condition = "" if outcome == "all" else " AND outcome = %s"
            args = (days,) if outcome == "all" else (days, outcome)
            cur.execute(f"""
                SELECT id, term, outcome, CONCAT(DATE_FORMAT(searched_at, '%%Y-%%m-%%dT%%H:%%i:%%s'), 'Z') AS searched_at
                FROM {table} WHERE {window}{condition}
                ORDER BY searched_at DESC, id DESC LIMIT 100
            """, args)
            recent = cur.fetchall()
        conn.commit()
        return {"totals": totals, "rankings": rankings, "recent": recent}
    finally:
        conn.close()
