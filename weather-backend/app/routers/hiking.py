"""산악 페이지 API: 산행 계획 저장, 산행 파티(초대·일정·공동 준비물)."""
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field

from app.database import get_conn
from app.core.auth import _get_token_user
from app.core.schema import _ensure_hiking_tables

router = APIRouter()


class HikingPlanItem(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    checked: bool = False

class HikingPlanRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mountain_name: str = Field(min_length=1, max_length=120)
    course_index: int = Field(default=0, ge=0, le=99)
    course_name: str = Field(min_length=1, max_length=180)
    weather_date: str = Field(default="", max_length=24)
    selected_date_idx: int = Field(default=0, ge=0, le=30)
    pace: str = "normal"
    departure_offset_hours: int = Field(default=0, ge=0, le=48)
    departure_at: str = Field(default="", max_length=40)
    items: list[HikingPlanItem] = Field(default_factory=list)

class HikingPartyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    plan_id: int | None = None
    mountain_name: str = Field(min_length=1, max_length=120)
    course_name: str = Field(min_length=1, max_length=180)
    weather_date: str = Field(default="", max_length=24)
    pace: str = "normal"
    departure_offset_hours: int = Field(default=0, ge=0, le=48)
    departure_at: str = Field(default="", max_length=40)
    items: list[HikingPlanItem] = Field(default_factory=list)

class HikingPartyInviteRequest(BaseModel):
    username: str = Field(min_length=4, max_length=80)

class HikingPartyInviteResponse(BaseModel):
    decision: str

class HikingPartyItemRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    quantity: int = Field(default=1, ge=1, le=99)
    kind: Literal["personal", "shared", "assigned"] = "shared"
    unit: str = Field(default="", max_length=12)
    assigned_to_username: str | None = Field(default=None, max_length=80)


class GearAllocationRequest(BaseModel):
    quantity: int = Field(ge=0, le=99)


class GearCheckRequest(BaseModel):
    is_packed: bool

class HikingPartyItemUpdate(BaseModel):
    quantity: int | None = Field(default=None, ge=1, le=99)
    assigned_to_username: str | None = Field(default=None, max_length=80)
    is_packed: bool | None = None

def _serialize_hiking_plan(row):
    try:
        items = json.loads(row.get("items_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        items = []
    return {
        "id": row["id"],
        "name": row["name"],
        "mountain_name": row["mountain_name"],
        "course_index": int(row.get("course_index") or 0),
        "course_name": row["course_name"],
        "weather_date": row.get("weather_date") or "",
        "selected_date_idx": int(row.get("selected_date_idx") or 0),
        "pace": row.get("pace") or "normal",
        "departure_offset_hours": int(row.get("departure_offset_hours") or 0),
        "departure_at": row.get("departure_at") or "",
        "items": items if isinstance(items, list) else [],
        "updated_at": str(row.get("updated_at") or ""),
    }

def _validate_hiking_plan(req: HikingPlanRequest):
    if req.pace not in {"slow", "normal", "fast"}:
        raise HTTPException(status_code=400, detail="산행 속도를 확인해주세요")
    if any(not value.strip() for value in (req.name, req.mountain_name, req.course_name)):
        raise HTTPException(status_code=400, detail="계획 이름과 산·코스를 입력해 주세요")
    if len(req.items) > 100:
        raise HTTPException(status_code=400, detail="준비물은 계획당 100개까지 저장할 수 있습니다")
    normalized = [item.name.strip() for item in req.items]
    if any(not name for name in normalized) or len({name.casefold() for name in normalized}) != len(normalized):
        raise HTTPException(status_code=400, detail="준비물 이름이 비어 있거나 중복되었습니다")

def _party_member_role(cur, party_id: int, user_id: int):
    cur.execute(
        "SELECT role FROM hiking_party_members WHERE party_id = %s AND user_id = %s",
        (party_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="이 산행 파티의 구성원이 아닙니다")
    return row["role"]

def _get_party_items(cur, party_id: int):
    cur.execute("""
        SELECT i.id, i.name, i.quantity, i.is_packed, i.assigned_to_user_id,
               u.username AS assigned_to_username, u.name AS assigned_to_name,
               creator.username AS created_by_username,
               COALESCE(mode.kind, 'shared') AS kind, COALESCE(mode.unit, '') AS unit
        FROM hiking_party_items i
        LEFT JOIN hiking_party_item_modes mode ON mode.item_id = i.id
        LEFT JOIN users u ON u.id = i.assigned_to_user_id
        JOIN users creator ON creator.id = i.created_by_user_id
        WHERE i.party_id = %s
        ORDER BY i.id
    """, (party_id,))
    items = cur.fetchall()
    cur.execute("""
        SELECT a.item_id, u.username, u.name, a.quantity, a.is_packed
        FROM hiking_party_item_members a JOIN users u ON u.id = a.user_id
        WHERE a.party_id = %s ORDER BY u.username
    """, (party_id,))
    allocations = {}
    for row in cur.fetchall():
        allocations.setdefault(row["item_id"], []).append({
            "username": row["username"], "name": row["name"],
            "quantity": row["quantity"], "is_packed": bool(row["is_packed"]),
        })
    for item in items:
        rows = allocations.get(item["id"], [])
        if item.pop("assigned_to_user_id") is not None and not rows:
            rows = [{"username": item["assigned_to_username"], "name": item["assigned_to_name"],
                     "quantity": item["quantity"], "is_packed": bool(item["is_packed"])}]
        item["member_items"] = rows
        item["is_packed"] = bool(item["is_packed"])
    return items


def _lock_gear_item(cur, party_id, item_id, user_id):
    cur.execute("SELECT id FROM hiking_parties WHERE id = %s FOR UPDATE", (party_id,))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다")
    role = _party_member_role(cur, party_id, user_id)
    cur.execute("""
        SELECT i.*, mode.kind FROM hiking_party_items i
        LEFT JOIN hiking_party_item_modes mode ON mode.item_id = i.id
        WHERE i.id = %s AND i.party_id = %s FOR UPDATE
    """, (item_id, party_id))
    item = cur.fetchone()
    if not item:
        raise HTTPException(status_code=404, detail="준비물을 찾을 수 없습니다")
    return role, item


def _adopt_legacy_allocation(cur, item):
    if item["kind"] is not None:
        return
    cur.execute("INSERT INTO hiking_party_item_modes (item_id, kind) VALUES (%s, 'shared')", (item["id"],))
    if item["assigned_to_user_id"] is not None:
        cur.execute("""
            INSERT INTO hiking_party_item_members (item_id, party_id, user_id, quantity, is_packed)
            SELECT %s, party_id, user_id, %s, %s FROM hiking_party_members WHERE party_id=%s AND user_id=%s
        """, (item["id"], item["quantity"], item["is_packed"], item["party_id"], item["assigned_to_user_id"]))
    cur.execute("UPDATE hiking_party_items SET assigned_to_user_id=NULL, is_packed=0 WHERE id=%s", (item["id"],))


def _resolve_gear_assignee(cur, party_id, username):
    if not username or not username.strip():
        raise HTTPException(status_code=400, detail="준비물을 챙길 파티원 한 명을 선택해 주세요")
    cur.execute("""
        SELECT m.user_id FROM hiking_party_members m JOIN users u ON u.id = m.user_id
        WHERE m.party_id=%s AND u.username=%s FOR UPDATE
    """, (party_id, username.strip()))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="담당자는 현재 파티 구성원이어야 합니다")
    return row["user_id"]


def _assign_exclusive_gear(cur, party_id, item_id, assignee_id, quantity):
    cur.execute("DELETE FROM hiking_party_item_members WHERE item_id=%s AND user_id<>%s", (item_id, assignee_id))
    cur.execute("""
        INSERT INTO hiking_party_item_members (item_id, user_id, party_id, quantity) VALUES (%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE is_packed=IF(quantity=%s,is_packed,0), quantity=%s
    """, (item_id, assignee_id, party_id, quantity, quantity, quantity))


@router.get("/api/users/me/hiking-plans")
def list_my_hiking_plans(authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("SELECT * FROM hiking_plans WHERE user_id = %s ORDER BY updated_at DESC, id DESC", (user["id"],))
            return [_serialize_hiking_plan(row) for row in cur.fetchall()]
    finally:
        conn.close()

@router.post("/api/users/me/hiking-plans")
def save_hiking_plan(req: HikingPlanRequest, authorization: str | None = Header(default=None)):
    _validate_hiking_plan(req)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("""
                INSERT INTO hiking_plans
                    (user_id, name, mountain_name, course_index, course_name, weather_date,
                     selected_date_idx, pace, departure_offset_hours, departure_at, items_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user["id"], req.name.strip(), req.mountain_name.strip(), req.course_index,
                req.course_name.strip(), req.weather_date, req.selected_date_idx, req.pace,
                req.departure_offset_hours, req.departure_at,
                json.dumps([{"name": item.name.strip(), "checked": item.checked} for item in req.items], ensure_ascii=False),
            ))
            plan_id = cur.lastrowid
        conn.commit()
        return {"id": plan_id, "message": "산행 계획을 계정에 저장했습니다"}
    finally:
        conn.close()

@router.put("/api/users/me/hiking-plans/{plan_id}")
def update_hiking_plan(plan_id: int, req: HikingPlanRequest, authorization: str | None = Header(default=None)):
    _validate_hiking_plan(req)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("""
                UPDATE hiking_plans SET name = %s, mountain_name = %s, course_index = %s,
                    course_name = %s, weather_date = %s, selected_date_idx = %s, pace = %s,
                    departure_offset_hours = %s, departure_at = %s, items_json = %s
                WHERE id = %s AND user_id = %s
            """, (
                req.name.strip(), req.mountain_name.strip(), req.course_index, req.course_name.strip(),
                req.weather_date, req.selected_date_idx, req.pace, req.departure_offset_hours,
                req.departure_at,
                json.dumps([{"name": item.name.strip(), "checked": item.checked} for item in req.items], ensure_ascii=False),
                plan_id, user["id"],
            ))
            if cur.rowcount == 0:
                cur.execute("SELECT id FROM hiking_plans WHERE id = %s AND user_id = %s", (plan_id, user["id"]))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="저장된 산행 계획을 찾을 수 없습니다")
        conn.commit()
        return {"message": "산행 계획을 업데이트했습니다"}
    finally:
        conn.close()

@router.delete("/api/users/me/hiking-plans/{plan_id}")
def delete_hiking_plan(plan_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("DELETE FROM hiking_plans WHERE id = %s AND user_id = %s", (plan_id, user["id"]))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="저장된 산행 계획을 찾을 수 없습니다")
        conn.commit()
        return {"message": "산행 계획을 삭제했습니다"}
    finally:
        conn.close()

@router.get("/api/hiking/parties/invitations")
def list_hiking_party_invitations(authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("""
                SELECT i.id, i.party_id, p.name AS party_name, p.mountain_name, p.course_name,
                       p.weather_date, inviter.username AS invited_by_username, inviter.name AS invited_by_name
                FROM hiking_party_invites i
                JOIN hiking_parties p ON p.id = i.party_id
                JOIN users inviter ON inviter.id = i.invited_by_user_id
                WHERE i.invited_user_id = %s AND i.status = 'pending'
                ORDER BY i.created_at DESC
            """, (user["id"],))
            return cur.fetchall()
    finally:
        conn.close()

@router.post("/api/hiking/parties/invitations/{invite_id}/respond")
def respond_to_hiking_party_invite(invite_id: int, req: HikingPartyInviteResponse, authorization: str | None = Header(default=None)):
    if req.decision not in {"accept", "decline"}:
        raise HTTPException(status_code=400, detail="초대 응답을 확인해주세요")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("SELECT party_id FROM hiking_party_invites WHERE id = %s AND invited_user_id = %s AND status = 'pending' FOR UPDATE", (invite_id, user["id"]))
            invite = cur.fetchone()
            if not invite:
                raise HTTPException(status_code=404, detail="대기 중인 초대를 찾을 수 없습니다")
            new_status = "accepted" if req.decision == "accept" else "declined"
            cur.execute("UPDATE hiking_party_invites SET status = %s WHERE id = %s", (new_status, invite_id))
            if req.decision == "accept":
                cur.execute("INSERT IGNORE INTO hiking_party_members (party_id, user_id, role) VALUES (%s, %s, 'member')", (invite["party_id"], user["id"]))
        conn.commit()
        return {"message": "파티 초대를 수락했습니다" if req.decision == "accept" else "파티 초대를 거절했습니다"}
    finally:
        conn.close()

@router.get("/api/hiking/parties")
def list_hiking_parties(authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("""
                SELECT p.id, p.name, p.mountain_name, p.course_name, p.weather_date,
                       p.pace, p.departure_offset_hours, p.departure_at, p.owner_user_id,
                       owner.username AS owner_username, owner.name AS owner_name,
                       mine.role,
                       (SELECT COUNT(*) FROM hiking_party_members all_members WHERE all_members.party_id = p.id) AS member_count
                FROM hiking_parties p
                JOIN hiking_party_members mine ON mine.party_id = p.id AND mine.user_id = %s
                JOIN users owner ON owner.id = p.owner_user_id
                ORDER BY p.updated_at DESC, p.id DESC
            """, (user["id"],))
            return cur.fetchall()
    finally:
        conn.close()

@router.post("/api/hiking/parties")
def create_hiking_party(req: HikingPartyCreateRequest, authorization: str | None = Header(default=None)):
    if req.pace not in {"slow", "normal", "fast"}:
        raise HTTPException(status_code=400, detail="산행 속도를 확인해주세요")
    if any(not value.strip() for value in (req.name, req.mountain_name, req.course_name)):
        raise HTTPException(status_code=400, detail="계획 이름과 산·코스를 입력해 주세요")
    if len(req.items) > 100:
        raise HTTPException(status_code=400, detail="공동 준비물은 파티당 100개까지 등록할 수 있습니다")
    normalized_items = [item.name.strip() for item in req.items]
    if any(not name for name in normalized_items) or len({name.casefold() for name in normalized_items}) != len(normalized_items):
        raise HTTPException(status_code=400, detail="준비물 이름이 비어 있거나 중복되었습니다")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            if req.plan_id is not None:
                cur.execute("SELECT id FROM hiking_plans WHERE id = %s AND user_id = %s", (req.plan_id, user["id"]))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="연결할 산행 계획을 찾을 수 없습니다")
            cur.execute("""
                INSERT INTO hiking_parties
                    (owner_user_id, plan_id, name, mountain_name, course_name, weather_date, pace, departure_offset_hours, departure_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user["id"], req.plan_id, req.name.strip(), req.mountain_name.strip(), req.course_name.strip(),
                req.weather_date, req.pace, req.departure_offset_hours, req.departure_at,
            ))
            party_id = cur.lastrowid
            cur.execute("INSERT INTO hiking_party_members (party_id, user_id, role) VALUES (%s, %s, 'owner')", (party_id, user["id"]))
            if req.items:
                cur.executemany(
                    "INSERT INTO hiking_party_items (party_id, name, quantity, created_by_user_id, is_packed, assigned_to_user_id) VALUES (%s, %s, %s, %s, %s, %s)",
                    [(party_id, item.name.strip(), 1, user["id"], 1 if item.checked else 0, user["id"] if item.checked else None) for item in req.items],
                )
        conn.commit()
        return {"id": party_id, "message": "산행 파티를 만들었습니다"}
    finally:
        conn.close()

@router.get("/api/hiking/parties/{party_id}")
def get_hiking_party(party_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            role = _party_member_role(cur, party_id, user["id"])
            cur.execute("""
                SELECT p.id, p.name, p.plan_id, p.mountain_name, p.course_name, p.weather_date, p.pace,
                       p.departure_offset_hours, p.departure_at, p.owner_user_id,
                       owner.username AS owner_username, owner.name AS owner_name
                FROM hiking_parties p JOIN users owner ON owner.id = p.owner_user_id WHERE p.id = %s
            """, (party_id,))
            party = cur.fetchone()
            if not party:
                raise HTTPException(status_code=404, detail="산행 파티를 찾을 수 없습니다")
            cur.execute("""
                SELECT u.username, u.name, m.role FROM hiking_party_members m
                JOIN users u ON u.id = m.user_id WHERE m.party_id = %s ORDER BY m.joined_at, u.username
            """, (party_id,))
            members = cur.fetchall()
            pending_invites = []
            if role == "owner":
                cur.execute("""SELECT i.id, u.username, u.name FROM hiking_party_invites i
                    JOIN users u ON u.id = i.invited_user_id
                    WHERE i.party_id = %s AND i.status = 'pending' ORDER BY i.created_at""", (party_id,))
                pending_invites = cur.fetchall()
            cur.execute("SELECT actor_name, old_schedule, new_schedule, created_at FROM hiking_party_schedule_log WHERE party_id = %s ORDER BY id DESC LIMIT 5", (party_id,))
            history = cur.fetchall()
            return {**party, "role": role, "members": members, "items": _get_party_items(cur, party_id), "pending_invites": pending_invites, "schedule_history": history}
    finally:
        conn.close()

@router.post("/api/hiking/parties/{party_id}/invites")
def invite_to_hiking_party(party_id: int, req: HikingPartyInviteRequest, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            inviter = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            role = _party_member_role(cur, party_id, inviter["id"])
            if role != "owner":
                raise HTTPException(status_code=403, detail="파티장만 새 구성원을 초대할 수 있습니다")
            cur.execute("SELECT id FROM users WHERE username = %s", (req.username.strip(),))
            invitee = cur.fetchone()
            if not invitee:
                raise HTTPException(status_code=404, detail="해당 아이디를 찾을 수 없습니다")
            if invitee["id"] == inviter["id"]:
                raise HTTPException(status_code=400, detail="본인에게는 초대할 수 없습니다")
            cur.execute("SELECT 1 FROM hiking_party_members WHERE party_id = %s AND user_id = %s", (party_id, invitee["id"]))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="이미 파티 구성원입니다")
            cur.execute("""
                INSERT INTO hiking_party_invites (party_id, invited_user_id, invited_by_user_id, status)
                VALUES (%s, %s, %s, 'pending')
                ON DUPLICATE KEY UPDATE invited_by_user_id = VALUES(invited_by_user_id), status = 'pending', updated_at = CURRENT_TIMESTAMP
            """, (party_id, invitee["id"], inviter["id"]))
        conn.commit()
        return {"message": "파티 초대를 보냈습니다"}
    finally:
        conn.close()

class PartyTransferRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)

class PartyScheduleRequest(BaseModel):
    plan_id: int
    expected_departure_at: str = Field(default="", max_length=40)
    expected_mountain_name: str = Field(default="", max_length=120)
    expected_course_name: str = Field(default="", max_length=180)

@router.post("/api/hiking/parties/{party_id}/schedule")
def update_party_schedule(party_id: int, req: PartyScheduleRequest, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("SELECT * FROM hiking_parties WHERE id = %s FOR UPDATE", (party_id,))
            party = cur.fetchone()
            if not party or party['owner_user_id'] != user['id']:
                raise HTTPException(status_code=403, detail="파티장만 일정을 변경할 수 있습니다")
            if (party['departure_at'], party['mountain_name'], party['course_name']) != (req.expected_departure_at, req.expected_mountain_name, req.expected_course_name):
                raise HTTPException(status_code=409, detail="파티 일정이 변경되었습니다. 새로고침 후 다시 확인해 주세요")
            cur.execute("SELECT * FROM hiking_plans WHERE id = %s AND user_id = %s", (req.plan_id, user['id']))
            plan = cur.fetchone()
            if not plan:
                raise HTTPException(status_code=404, detail="본인의 저장된 계획을 찾을 수 없습니다")
            old = f"{party['mountain_name']} · {party['course_name']} · {party['departure_at']}"
            new = f"{plan['mountain_name']} · {plan['course_name']} · {plan['departure_at']}"
            cur.execute("UPDATE hiking_parties SET plan_id=%s, mountain_name=%s, course_name=%s, weather_date=%s, pace=%s, departure_offset_hours=%s, departure_at=%s WHERE id=%s", (plan['id'], plan['mountain_name'], plan['course_name'], plan['weather_date'], plan['pace'], plan['departure_offset_hours'], plan['departure_at'], party_id))
            cur.execute("INSERT INTO hiking_party_schedule_log (party_id, actor_name, old_schedule, new_schedule) VALUES (%s,%s,%s,%s)", (party_id, user['name'], old, new))
        conn.commit()
        return {"message": "파티 일정을 변경했습니다"}
    finally:
        conn.close()

@router.delete("/api/hiking/parties/{party_id}/invites/{invite_id}")
def cancel_party_invite(party_id: int, invite_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            if _party_member_role(cur, party_id, user['id']) != 'owner':
                raise HTTPException(status_code=403, detail="파티장만 초대를 취소할 수 있습니다")
            cur.execute("UPDATE hiking_party_invites SET status='cancelled' WHERE id=%s AND party_id=%s AND status='pending'", (invite_id, party_id))
            if not cur.rowcount:
                raise HTTPException(status_code=404, detail="대기 중인 초대가 없습니다")
        conn.commit()
        return {"message": "초대를 취소했습니다"}
    finally:
        conn.close()

@router.post("/api/hiking/parties/{party_id}/leave")
def leave_party(party_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("SELECT id, owner_user_id FROM hiking_parties WHERE id=%s FOR UPDATE", (party_id,))
            party = cur.fetchone()
            if not party:
                raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다")
            if party['owner_user_id'] == user['id']:
                raise HTTPException(status_code=409, detail="파티장은 권한을 넘기거나 파티를 삭제해 주세요")
            _party_member_role(cur, party_id, user['id'])
            cur.execute("UPDATE hiking_party_items SET assigned_to_user_id=NULL, is_packed=0 WHERE party_id=%s AND assigned_to_user_id=%s", (party_id, user['id']))
            cur.execute("UPDATE hiking_party_items SET created_by_user_id=%s WHERE party_id=%s AND created_by_user_id=%s", (party['owner_user_id'], party_id, user['id']))
            cur.execute("DELETE FROM hiking_party_members WHERE party_id=%s AND user_id=%s", (party_id, user['id']))
        conn.commit()
        return {"message": "파티에서 나왔습니다"}
    finally:
        conn.close()

@router.post("/api/hiking/parties/{party_id}/transfer")
def transfer_party(party_id: int, req: PartyTransferRequest, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("SELECT owner_user_id FROM hiking_parties WHERE id=%s FOR UPDATE", (party_id,))
            party = cur.fetchone()
            if not party or party['owner_user_id'] != user['id']:
                raise HTTPException(status_code=403, detail="파티장만 권한을 넘길 수 있습니다")
            cur.execute("SELECT u.id FROM users u JOIN hiking_party_members m ON m.user_id=u.id WHERE m.party_id=%s AND u.username=%s", (party_id, req.username.strip()))
            target = cur.fetchone()
            if not target or target['id'] == user['id']:
                raise HTTPException(status_code=400, detail="다른 파티 구성원을 선택해 주세요")
            cur.execute("UPDATE hiking_party_members SET role='member' WHERE party_id=%s AND user_id=%s", (party_id, user['id']))
            cur.execute("UPDATE hiking_party_members SET role='owner' WHERE party_id=%s AND user_id=%s", (party_id, target['id']))
            cur.execute("UPDATE hiking_parties SET owner_user_id=%s, plan_id=NULL WHERE id=%s", (target['id'], party_id))
        conn.commit()
        return {"message": "파티장 권한을 넘겼습니다"}
    finally:
        conn.close()

@router.post("/api/hiking/parties/{party_id}/items")
def add_hiking_party_item(party_id: int, req: HikingPartyItemRequest, authorization: str | None = Header(default=None)):
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="준비물 이름을 입력해 주세요")
    if req.kind != "assigned" and req.assigned_to_username is not None:
        raise HTTPException(status_code=400, detail="지정한 사람만 챙기는 유형에서 담당자를 선택해 주세요")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            cur.execute("SELECT id FROM hiking_parties WHERE id=%s FOR UPDATE", (party_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="파티를 찾을 수 없습니다")
            _party_member_role(cur, party_id, user["id"])
            assignee_id = _resolve_gear_assignee(cur, party_id, req.assigned_to_username) if req.kind == "assigned" else None
            cur.execute("SELECT COUNT(*) AS count FROM hiking_party_items WHERE party_id = %s", (party_id,))
            if cur.fetchone()["count"] >= 100:
                raise HTTPException(status_code=400, detail="공동 준비물은 파티당 100개까지 등록할 수 있습니다")
            cur.execute("INSERT INTO hiking_party_items (party_id, name, quantity, created_by_user_id) VALUES (%s, %s, %s, %s)",
                        (party_id, req.name.strip(), req.quantity, user["id"]))
            item_id = cur.lastrowid
            if {"kind", "unit"} & req.model_fields_set:
                cur.execute("INSERT INTO hiking_party_item_modes (item_id,kind,unit) VALUES (%s,%s,%s)",
                            (item_id, req.kind, req.unit.strip()))
            if assignee_id is not None:
                _assign_exclusive_gear(cur, party_id, item_id, assignee_id, req.quantity)
        conn.commit()
        return {"id": item_id, "message": "준비물을 추가했습니다"}
    finally:
        conn.close()

@router.patch("/api/hiking/parties/{party_id}/items/{item_id}")
def update_hiking_party_item(party_id: int, item_id: int, req: HikingPartyItemUpdate, authorization: str | None = Header(default=None)):
    fields = req.model_fields_set
    if not fields:
        raise HTTPException(status_code=400, detail="변경할 항목이 없습니다")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            role, item = _lock_gear_item(cur, party_id, item_id, user["id"])
            if item["kind"] is not None:
                if role != "owner" and item["created_by_user_id"] != user["id"]:
                    raise HTTPException(status_code=403, detail="파티장이나 등록한 회원만 준비물 설정을 수정할 수 있습니다")
                if item["kind"] == "assigned" and fields == {"assigned_to_username"}:
                    assignee_id = _resolve_gear_assignee(cur, party_id, req.assigned_to_username)
                    _assign_exclusive_gear(cur, party_id, item_id, assignee_id, item["quantity"])
                    conn.commit()
                    return {"message": "준비물 담당자를 변경했습니다"}
                if fields != {"quantity"} or req.quantity is None:
                    raise HTTPException(status_code=400, detail="담당과 완료 상태는 준비물 목록에서 변경해 주세요")
                if item["kind"] == "shared":
                    cur.execute("SELECT COALESCE(SUM(quantity),0) AS total FROM hiking_party_item_members WHERE item_id=%s FOR UPDATE", (item_id,))
                    if cur.fetchone()["total"] > req.quantity:
                        raise HTTPException(status_code=409, detail="먼저 배정 수량을 줄여 주세요")
                cur.execute("UPDATE hiking_party_items SET quantity=%s WHERE id=%s", (req.quantity, item_id))
                if req.quantity != item["quantity"]:
                    if item["kind"] == "personal":
                        cur.execute("DELETE FROM hiking_party_item_members WHERE item_id=%s", (item_id,))
                    if item["kind"] == "assigned":
                        cur.execute("UPDATE hiking_party_item_members SET quantity=%s, is_packed=0 WHERE item_id=%s", (req.quantity, item_id))
                conn.commit()
                return {"message": "준비물 수량을 업데이트했습니다"}
            updates = []
            values = []
            if "quantity" in fields and req.quantity is not None:
                updates.append("quantity = %s")
                values.append(req.quantity)
            if "is_packed" in fields and req.is_packed is not None:
                updates.append("is_packed = %s")
                values.append(1 if req.is_packed else 0)
            if "assigned_to_username" in fields:
                assignee_id = None
                if req.assigned_to_username:
                    cur.execute("""
                        SELECT u.id FROM users u JOIN hiking_party_members m ON m.user_id = u.id
                        WHERE m.party_id = %s AND u.username = %s
                    """, (party_id, req.assigned_to_username.strip()))
                    assignee = cur.fetchone()
                    if not assignee:
                        raise HTTPException(status_code=400, detail="담당자는 현재 파티 구성원이어야 합니다")
                    assignee_id = assignee["id"]
                updates.append("assigned_to_user_id = %s")
                values.append(assignee_id)
            if not updates:
                raise HTTPException(status_code=400, detail="변경할 내용을 입력해 주세요")
            if updates:
                cur.execute(f"UPDATE hiking_party_items SET {', '.join(updates)} WHERE id = %s AND party_id = %s", (*values, item_id, party_id))
        conn.commit()
        return {"message": "공동 준비물을 업데이트했습니다"}
    finally:
        conn.close()

@router.put("/api/hiking/parties/{party_id}/items/{item_id}/allocations/{username}")
def allocate_party_gear(party_id: int, item_id: int, username: str, req: GearAllocationRequest, authorization: str | None = Header(default=None)):
    with get_conn() as conn, conn.cursor() as cur:
        user = _get_token_user(cur, authorization)
        _ensure_hiking_tables(cur)
        role, item = _lock_gear_item(cur, party_id, item_id, user["id"])
        if item["kind"] == "personal":
            raise HTTPException(status_code=400, detail="각자 챙기는 물품에는 담당자를 배정하지 않습니다")
        if item["kind"] == "assigned":
            raise HTTPException(status_code=400, detail="지정한 사람만 챙기는 물품은 담당자 변경을 사용해 주세요")
        cur.execute("""
            SELECT u.id FROM users u JOIN hiking_party_members m ON m.user_id = u.id
            WHERE m.party_id = %s AND u.username = %s FOR UPDATE
        """, (party_id, username))
        target = cur.fetchone()
        if not target:
            raise HTTPException(status_code=400, detail="현재 파티 구성원만 담당할 수 있습니다")
        if target["id"] != user["id"] and role != "owner" and item["created_by_user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="내 담당 수량만 변경할 수 있습니다")
        _adopt_legacy_allocation(cur, item)
        cur.execute("""
            SELECT COALESCE(SUM(quantity),0) AS total FROM hiking_party_item_members
            WHERE item_id=%s AND user_id<>%s FOR UPDATE
        """, (item_id, target["id"]))
        if cur.fetchone()["total"] + req.quantity > item["quantity"]:
            raise HTTPException(status_code=409, detail="배정 수량이 파티 전체 수량보다 많습니다")
        key = (item_id, target["id"])
        if req.quantity == 0:
            cur.execute("DELETE FROM hiking_party_item_members WHERE item_id=%s AND user_id=%s", key)
        else:
            cur.execute("""
                INSERT INTO hiking_party_item_members (item_id,user_id,party_id,quantity) VALUES (%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE is_packed=IF(quantity=%s,is_packed,0), quantity=%s
            """, (*key, party_id, req.quantity, req.quantity, req.quantity))
        conn.commit()
    return {"message": "담당 수량을 저장했습니다"}


@router.put("/api/hiking/parties/{party_id}/items/{item_id}/my-check")
def check_my_party_gear(party_id: int, item_id: int, req: GearCheckRequest, authorization: str | None = Header(default=None)):
    with get_conn() as conn, conn.cursor() as cur:
        user = _get_token_user(cur, authorization)
        _ensure_hiking_tables(cur)
        _, item = _lock_gear_item(cur, party_id, item_id, user["id"])
        key = (item_id, user["id"])
        if item["kind"] == "personal":
            if req.is_packed:
                cur.execute("""
                    INSERT INTO hiking_party_item_members (item_id,user_id,party_id,quantity,is_packed) VALUES (%s,%s,%s,1,1)
                    ON DUPLICATE KEY UPDATE is_packed=1
                """, (*key, party_id))
            else:
                cur.execute("DELETE FROM hiking_party_item_members WHERE item_id=%s AND user_id=%s", key)
        else:
            _adopt_legacy_allocation(cur, item)
            cur.execute("SELECT 1 FROM hiking_party_item_members WHERE item_id=%s AND user_id=%s FOR UPDATE", key)
            if not cur.fetchone():
                raise HTTPException(status_code=403, detail="내게 배정된 준비물만 체크할 수 있습니다")
            cur.execute("UPDATE hiking_party_item_members SET is_packed=%s WHERE item_id=%s AND user_id=%s",
                        (int(req.is_packed), *key))
        conn.commit()
    return {"message": "준비 상태를 저장했습니다"}


@router.delete("/api/hiking/parties/{party_id}/items/{item_id}")
def delete_hiking_party_item(party_id: int, item_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            role = _party_member_role(cur, party_id, user["id"])
            cur.execute("SELECT created_by_user_id FROM hiking_party_items WHERE id = %s AND party_id = %s", (item_id, party_id))
            item = cur.fetchone()
            if not item:
                raise HTTPException(status_code=404, detail="공동 준비물을 찾을 수 없습니다")
            if role != "owner" and item["created_by_user_id"] != user["id"]:
                raise HTTPException(status_code=403, detail="준비물을 추가한 사람 또는 파티장만 삭제할 수 있습니다")
            cur.execute("DELETE FROM hiking_party_items WHERE id = %s", (item_id,))
        conn.commit()
        return {"message": "공동 준비물을 삭제했습니다"}
    finally:
        conn.close()

@router.delete("/api/hiking/parties/{party_id}")
def delete_hiking_party(party_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            _ensure_hiking_tables(cur)
            role = _party_member_role(cur, party_id, user["id"])
            if role != "owner":
                raise HTTPException(status_code=403, detail="파티장만 파티를 삭제할 수 있습니다")
            cur.execute("DELETE FROM hiking_parties WHERE id = %s", (party_id,))
        conn.commit()
        return {"message": "산행 파티를 삭제했습니다"}
    finally:
        conn.close()
