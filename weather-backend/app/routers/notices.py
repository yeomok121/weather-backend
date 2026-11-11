"""관리자 페이지 API: 공지사항 (읽기는 누구나, 작성·수정·삭제는 관리자)."""
import threading
from datetime import datetime as _dt

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from app.database import get_conn
from app.routers.admin import require_admin

router = APIRouter()

_NOTICE_TYPES = ("안내", "점검", "긴급")
_notices_schema_ready = False
_notices_schema_lock = threading.Lock()

def _ensure_notices_table(cur):
    global _notices_schema_ready
    if _notices_schema_ready:
        return
    with _notices_schema_lock:
        if _notices_schema_ready:
            return
        cur.execute("""
            CREATE TABLE IF NOT EXISTS notices (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                notice_type VARCHAR(10)  NOT NULL DEFAULT '안내',
                title       VARCHAR(200) NOT NULL,
                notice_date DATE         NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        _notices_schema_ready = True

class NoticeRequest(BaseModel):
    type: str = "안내"
    title: str
    date: str  # YYYY.MM.DD

def _parse_notice(req: NoticeRequest):
    title = req.title.strip()
    if not title or len(title) > 200:
        raise HTTPException(status_code=400, detail="제목은 1~200자로 입력해주세요")
    if req.type not in _NOTICE_TYPES:
        raise HTTPException(status_code=400, detail="분류가 올바르지 않습니다")
    try:
        d = _dt.strptime(req.date.strip(), "%Y.%m.%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="날짜는 YYYY.MM.DD 형식으로 입력해주세요")
    return req.type, title, d

def _serialize_notice(row):
    return {
        "id": row["id"],
        "type": row["notice_type"],
        "title": row["title"],
        "date": row["notice_date"].strftime("%Y.%m.%d"),
        "color": "#e53e3e" if row["notice_type"] == "긴급" else "#3182ce",
    }

@router.get("/api/notices")
def list_notices():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_notices_table(cur)
            cur.execute("SELECT id, notice_type, title, notice_date FROM notices ORDER BY notice_date DESC, id DESC")
            return [_serialize_notice(r) for r in cur.fetchall()]
    finally:
        conn.close()

@router.post("/api/admin/notices")
def admin_create_notice(req: NoticeRequest, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization, 'notices')
    ntype, title, d = _parse_notice(req)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_notices_table(cur)
            cur.execute("INSERT INTO notices (notice_type, title, notice_date) VALUES (%s, %s, %s)", (ntype, title, d))
            new_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return {"message": "공지사항이 등록되었습니다", "id": new_id}

@router.put("/api/admin/notices/{notice_id}")
def admin_update_notice(notice_id: int, req: NoticeRequest, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization, 'notices')
    ntype, title, d = _parse_notice(req)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_notices_table(cur)
            cur.execute("SELECT id FROM notices WHERE id = %s", (notice_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="공지사항을 찾을 수 없습니다")
            cur.execute("UPDATE notices SET notice_type = %s, title = %s, notice_date = %s WHERE id = %s",
                        (ntype, title, d, notice_id))
        conn.commit()
    finally:
        conn.close()
    return {"message": "수정되었습니다"}

@router.delete("/api/admin/notices/{notice_id}")
def admin_delete_notice(notice_id: int, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization, 'notices')
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_notices_table(cur)
            cur.execute("DELETE FROM notices WHERE id = %s", (notice_id,))
        conn.commit()
    finally:
        conn.close()
    return {"message": "삭제되었습니다"}
