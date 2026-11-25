"""관리자 페이지 API: 관리자 인증, 회원 관리, 감사 로그, 해양 API 점검."""
import os
import hmac
import re
from datetime import datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import AwareDatetime, BaseModel, Field, UUID4

from app.database import get_conn
from app.core.auth import (
    _validate_new_username, _lock_username_changes, _get_token_user,
    _lock_admin_account_changes, _protect_last_superadmin,
)
from app.core.schema import (
    ACTIVE_SUSPENSION, _ensure_admin_tables, _ensure_user_favorites_table,
    _ensure_hiking_tables, ensure_member_recovery,
)
from app.community.store import ensure_tables as ensure_community_tables
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
                return {'name': user['username'], 'role': user['role'], 'id': user['id']}
        finally:
            conn.close()
    expected = os.getenv("ADMIN_PASSWORD")
    if (not expected or not x_admin_password
            or not hmac.compare_digest(x_admin_password.encode("utf-8"), expected.encode("utf-8"))):
        raise HTTPException(status_code=401, detail="관리자 인증에 실패했습니다")
    return {'name': '공용 관리자 비밀번호', 'role': 'superadmin'}

MemberRole = Literal["member", "member_manager", "notice_manager", "superadmin"]
MEMBER_AUDIT = """(a.action IN ('create', 'update', 'update_password', 'rename', 'delete', 'suspend', 'restore')
                   OR LEFT(a.action, 5) = 'role:')"""


class AdminUserUpdate(BaseModel):
    username: str = Field(max_length=80)
    name: str = Field(max_length=120)
    password: str | None = None
    reason: str = Field(default="", max_length=500)


class AdminUserState(BaseModel):
    suspended: bool | None = None
    role: MemberRole | None = None
    reason: str = Field(max_length=500)
    until_at: AwareDatetime | None = None


class AdminUserDelete(BaseModel):
    username: str = Field(max_length=80)
    reason: str = Field(max_length=500)


def _required_reason(value):
    reason = value.strip()
    if not reason:
        raise HTTPException(status_code=422, detail='작업 사유를 입력해 주세요.')
    return reason


def _write_member_audit(cur, actor, action, user_id, username, reason=""):
    cur.execute("INSERT INTO admin_audit_log (actor, action, target_id, target_username) VALUES (%s, %s, %s, %s)",
                (actor['name'], action, user_id, username))
    if reason:
        cur.execute("INSERT INTO admin_member_action_notes (audit_id, reason) VALUES (%s, %s)", (cur.lastrowid, reason))


def _member_history(cur, user_id=None):
    target_filter = " AND a.target_id = %s" if user_id is not None else ""
    cur.execute(f"""
        SELECT a.id, a.actor, a.action, a.target_username, a.created_at, UNIX_TIMESTAMP(a.created_at) AS occurred_at, n.reason
        FROM admin_audit_log a LEFT JOIN admin_member_action_notes n ON n.audit_id = a.id
        WHERE {MEMBER_AUDIT}{target_filter} ORDER BY a.id DESC LIMIT 100
    """, (user_id,) if user_id is not None else ())
    return cur.fetchall()


def _require_manage_target(cur, actor, user_id, *, lock=True):
    cur.execute(f"""
        SELECT u.username, COALESCE(m.role, 'member') AS role, {ACTIVE_SUSPENSION} AS suspended
        FROM users u LEFT JOIN admin_user_metadata m ON m.user_id = u.id WHERE u.id = %s
        {"FOR UPDATE" if lock else ""}
    """, (user_id,))
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
            date_expr = "COALESCE(u.created_at, m.registered_at)" if cur.fetchone() else "m.registered_at"
            cur.execute(f"""
                SELECT u.id, u.username, u.name, {date_expr} AS created_at,
                       {ACTIVE_SUSPENSION} AS suspended, COALESCE(m.role, 'member') AS role
                FROM users u LEFT JOIN admin_user_metadata m ON m.user_id = u.id ORDER BY u.id
            """)
            return [{**row, "is_self": row["id"] == actor.get("id")} for row in cur.fetchall()]
    finally:
        conn.close()


@router.get("/api/admin/users/{user_id}/summary")
def admin_user_summary(user_id: int, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            _ensure_hiking_tables(cur)
            ensure_member_recovery(cur)
            if actor['role'] == 'superadmin':
                ensure_community_tables(cur)
            target = _require_manage_target(cur, actor, user_id, lock=False)
            cur.execute("""
                SELECT u.name, s.reason AS suspension_reason, s.until_at,
                       EXISTS(SELECT 1 FROM member_recovery_email e WHERE e.user_id = u.id) AS recovery_ready,
                       (SELECT COUNT(*) FROM hiking_plans h WHERE h.user_id = u.id) AS plan_count,
                       (SELECT COUNT(*) FROM hiking_party_members p WHERE p.user_id = u.id) AS party_count,
                       (SELECT COUNT(*) FROM hiking_parties p WHERE p.owner_user_id = u.id) AS owned_party_count
                FROM users u LEFT JOIN admin_member_suspensions s ON s.user_id = u.id WHERE u.id = %s
            """, (user_id,))
            details = cur.fetchone()
            until_at = details.pop("until_at")
            if actor['role'] == 'superadmin':
                cur.execute("""
                    SELECT (SELECT COUNT(*) FROM community_posts WHERE user_id = %s) AS post_count,
                           (SELECT COUNT(*) FROM community_comments WHERE user_id = %s) AS comment_count
                """, (user_id, user_id))
                details.update(cur.fetchone())
            return {
                "id": user_id, **target, **details,
                "suspended_until": until_at.replace(tzinfo=timezone.utc) if until_at and target["suspended"] else None,
                "history": _member_history(cur, user_id),
            }
    finally:
        conn.close()


@router.post("/api/admin/users")
def admin_create_user(x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization, 'members')
    raise HTTPException(status_code=410, detail='회원이 가입 화면에서 직접 계정을 만들도록 안내해 주세요.')


@router.put("/api/admin/users/{user_id}")
def admin_update_user(user_id: int, req: AdminUserUpdate, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    if req.password is not None:
        raise HTTPException(status_code=403, detail='회원이 비밀번호 찾기에서 직접 재설정해야 합니다.')
    username, name = req.username.strip(), req.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail='이름을 입력해 주세요.')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            _lock_username_changes(cur)
            target = _require_manage_target(cur, actor, user_id)
            renamed = username != target['username']
            if renamed:
                if actor['role'] != 'superadmin':
                    raise HTTPException(status_code=403, detail='아이디 변경은 최고 관리자만 할 수 있습니다.')
                if user_id == actor.get('id'):
                    raise HTTPException(status_code=400, detail='현재 관리자 계정의 아이디는 변경할 수 없습니다.')
                username = _validate_new_username(username)
                reason = _required_reason(req.reason)
                cur.execute("SELECT id FROM users WHERE username = %s AND id <> %s", (username, user_id))
                if cur.fetchone():
                    raise HTTPException(status_code=409, detail='이미 사용 중인 아이디입니다.')
                cur.execute("UPDATE users SET username = %s, name = %s WHERE id = %s", (username, name, user_id))
                cur.execute("INSERT INTO admin_user_metadata (user_id, token_version) VALUES (%s, 1) ON DUPLICATE KEY UPDATE token_version = token_version + 1", (user_id,))
                _write_member_audit(cur, actor, 'rename', user_id, target['username'], f'{target["username"]} → {username} · {reason}'[:500])
            else:
                cur.execute("UPDATE users SET name = %s WHERE id = %s", (name, user_id))
                _write_member_audit(cur, actor, 'update', user_id, username)
        conn.commit()
    finally:
        conn.close()
    return {"message": "회원 정보가 수정되었습니다."}


@router.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, req: AdminUserDelete, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'system')
    reason = _required_reason(req.reason)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            _ensure_user_favorites_table(cur)
            _lock_admin_account_changes(cur)
            target = _require_manage_target(cur, actor, user_id)
            if target['username'] != req.username.strip():
                raise HTTPException(status_code=409, detail='확인 아이디가 현재 회원 아이디와 일치하지 않습니다.')
            if user_id == actor.get('id'):
                raise HTTPException(status_code=400, detail='현재 관리자 계정은 삭제할 수 없습니다.')
            if target['role'] == 'superadmin':
                _protect_last_superadmin(cur, user_id)
            cur.execute("DELETE FROM user_favorites WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            _write_member_audit(cur, actor, 'delete', user_id, target['username'], reason)
        conn.commit()
    finally:
        conn.close()
    return {"message": "회원이 영구 삭제되었습니다."}


@router.patch('/api/admin/users/{user_id}/state')
def admin_user_state(user_id: int, req: AdminUserState, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization, 'members')
    if (req.suspended is None) == (req.role is None):
        raise HTTPException(status_code=422, detail='상태 변경 또는 권한 변경 중 하나를 선택해 주세요.')
    if req.role is not None and actor['role'] != 'superadmin':
        raise HTTPException(status_code=403, detail='권한 변경은 최고 관리자만 할 수 있습니다.')
    if req.until_at and (req.suspended is not True or req.until_at <= datetime.now(timezone.utc)):
        raise HTTPException(status_code=422, detail='정지 해제 예정 시각은 현재 이후로 선택해 주세요.')
    reason = _required_reason(req.reason)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            _lock_admin_account_changes(cur)
            target = _require_manage_target(cur, actor, user_id)
            if user_id == actor.get('id'):
                raise HTTPException(status_code=400, detail='현재 관리자 계정의 상태나 권한은 변경할 수 없습니다.')
            if target['role'] == 'superadmin' and (req.suspended is True or req.role not in (None, 'superadmin')):
                _protect_last_superadmin(cur, user_id)
            cur.execute('INSERT INTO admin_user_metadata (user_id) VALUES (%s) ON DUPLICATE KEY UPDATE user_id = user_id', (user_id,))
            if req.role is not None:
                if req.role == target['role']:
                    raise HTTPException(status_code=409, detail='이미 선택한 권한이 적용되어 있습니다.')
                cur.execute('UPDATE admin_user_metadata SET role = %s, token_version = token_version + 1 WHERE user_id = %s', (req.role, user_id))
                _write_member_audit(cur, actor, 'role:' + req.role, user_id, target['username'], reason)
            else:
                cur.execute('UPDATE admin_user_metadata SET suspended = %s, token_version = token_version + 1 WHERE user_id = %s', (req.suspended, user_id))
                if req.suspended:
                    until_at = req.until_at.astimezone(timezone.utc).replace(tzinfo=None) if req.until_at else None
                    cur.execute("""
                        INSERT INTO admin_member_suspensions (user_id, reason, until_at) VALUES (%s, %s, %s)
                        ON DUPLICATE KEY UPDATE reason = VALUES(reason), until_at = VALUES(until_at)
                    """, (user_id, reason, until_at))
                else:
                    cur.execute('DELETE FROM admin_member_suspensions WHERE user_id = %s', (user_id,))
                _write_member_audit(cur, actor, 'suspend' if req.suspended else 'restore', user_id, target['username'], reason)
        conn.commit()
    finally:
        conn.close()
    return {'message': '회원 상태가 변경되었습니다.'}


@router.get("/api/admin/activity")
def admin_activity(
    days: int = Query(30, ge=1, le=90),
    kind: Literal["all", "member", "notice"] = "all",
    target: str = Query("", max_length=80),
    x_admin_password: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    require_admin(x_admin_password, authorization, "system")
    clauses = ["created_at >= CURRENT_TIMESTAMP - INTERVAL %s DAY"]
    args = [days]
    if kind != "all":
        clauses.append("LEFT(a.action, 7) = 'notice_'" if kind == "notice" else MEMBER_AUDIT)
    if target.strip():
        clauses.append("LOCATE(%s, target_username) > 0")
        args.append(target.strip())
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            where = " AND ".join(clauses)
            cur.execute(f"""
                SELECT id, actor, action, target_username, created_at
                FROM admin_audit_log a WHERE {where} ORDER BY id DESC LIMIT 100
            """, args)
            return cur.fetchall()
    finally:
        conn.close()


class MountainCandidate(BaseModel):
    name: str = Field(min_length=2, max_length=24, pattern=r"^[가-힣 ]+$")
    kind: Literal["new", "course"]
    status: Literal["pending", "sourcing", "done"] = "pending"
    note: str = Field(default="", max_length=500)


def _prepare_candidates(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mountain_admin_candidates (
            name VARCHAR(24) NOT NULL, kind VARCHAR(10) NOT NULL,
            status VARCHAR(10) NOT NULL, note VARCHAR(500) NOT NULL,
            updated_at DATETIME NOT NULL, PRIMARY KEY (name, kind)
        ) DEFAULT CHARSET=utf8mb4
    """)


@router.get("/api/admin/mountain-candidates")
def list_candidates(x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization, "system")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _prepare_candidates(cur)
            cur.execute("SELECT name, kind, status, note FROM mountain_admin_candidates ORDER BY updated_at DESC, name, kind")
            return cur.fetchall()
    finally:
        conn.close()


@router.put("/api/admin/mountain-candidates")
def save_candidate(req: MountainCandidate, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization, "system")
    name = " ".join(req.name.split())
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="산 이름을 확인해 주세요.")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _prepare_candidates(cur)
            cur.execute("""
                INSERT INTO mountain_admin_candidates VALUES (%s, %s, %s, %s, UTC_TIMESTAMP())
                ON DUPLICATE KEY UPDATE status = VALUES(status), note = VALUES(note), updated_at = UTC_TIMESTAMP()
            """, (name, req.kind, req.status, req.note.strip()))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.get('/api/admin/audit')
def admin_audit(x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization, 'members')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_admin_tables(cur)
            return _member_history(cur)
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


KST = timezone(timedelta(hours=9))


def _usage_window(days):
    if days not in (7, 30):
        raise HTTPException(status_code=422, detail="7일 또는 30일을 선택하세요.")
    today = datetime.now(KST).date()
    first_day = today - timedelta(days=days - 1)
    from_utc = datetime.combine(first_day, time.min, KST).astimezone(timezone.utc).replace(tzinfo=None)
    return first_day, from_utc


class PageViewEvent(BaseModel):
    event_id: UUID4
    browser_id: UUID4
    page: Literal["weather", "mountain", "fishing", "community"]
    mountain: str | None = Field(default=None, max_length=24)


def _prepare_page_views(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS site_page_views (
            event_id CHAR(36) PRIMARY KEY,
            browser_id CHAR(36) NOT NULL,
            page VARCHAR(12) NOT NULL,
            mountain VARCHAR(24),
            viewed_at DATETIME NOT NULL,
            INDEX ix_site_page_views_time (viewed_at)
        ) DEFAULT CHARSET=utf8mb4
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS site_page_view_collection (
            id TINYINT UNSIGNED PRIMARY KEY,
            started_at DATETIME NOT NULL
        )
    """)


@router.post("/api/page-views", status_code=204)
def record_page_view(event: PageViewEvent):
    if event.mountain is not None:
        from app.weather import MOUNTAINS_DB

        if event.page != "mountain" or event.mountain not in MOUNTAINS_DB:
            raise HTTPException(status_code=422, detail="등록된 산만 선택할 수 있습니다.")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _prepare_page_views(cur)
            cur.execute("INSERT IGNORE INTO site_page_view_collection VALUES (1, UTC_TIMESTAMP())")
            cur.execute("""
                INSERT INTO site_page_views (event_id, browser_id, page, mountain, viewed_at)
                VALUES (%s, %s, %s, %s, UTC_TIMESTAMP())
                ON DUPLICATE KEY UPDATE event_id = event_id
            """, (str(event.event_id), str(event.browser_id), event.page, event.mountain))
            cur.execute("DELETE FROM site_page_views WHERE viewed_at < UTC_TIMESTAMP() - INTERVAL 30 DAY")
        conn.commit()
    finally:
        conn.close()


@router.get("/api/admin/usage")
def admin_usage(days: int = Query(default=7), x_admin_password: str | None = Header(default=None),
                authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization, "system")
    first_day, from_utc = _usage_window(days)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _prepare_page_views(cur)
            cur.execute("SELECT started_at FROM site_page_view_collection WHERE id = 1")
            collection = cur.fetchone()
            started_at = collection["started_at"].replace(tzinfo=timezone.utc) if collection else None
            cur.execute("""
                SELECT COUNT(*) AS views, COUNT(DISTINCT browser_id) AS visitors
                FROM site_page_views WHERE viewed_at >= %s
            """, (from_utc,))
            totals = cur.fetchone()
            cur.execute("""
                SELECT DATE(viewed_at + INTERVAL 9 HOUR) AS day, COUNT(*) AS views,
                       COUNT(DISTINCT browser_id) AS visitors
                FROM site_page_views WHERE viewed_at >= %s GROUP BY day
            """, (from_utc,))
            daily = {row["day"]: row for row in cur.fetchall()}
            cur.execute("""
                SELECT page, COUNT(*) AS views FROM site_page_views WHERE viewed_at >= %s
                GROUP BY page ORDER BY views DESC, page
            """, (from_utc,))
            pages = cur.fetchall()
            cur.execute("""
                SELECT mountain, COUNT(*) AS views FROM site_page_views
                WHERE viewed_at >= %s AND page = 'mountain' AND mountain IS NOT NULL
                GROUP BY mountain ORDER BY views DESC, mountain LIMIT 10
            """, (from_utc,))
            mountains = cur.fetchall()
            cur.execute("DELETE FROM site_page_views WHERE viewed_at < UTC_TIMESTAMP() - INTERVAL 30 DAY")
            search_counts = {}
            for service, table in SEARCH_TABLES.items():
                _prepare_search_events(cur, table)
                cur.execute(f"""
                    SELECT COUNT(*) AS total, COUNT(CASE WHEN outcome = 'not_found' THEN 1 END) AS not_found
                    FROM {table} WHERE searched_at >= %s
                """, (from_utc,))
                search_counts[service] = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    start_day = started_at.astimezone(KST).date() if started_at else None
    history = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        row = daily.get(day) or {"day": day, "views": 0, "visitors": 0}
        history.append(row if start_day and day >= start_day else {"day": day, "views": None, "visitors": None})
    return {
        "started_at": started_at,
        "totals": {
            "views": totals["views"] if started_at else None,
            "visitors": totals["visitors"] if started_at else None,
            "searches": sum(search_counts[service]["total"] for service in ("weather", "mountain")),
            "no_results": sum(search_counts[service]["not_found"] for service in ("weather", "mountain")),
            "fishing_selections": search_counts["fishing"]["total"],
        },
        "daily": history, "pages": pages, "mountains": mountains,
    }


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
    term: str = Query("", max_length=40),
    outcome: Literal["all", "course", "no_course", "not_found", "error", "found"] = "all",
    x_admin_password: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    require_admin(x_admin_password, authorization, "system")
    table = SEARCH_TABLES[service]
    _, from_utc = _usage_window(days)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _prepare_search_events(cur, table)
            window = "searched_at >= %s"
            cur.execute(f"SELECT outcome, COUNT(*) AS count FROM {table} WHERE {window} GROUP BY outcome", (from_utc,))
            totals = {row["outcome"]: row["count"] for row in cur.fetchall()}
            rankings = {}
            for status in ("not_found", "no_course", "found"):
                cur.execute(f"""
                    SELECT term, COUNT(*) AS count FROM {table}
                    WHERE {window} AND outcome = %s
                    GROUP BY term ORDER BY count DESC, term LIMIT 10
                """, (from_utc, status))
                rankings[status] = cur.fetchall()
            cur.execute(f"""
                SELECT DATE(searched_at + INTERVAL 9 HOUR) AS day, COUNT(*) AS count
                FROM {table} WHERE {window} GROUP BY day ORDER BY day
            """, (from_utc,))
            daily = cur.fetchall()
            condition = "" if outcome == "all" else " AND outcome = %s"
            args = (from_utc,) if outcome == "all" else (from_utc, outcome)
            if term.strip():
                condition += " AND LOCATE(%s, term) > 0"
                args += (term.strip(),)
            cur.execute(f"""
                SELECT id, term, outcome, CONCAT(DATE_FORMAT(searched_at, '%%Y-%%m-%%dT%%H:%%i:%%s'), 'Z') AS searched_at
                FROM {table} WHERE {window}{condition}
                ORDER BY searched_at DESC, id DESC LIMIT 100
            """, args)
            recent = cur.fetchall()
        conn.commit()
        return {"totals": totals, "rankings": rankings, "recent": recent, "daily": daily}
    finally:
        conn.close()
