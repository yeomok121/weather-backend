"""낚시 커뮤니티 API — 사진으로 조과 자랑(자랑), 낚시 팁(팁), 관리자 공지(공지).

읽기는 누구나, 쓰기·공감·신고는 로그인한 회원. 공지는 최고 관리자·공지 관리자만.
글 입력 검사·사진 처리는 app/community/rules.py, 저장은 app/community/store.py
"""
from hashlib import sha256
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.community import rules, store
from app.core.auth import _get_token_user
from app.database import get_conn
from app.routers.admin import require_admin
from app.core.schema import _ensure_admin_tables

router = APIRouter()


class PostRequest(BaseModel):
    category: str
    title: str
    body: str = ""
    point_name: str | None = None
    species: str | None = None
    length_cm: float | str | None = None
    photos: list[str] = Field(default_factory=list)


class EditRequest(PostRequest):
    keep_photo_ids: list[int] = Field(default_factory=list)  # 남길 기존 사진 (이 순서대로), photos는 새로 더할 사진


class CommentRequest(BaseModel):
    body: str


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
def create_community_post(req: PostRequest, authorization: str | None = Header(default=None),
                          idempotency_key: UUID | None = Header(default=None)):
    fingerprint = sha256(req.model_dump_json().encode()).digest() if idempotency_key else None
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            store.ensure_tables(cur)
            user = _get_token_user(cur, authorization)
            try:
                post = rules.clean_post(req.model_dump(), user["role"])
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            if idempotency_key:
                cur.execute("SELECT id FROM users WHERE id=%s FOR UPDATE", (user["id"],))
                cur.execute("""
                    SELECT payload_digest, post_id FROM community_post_submissions
                    WHERE user_id=%s AND submission_key=%s FOR UPDATE
                """, (user["id"], idempotency_key.bytes))
                previous = cur.fetchone()
                if previous:
                    if previous["payload_digest"] != fingerprint or not previous["post_id"]:
                        raise HTTPException(status_code=409, detail="이미 처리된 제출입니다. 글 목록을 확인해 주세요")
                    conn.commit()
                    created, _ = store.get_post(cur, previous["post_id"], user)
                    if not created:
                        raise HTTPException(status_code=409, detail="이미 처리된 제출입니다. 글 목록을 확인해 주세요")
                    return created
            if post["category"] != "공지" and store.count_recent_posts(cur, user["id"]) >= rules.DAILY_POST_LIMIT:
                raise HTTPException(status_code=429, detail=f"글은 하루에 {rules.DAILY_POST_LIMIT}개까지 쓸 수 있어요.")
            try:  # 사진 처리는 무거우니 한도 확인 뒤에
                photos = [rules.process_photo(photo) for photo in post["photos"]]
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            post_id = store.create_post(cur, user["id"], post, photos)
            if idempotency_key:
                cur.execute("""
                    INSERT INTO community_post_submissions (user_id, submission_key, payload_digest, post_id)
                    VALUES (%s,%s,%s,%s)
                """, (user["id"], idempotency_key.bytes, fingerprint, post_id))
            created, _ = store.get_post(cur, post_id, user)
        conn.commit()
    finally:
        conn.close()
    return created


@router.put("/api/community/posts/{post_id}")
def edit_community_post(post_id: int, req: EditRequest, authorization: str | None = Header(default=None)):
    """내 글 고치기 (글쓴이만). 금어기·금지체장 확인은 웹에서, 형식·권한·사진 처리는 여기서 다시 합니다."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            store.ensure_tables(cur)
            post, owner_id = store.get_post(cur, post_id, user)
            if not post:
                raise HTTPException(status_code=404, detail="글을 찾을 수 없어요.")
            if owner_id != user["id"]:
                raise HTTPException(status_code=403, detail="내가 쓴 글만 고칠 수 있어요.")
            existing = store.post_photo_ids(cur, post_id)
            keep = list(dict.fromkeys(req.keep_photo_ids))
            if any(pid not in existing for pid in keep):
                raise HTTPException(status_code=400, detail="사진 정보를 확인해 주세요. 새로고침 후 다시 시도해 주세요.")
            data = req.model_dump()
            data["photos"] = [*keep, *req.photos]  # 개수·필수 확인은 남길 사진과 새 사진을 합쳐서
            try:
                cleaned = rules.clean_post(data, user["role"])
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            try:
                new_photos = [rules.process_photo(photo) for photo in req.photos]
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            store.update_post(cur, post_id, cleaned, keep, new_photos)
            updated, _ = store.get_post(cur, post_id, user)
        conn.commit()
    finally:
        conn.close()
    return updated


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


# ── 댓글 ── 읽기는 누구나, 쓰기는 로그인. 글쓴이는 마음에 드는 남의 댓글에 하트를 누를 수 있습니다.
def _visible_post(cur, post_id, viewer):
    post, owner_id = store.get_post(cur, post_id, viewer)
    if not post:
        raise HTTPException(status_code=404, detail="글을 찾을 수 없어요. 지워졌거나 숨겨진 글일 수 있어요.")
    return post, owner_id


@router.get("/api/community/posts/{post_id}/comments")
def list_community_comments(post_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            store.ensure_tables(cur)
            viewer = _viewer(cur, authorization)
            _, owner_id = _visible_post(cur, post_id, viewer)
            return {"comments": store.list_comments(cur, post_id, viewer, owner_id)}
    finally:
        conn.close()


@router.post("/api/community/posts/{post_id}/comments", status_code=201)
def add_community_comment(post_id: int, req: CommentRequest, authorization: str | None = Header(default=None)):
    try:
        body = rules.clean_comment(req.body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            store.ensure_tables(cur)
            _, owner_id = _visible_post(cur, post_id, user)
            if store.count_recent_comments(cur, user["id"]) >= rules.DAILY_COMMENT_LIMIT:
                raise HTTPException(status_code=429, detail=f"댓글은 하루에 {rules.DAILY_COMMENT_LIMIT}개까지 쓸 수 있어요.")
            store.add_comment(cur, post_id, user["id"], body)
            comments = store.list_comments(cur, post_id, user, owner_id)
        conn.commit()
    finally:
        conn.close()
    return {"comments": comments}


@router.delete("/api/community/comments/{comment_id}")
def delete_community_comment(comment_id: int, authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            store.ensure_tables(cur)
            comment = store.get_comment(cur, comment_id)
            if not comment:
                raise HTTPException(status_code=404, detail="댓글을 찾을 수 없어요.")
            if not rules.can_delete(comment["user_id"], user):
                raise HTTPException(status_code=403, detail="내가 쓴 댓글만 지울 수 있어요.")
            store.delete_comment(cur, comment_id)
            comments = store.list_comments(cur, comment["post_id"], user, comment["post_owner_id"])
        conn.commit()
    finally:
        conn.close()
    return {"comments": comments}


@router.post("/api/community/comments/{comment_id}/heart")
def heart_community_comment(comment_id: int, authorization: str | None = Header(default=None)):
    """글쓴이 하트: 글쓴이만, 남의 댓글에만. 다시 누르면 취소."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            store.ensure_tables(cur)
            comment = store.get_comment(cur, comment_id)
            if not comment or (comment["hidden"] and comment["post_owner_id"] != user["id"]):
                raise HTTPException(status_code=404, detail="댓글을 찾을 수 없어요.")
            if comment["post_owner_id"] != user["id"]:
                raise HTTPException(status_code=403, detail="글쓴이만 하트를 누를 수 있어요.")
            if comment["user_id"] == user["id"]:
                raise HTTPException(status_code=400, detail="내 댓글에는 하트를 누를 수 없어요.")
            if comment["comment_hidden"]:
                raise HTTPException(status_code=400, detail="신고로 숨겨진 댓글이에요.")
            store.set_comment_heart(cur, comment_id, not comment["author_hearted"])
            comments = store.list_comments(cur, comment["post_id"], user, comment["post_owner_id"])
        conn.commit()
    finally:
        conn.close()
    return {"comments": comments}


@router.post("/api/community/comments/{comment_id}/report")
def report_community_comment(comment_id: int, req: ReportRequest, authorization: str | None = Header(default=None)):
    """댓글 신고: 로그인한 회원이 남의 댓글에 한 번씩. 5명이 신고하면 자동으로 숨김 (쓴 사람·관리자에게만 보임)."""
    reason = " ".join(str(req.reason or "").split())[:200] or "사유 없음"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            user = _get_token_user(cur, authorization)
            store.ensure_tables(cur)
            comment = store.get_comment(cur, comment_id)
            if not comment or comment["hidden"]:
                raise HTTPException(status_code=404, detail="댓글을 찾을 수 없어요.")
            if comment["user_id"] == user["id"]:
                raise HTTPException(status_code=400, detail="내 댓글은 신고할 수 없어요.")
            first = store.report_comment(cur, comment_id, user["id"], reason)
        conn.commit()
    finally:
        conn.close()
    return {"message": "신고를 받았어요. 관리자가 확인할게요." if first else "이미 신고한 댓글이에요."}


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


# ── 관리자: 신고 게시판 (관리자 계정 또는 공용 관리자 비밀번호) ──
ADMIN_VIEWER = {"id": 0, "role": "superadmin"}  # 숨겨진 글·신고 수까지 보는 관리자 시점


def _audit(cur, actor, action, author):
    _ensure_admin_tables(cur)  # 공용 비밀번호로 들어온 경우에도 기록 테이블 준비
    if author:
        cur.execute("INSERT INTO admin_audit_log (actor, action, target_id, target_username) VALUES (%s, %s, %s, %s)",
                    (actor["name"], action, author["user_id"], author["username"] or ""))


@router.get("/api/admin/community/reports")
def list_community_reports(status: str = Query(default="all", pattern="^(all|hidden|visible)$"), page: int = Query(default=1, ge=1),
                           x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            store.ensure_tables(cur)
            return store.list_reported_posts(cur, status, page)
    finally:
        conn.close()


@router.get("/api/admin/community/posts/{post_id}")
def admin_community_post(post_id: int, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            store.ensure_tables(cur)
            post, _ = store.get_post(cur, post_id, ADMIN_VIEWER)
    finally:
        conn.close()
    if not post:
        raise HTTPException(status_code=404, detail="글을 찾을 수 없어요.")
    post["mine"] = False
    return post


@router.post("/api/admin/community/posts/{post_id}/restore")
def admin_restore_community_post(post_id: int, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            store.ensure_tables(cur)
            author = store.post_author(cur, post_id)
            if not author:
                raise HTTPException(status_code=404, detail="글을 찾을 수 없어요.")
            store.restore_post(cur, post_id)
            _audit(cur, actor, "community_restore", author)
        conn.commit()
    finally:
        conn.close()
    return {"message": "신고를 초기화하고 글을 다시 보이게 했어요."}


@router.delete("/api/admin/community/posts/{post_id}")
def admin_delete_community_post(post_id: int, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            store.ensure_tables(cur)
            author = store.post_author(cur, post_id)
            if not author:
                raise HTTPException(status_code=404, detail="글을 찾을 수 없어요.")
            store.delete_post(cur, post_id)
            _audit(cur, actor, "community_delete", author)
        conn.commit()
    finally:
        conn.close()
    return {"message": "글을 지웠어요."}


# ── 관리자: 댓글 신고 ──
@router.get("/api/admin/community/comment-reports")
def list_community_comment_reports(status: str = Query(default="all", pattern="^(all|hidden|visible)$"), page: int = Query(default=1, ge=1),
                                   x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    require_admin(x_admin_password, authorization)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            store.ensure_tables(cur)
            return store.list_reported_comments(cur, status, page)
    finally:
        conn.close()


@router.post("/api/admin/community/comments/{comment_id}/restore")
def admin_restore_community_comment(comment_id: int, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            store.ensure_tables(cur)
            author = store.comment_author(cur, comment_id)
            if not author:
                raise HTTPException(status_code=404, detail="댓글을 찾을 수 없어요.")
            store.restore_comment(cur, comment_id)
            _audit(cur, actor, "community_comment_restore", author)
        conn.commit()
    finally:
        conn.close()
    return {"message": "댓글 신고를 초기화하고 다시 보이게 했어요."}


@router.delete("/api/admin/community/comments/{comment_id}")
def admin_delete_community_comment(comment_id: int, x_admin_password: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    actor = require_admin(x_admin_password, authorization)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            store.ensure_tables(cur)
            author = store.comment_author(cur, comment_id)
            if not author:
                raise HTTPException(status_code=404, detail="댓글을 찾을 수 없어요.")
            store.delete_comment(cur, comment_id)
            _audit(cur, actor, "community_comment_delete", author)
        conn.commit()
    finally:
        conn.close()
    return {"message": "댓글을 지웠어요."}
