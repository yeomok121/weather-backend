"""낚시 커뮤니티 API — 사진으로 조과 자랑(자랑), 낚시 팁(팁), 관리자 공지(공지).

읽기는 누구나, 쓰기·공감·신고는 로그인한 회원. 공지는 최고 관리자·공지 관리자만.
글 입력 검사·사진 처리는 app/community/rules.py, 저장은 app/community/store.py
"""
from fastapi import APIRouter, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.community import rules, store
from app.core.auth import _get_token_user
from app.database import get_conn

router = APIRouter()


class PostRequest(BaseModel):
    category: str
    title: str
    body: str = ""
    point_name: str | None = None
    species: str | None = None
    length_cm: float | str | None = None
    photos: list[str] = Field(default_factory=list)


class ReportRequest(BaseModel):
    reason: str = ""


def _viewer(cur, authorization):
    """읽기 요청: 로그인 정보가 맞으면 회원, 없거나 만료면 손님(None)으로 봅니다."""
    if not authorization:
        return None
    try:
        return _get_token_user(cur, authorization)
    except HTTPException:
        return None


@router.get("/api/community/posts")
def list_community_posts(category: str = "", before: int | None = Query(default=None, ge=1), limit: int = Query(default=20, ge=1, le=50),
                         authorization: str | None = Header(default=None)):
    if category and category not in rules.WRITER_CATEGORIES:
        raise HTTPException(status_code=400, detail="분류를 확인해 주세요.")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            store.ensure_tables(cur)
            return store.list_posts(cur, _viewer(cur, authorization), category or None, before, limit)
    finally:
        conn.close()


@router.get("/api/community/posts/{post_id}")
def get_community_post(post_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            store.ensure_tables(cur)
            post, _ = store.get_post(cur, post_id, _viewer(cur, authorization))
    finally:
        conn.close()
    if not post:
        raise HTTPException(status_code=404, detail="글을 찾을 수 없어요. 지워졌거나 숨겨진 글일 수 있어요.")
    return post


@router.post("/api/community/posts", status_code=201)
def create_community_post(req: PostRequest, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            try:
                post = rules.clean_post(req.model_dump(), user["role"])
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            store.ensure_tables(cur)
            if post["category"] != "공지" and store.count_recent_posts(cur, user["id"]) >= rules.DAILY_POST_LIMIT:
                raise HTTPException(status_code=429, detail=f"글은 하루에 {rules.DAILY_POST_LIMIT}개까지 쓸 수 있어요.")
            try:  # 사진 처리는 무거우니 한도 확인 뒤에
                photos = [rules.process_photo(photo) for photo in post["photos"]]
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            post_id = store.create_post(cur, user["id"], post, photos)
            created, _ = store.get_post(cur, post_id, user)
        conn.commit()
    finally:
        conn.close()
    return created


@router.delete("/api/community/posts/{post_id}")
def delete_community_post(post_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            store.ensure_tables(cur)
            post, owner_id = store.get_post(cur, post_id, user)
            if not post:
                raise HTTPException(status_code=404, detail="글을 찾을 수 없어요.")
            if not rules.can_delete(owner_id, user):
                raise HTTPException(status_code=403, detail="내가 쓴 글만 지울 수 있어요.")
            store.delete_post(cur, post_id)
        conn.commit()
    finally:
        conn.close()
    return {"message": "글을 지웠어요."}


@router.post("/api/community/posts/{post_id}/like")
def like_community_post(post_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            store.ensure_tables(cur)
            result = store.toggle_like(cur, post_id, user["id"])
            if result is None:
                raise HTTPException(status_code=404, detail="글을 찾을 수 없어요.")
        conn.commit()
    finally:
        conn.close()
    return result


@router.post("/api/community/posts/{post_id}/report")
def report_community_post(post_id: int, req: ReportRequest, authorization: str | None = Header(default=None)):
    reason = " ".join(str(req.reason or "").split())[:200] or "사유 없음"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            store.ensure_tables(cur)
            result = store.report_post(cur, post_id, user["id"], reason)
            if result is None:
                raise HTTPException(status_code=404, detail="글을 찾을 수 없어요.")
        conn.commit()
    finally:
        conn.close()
    return {"message": "신고를 받았어요. 관리자가 확인할게요." if result["first_time"] else "이미 신고한 글이에요."}


@router.get("/api/community/photos/{photo_id}")
def get_community_photo(photo_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            store.ensure_tables(cur)
            photo = store.get_photo(cur, photo_id)
    finally:
        conn.close()
    if not photo:
        raise HTTPException(status_code=404, detail="사진을 찾을 수 없어요.")
    # 사진 내용은 바뀌지 않지만, 글이 숨겨지거나 지워질 수 있어 하루만 보관하게 합니다.
    return Response(content=photo["bytes"], media_type=photo["mime"], headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/admin/community/reports")
def list_community_reports(authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            if user["role"] not in rules.MODERATOR_ROLES:
                raise HTTPException(status_code=403, detail="관리자 권한이 없습니다")
            store.ensure_tables(cur)
            rows = store.list_reports(cur)
    finally:
        conn.close()
    return {"reports": rows}
