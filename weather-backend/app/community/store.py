"""낚시 커뮤니티: MySQL 저장소. 테이블은 처음 쓸 때 자동으로 만듭니다. (migrations/005_community.sql 과 같은 구조)

- 시각은 UTC로 저장하고 ISO 문자열(…Z)로 돌려줍니다.
- 사진은 서버에서 다시 저장한 JPEG(최대 1280px, 위치정보 제거)만 MEDIUMBLOB으로 보관합니다.
- 서로 다른 회원 5명이 신고하면 자동으로 숨깁니다. 관리자는 지울 수 있습니다.
- 댓글: 글쓴이는 마음에 드는 남의 댓글에 하트(author_hearted)를 누를 수 있습니다.
- 회원이 탈퇴하면 글·사진·공감·신고가 함께 지워집니다. (ON DELETE CASCADE)
"""
import threading

AUTO_HIDE_REPORTS = 5

_ready = False
_lock = threading.Lock()

TABLES = [
    """CREATE TABLE IF NOT EXISTS community_posts (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        category VARCHAR(8) NOT NULL,
        title VARCHAR(60) NOT NULL,
        body TEXT NOT NULL,
        point_name VARCHAR(80) NULL,
        species VARCHAR(30) NULL,
        length_cm DECIMAL(5,1) NULL,
        like_count INT NOT NULL DEFAULT 0,
        report_count INT NOT NULL DEFAULT 0,
        hidden TINYINT(1) NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NULL,
        KEY idx_community_posts_list (hidden, category, id),
        KEY idx_community_posts_user (user_id, created_at),
        CONSTRAINT fk_community_posts_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS community_photos (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        post_id INT NOT NULL,
        sort_order TINYINT UNSIGNED NOT NULL,
        mime VARCHAR(20) NOT NULL,
        width SMALLINT UNSIGNED NOT NULL,
        height SMALLINT UNSIGNED NOT NULL,
        data MEDIUMBLOB NOT NULL,
        KEY idx_community_photos_post (post_id, sort_order),
        CONSTRAINT fk_community_photos_post FOREIGN KEY (post_id) REFERENCES community_posts(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS community_likes (
        post_id INT NOT NULL,
        user_id INT NOT NULL,
        PRIMARY KEY (post_id, user_id),
        CONSTRAINT fk_community_likes_post FOREIGN KEY (post_id) REFERENCES community_posts(id) ON DELETE CASCADE,
        CONSTRAINT fk_community_likes_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS community_reports (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        post_id INT NOT NULL,
        user_id INT NOT NULL,
        reason VARCHAR(200) NOT NULL,
        created_at DATETIME NOT NULL,
        UNIQUE KEY uq_community_reports (post_id, user_id),
        CONSTRAINT fk_community_reports_post FOREIGN KEY (post_id) REFERENCES community_posts(id) ON DELETE CASCADE,
        CONSTRAINT fk_community_reports_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS community_post_submissions (
    user_id INT NOT NULL,
    submission_key BINARY(16) NOT NULL,
    payload_digest BINARY(32) NOT NULL,
    post_id INT NULL,
    PRIMARY KEY (user_id, submission_key),
    CONSTRAINT fk_community_submission_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_community_submission_post FOREIGN KEY (post_id) REFERENCES community_posts(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS community_comments (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        post_id INT NOT NULL,
        user_id INT NOT NULL,
        body VARCHAR(500) NOT NULL,
        author_hearted TINYINT(1) NOT NULL DEFAULT 0,
        report_count INT NOT NULL DEFAULT 0,
        hidden TINYINT(1) NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL,
        KEY idx_community_comments_post (post_id, id),
        KEY idx_community_comments_user (user_id, created_at),
        CONSTRAINT fk_community_comments_post FOREIGN KEY (post_id) REFERENCES community_posts(id) ON DELETE CASCADE,
        CONSTRAINT fk_community_comments_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS community_comment_reports (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        comment_id INT NOT NULL,
        user_id INT NOT NULL,
        reason VARCHAR(200) NOT NULL,
        created_at DATETIME NOT NULL,
        UNIQUE KEY uq_community_comment_reports (comment_id, user_id),
        CONSTRAINT fk_community_comment_reports_comment FOREIGN KEY (comment_id) REFERENCES community_comments(id) ON DELETE CASCADE,
        CONSTRAINT fk_community_comment_reports_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


def ensure_tables(cur):
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        for sql in TABLES:
            cur.execute(sql)
        _add_missing_columns(cur)
        _ready = True


# 나중에 더한 칸: 이미 만들어진 실제 DB에도 한 번만 추가합니다. (ADD COLUMN IF NOT EXISTS는 MySQL 8에 없어서 직접 확인)
LATER_COLUMNS = [
    ("community_posts", "updated_at", "ALTER TABLE community_posts ADD COLUMN updated_at DATETIME NULL AFTER created_at"),
    ("community_comments", "report_count", "ALTER TABLE community_comments ADD COLUMN report_count INT NOT NULL DEFAULT 0 AFTER author_hearted"),
    ("community_comments", "hidden", "ALTER TABLE community_comments ADD COLUMN hidden TINYINT(1) NOT NULL DEFAULT 0 AFTER report_count"),
]


def _add_missing_columns(cur):
    for table, column, ddl in LATER_COLUMNS:
        cur.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
                    (table, column))
        if cur.fetchone()["n"] == 0:
            cur.execute(ddl)


def _iso(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(value, "strftime") else value


POST_COLUMNS = ("p.id, p.user_id, p.category, p.title, p.body, p.point_name, p.species, p.length_cm, p.like_count, "
                "p.report_count, p.hidden, p.created_at, p.updated_at, u.name AS author, "
                "(SELECT COUNT(*) FROM community_comments cm WHERE cm.post_id = p.id AND cm.hidden = 0) AS comment_count")


def _attach(cur, rows, viewer):
    """사진 id·내 공감 여부·내 글 여부를 붙여 화면용 모양으로."""
    if not rows:
        return []
    ids = [row["id"] for row in rows]
    marks = ",".join(["%s"] * len(ids))
    cur.execute(f"SELECT id, post_id, width, height FROM community_photos WHERE post_id IN ({marks}) ORDER BY post_id, sort_order", ids)
    photos = {}
    for photo in cur.fetchall():
        photos.setdefault(photo["post_id"], []).append({"id": photo["id"], "width": photo["width"], "height": photo["height"]})
    liked = set()
    if viewer:
        cur.execute(f"SELECT post_id FROM community_likes WHERE user_id = %s AND post_id IN ({marks})", [viewer["id"], *ids])
        liked = {row["post_id"] for row in cur.fetchall()}
    moderator = bool(viewer) and viewer.get("role") in ("superadmin", "member_manager", "notice_manager")
    result = []
    for row in rows:
        item = {
            "id": row["id"], "category": row["category"], "title": row["title"], "body": row["body"],
            "point_name": row["point_name"], "species": row["species"],
            "length_cm": float(row["length_cm"]) if row["length_cm"] is not None else None,
            "author": row["author"] or "회원", "like_count": row["like_count"], "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row.get("updated_at")) if row.get("updated_at") else None,
            "photos": photos.get(row["id"], []), "liked": row["id"] in liked, "comment_count": row.get("comment_count") or 0,
            "mine": bool(viewer) and viewer["id"] == row["user_id"],
        }
        if moderator:
            item["report_count"] = row["report_count"]
        result.append(item)
    return result


def count_recent_posts(cur, user_id):
    cur.execute("SELECT COUNT(*) AS n FROM community_posts WHERE user_id = %s AND created_at >= UTC_TIMESTAMP() - INTERVAL 1 DAY", (user_id,))
    return cur.fetchone()["n"]


def create_post(cur, user_id, post, photos):
    cur.execute(
        "INSERT INTO community_posts (user_id, category, title, body, point_name, species, length_cm, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP())",
        (user_id, post["category"], post["title"], post["body"], post["point_name"], post["species"], post["length_cm"]),
    )
    post_id = cur.lastrowid
    for order, photo in enumerate(photos):
        cur.execute("INSERT INTO community_photos (post_id, sort_order, mime, width, height, data) VALUES (%s, %s, %s, %s, %s, %s)",
                    (post_id, order, photo["mime"], photo["width"], photo["height"], photo["bytes"]))
    return post_id


def list_posts(cur, viewer=None, category=None, before_id=None, limit=20):
    """공지(최근 5개)는 따로, 나머지는 최신순 limit개. before_id로 다음 쪽."""
    cur.execute(f"SELECT {POST_COLUMNS} FROM community_posts p LEFT JOIN users u ON u.id = p.user_id "
                "WHERE p.hidden = 0 AND p.category = '공지' ORDER BY p.id DESC LIMIT 5")
    notices = cur.fetchall()
    where, params = ["p.hidden = 0", "p.category <> '공지'"], []
    if category:
        where.append("p.category = %s")
        params.append(category)
    if before_id:
        where.append("p.id < %s")
        params.append(before_id)
    cur.execute(f"SELECT {POST_COLUMNS} FROM community_posts p LEFT JOIN users u ON u.id = p.user_id "
                f"WHERE {' AND '.join(where)} ORDER BY p.id DESC LIMIT %s", [*params, limit + 1])
    rows = cur.fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    return {"notices": _attach(cur, notices, viewer), "posts": _attach(cur, rows, viewer),
            "next_before": rows[-1]["id"] if more and rows else None}


def get_post(cur, post_id, viewer=None):
    cur.execute(f"SELECT {POST_COLUMNS} FROM community_posts p LEFT JOIN users u ON u.id = p.user_id WHERE p.id = %s", (post_id,))
    row = cur.fetchone()
    if not row:
        return None, None
    moderator = bool(viewer) and viewer.get("role") in ("superadmin", "member_manager", "notice_manager")
    if row["hidden"] and not moderator and not (viewer and viewer["id"] == row["user_id"]):
        return None, None
    return _attach(cur, [row], viewer)[0], row["user_id"]


def post_photo_ids(cur, post_id):
    cur.execute("SELECT id FROM community_photos WHERE post_id = %s ORDER BY sort_order", (post_id,))
    return [row["id"] for row in cur.fetchall()]


def update_post(cur, post_id, post, keep_photo_ids, new_photos):
    """글 고치기: 남길 사진(keep, 이 순서대로) + 새 사진(뒤에)으로 다시 정렬하고 수정 시각을 남깁니다."""
    cur.execute(
        "UPDATE community_posts SET category = %s, title = %s, body = %s, point_name = %s, species = %s, length_cm = %s, "
        "updated_at = UTC_TIMESTAMP() WHERE id = %s",
        (post["category"], post["title"], post["body"], post["point_name"], post["species"], post["length_cm"], post_id),
    )
    existing = post_photo_ids(cur, post_id)
    gone = [pid for pid in existing if pid not in keep_photo_ids]
    if gone:
        marks = ",".join(["%s"] * len(gone))
        cur.execute(f"DELETE FROM community_photos WHERE post_id = %s AND id IN ({marks})", [post_id, *gone])
    for order, pid in enumerate(keep_photo_ids):
        cur.execute("UPDATE community_photos SET sort_order = %s WHERE id = %s AND post_id = %s", (order, pid, post_id))
    for order, photo in enumerate(new_photos, start=len(keep_photo_ids)):
        cur.execute("INSERT INTO community_photos (post_id, sort_order, mime, width, height, data) VALUES (%s, %s, %s, %s, %s, %s)",
                    (post_id, order, photo["mime"], photo["width"], photo["height"], photo["bytes"]))


def delete_post(cur, post_id):
    cur.execute("DELETE FROM community_posts WHERE id = %s", (post_id,))


def toggle_like(cur, post_id, user_id):
    cur.execute("SELECT id FROM community_posts WHERE id = %s AND hidden = 0 FOR UPDATE", (post_id,))
    if not cur.fetchone():
        return None
    cur.execute("DELETE FROM community_likes WHERE post_id = %s AND user_id = %s", (post_id, user_id))
    liked = cur.rowcount == 0
    if liked:
        cur.execute("INSERT INTO community_likes (post_id, user_id) VALUES (%s, %s)", (post_id, user_id))
    cur.execute("UPDATE community_posts SET like_count = (SELECT COUNT(*) FROM community_likes WHERE post_id = %s) WHERE id = %s", (post_id, post_id))
    cur.execute("SELECT like_count FROM community_posts WHERE id = %s", (post_id,))
    return {"liked": liked, "like_count": cur.fetchone()["like_count"]}


def report_post(cur, post_id, user_id, reason):
    cur.execute("SELECT id FROM community_posts WHERE id = %s FOR UPDATE", (post_id,))
    if not cur.fetchone():
        return None
    cur.execute("INSERT IGNORE INTO community_reports (post_id, user_id, reason, created_at) VALUES (%s, %s, %s, UTC_TIMESTAMP())",
                (post_id, user_id, reason))
    first = cur.rowcount == 1
    cur.execute("UPDATE community_posts SET report_count = (SELECT COUNT(*) FROM community_reports WHERE post_id = %s), "
                "hidden = IF((SELECT COUNT(*) FROM community_reports WHERE post_id = %s) >= %s, 1, hidden) WHERE id = %s",
                (post_id, post_id, AUTO_HIDE_REPORTS, post_id))
    return {"reported": True, "first_time": first}


def get_photo(cur, photo_id):
    cur.execute("SELECT ph.mime, ph.data, p.hidden FROM community_photos ph JOIN community_posts p ON p.id = ph.post_id WHERE ph.id = %s", (photo_id,))
    row = cur.fetchone()
    if not row or row["hidden"]:
        return None
    return {"mime": row["mime"], "bytes": bytes(row["data"])}


REPORT_FILTERS = {"all": "", "hidden": " AND p.hidden = 1", "visible": " AND p.hidden = 0"}


def list_reported_posts(cur, status="all", page=1, per_page=20):
    """관리자 신고 게시판: 신고가 1건 이상인 글, 최근 신고 순. 신고 사유(최근 20건)와 첫 사진 id를 함께."""
    where = "p.report_count > 0" + REPORT_FILTERS.get(status, "")
    cur.execute(f"SELECT COUNT(*) AS n FROM community_posts p WHERE {where}")
    total = cur.fetchone()["n"]
    cur.execute(
        "SELECT p.id, p.category, p.title, p.hidden, p.report_count, p.created_at, u.name AS author, u.username AS author_username, "
        "(SELECT MAX(r.created_at) FROM community_reports r WHERE r.post_id = p.id) AS last_reported_at, "
        "(SELECT MIN(ph.id) FROM community_photos ph WHERE ph.post_id = p.id) AS photo_id, "
        "(SELECT COUNT(*) FROM community_photos ph WHERE ph.post_id = p.id) AS photo_count "
        f"FROM community_posts p LEFT JOIN users u ON u.id = p.user_id WHERE {where} "
        "ORDER BY last_reported_at DESC, p.id DESC LIMIT %s OFFSET %s",
        (per_page, (max(1, page) - 1) * per_page),
    )
    rows = cur.fetchall()
    reasons = {}
    if rows:
        ids = [row["id"] for row in rows]
        marks = ",".join(["%s"] * len(ids))
        cur.execute(f"SELECT r.post_id, r.reason, r.created_at, u.name AS reporter FROM community_reports r LEFT JOIN users u ON u.id = r.user_id "
                    f"WHERE r.post_id IN ({marks}) ORDER BY r.created_at DESC", ids)
        for row in cur.fetchall():
            bucket = reasons.setdefault(row["post_id"], [])
            if len(bucket) < 20:
                bucket.append({"reason": row["reason"], "reporter": row["reporter"] or "회원", "created_at": _iso(row["created_at"])})
    items = [{
        "id": row["id"], "category": row["category"], "title": row["title"], "hidden": bool(row["hidden"]),
        "report_count": row["report_count"], "author": row["author"] or "회원", "author_username": row["author_username"] or "",
        "created_at": _iso(row["created_at"]), "last_reported_at": _iso(row["last_reported_at"]),
        "photo_id": row["photo_id"], "photo_count": row["photo_count"], "reasons": reasons.get(row["id"], []),
    } for row in rows]
    return {"items": items, "total": total, "page": max(1, page), "per_page": per_page}


def post_author(cur, post_id):
    cur.execute("SELECT p.user_id, u.username FROM community_posts p LEFT JOIN users u ON u.id = p.user_id WHERE p.id = %s", (post_id,))
    return cur.fetchone()


def restore_post(cur, post_id):
    """신고 초기화: 신고 기록을 지우고 다시 보이게"""
    cur.execute("DELETE FROM community_reports WHERE post_id = %s", (post_id,))
    cur.execute("UPDATE community_posts SET report_count = 0, hidden = 0 WHERE id = %s", (post_id,))


# ── 댓글 ──
def list_comments(cur, post_id, viewer=None, post_owner_id=None):
    cur.execute("SELECT c.id, c.user_id, c.body, c.author_hearted, c.report_count, c.hidden, c.created_at, u.name AS author FROM community_comments c "
                "LEFT JOIN users u ON u.id = c.user_id WHERE c.post_id = %s ORDER BY c.id", (post_id,))
    viewer_id = viewer["id"] if viewer else None
    moderator = bool(viewer) and viewer.get("role") in ("superadmin", "member_manager", "notice_manager")
    result = []
    for row in cur.fetchall():
        mine = viewer_id is not None and viewer_id == row["user_id"]
        if row["hidden"] and not (mine or moderator):
            continue  # 신고로 숨겨진 댓글은 쓴 사람과 관리자에게만
        item = {
            "id": row["id"], "body": row["body"], "author": row["author"] or "회원", "created_at": _iso(row["created_at"]),
            "hearted": bool(row["author_hearted"]),
            "by_post_author": row["user_id"] == post_owner_id,          # 글쓴이가 단 댓글
            "mine": mine,
            "hidden": bool(row["hidden"]),
            "can_delete": mine or moderator,
            "can_heart": viewer_id is not None and viewer_id == post_owner_id and not mine,  # 글쓴이가 남의 댓글에만
            "can_report": viewer_id is not None and not mine,
        }
        if moderator:
            item["report_count"] = row["report_count"]
        result.append(item)
    return result


def count_recent_comments(cur, user_id):
    cur.execute("SELECT COUNT(*) AS n FROM community_comments WHERE user_id = %s AND created_at >= UTC_TIMESTAMP() - INTERVAL 1 DAY", (user_id,))
    return cur.fetchone()["n"]


def add_comment(cur, post_id, user_id, body):
    cur.execute("INSERT INTO community_comments (post_id, user_id, body, created_at) VALUES (%s, %s, %s, UTC_TIMESTAMP())", (post_id, user_id, body))
    return cur.lastrowid


def get_comment(cur, comment_id):
    """댓글과 그 글의 글쓴이·숨김 여부"""
    cur.execute("SELECT c.id, c.post_id, c.user_id, c.author_hearted, c.hidden AS comment_hidden, p.user_id AS post_owner_id, p.hidden FROM community_comments c "
                "JOIN community_posts p ON p.id = c.post_id WHERE c.id = %s", (comment_id,))
    return cur.fetchone()


def delete_comment(cur, comment_id):
    cur.execute("DELETE FROM community_comments WHERE id = %s", (comment_id,))


def set_comment_heart(cur, comment_id, hearted):
    cur.execute("UPDATE community_comments SET author_hearted = %s WHERE id = %s", (1 if hearted else 0, comment_id))


# ── 댓글 신고 ──
def report_comment(cur, comment_id, user_id, reason):
    cur.execute("INSERT IGNORE INTO community_comment_reports (comment_id, user_id, reason, created_at) VALUES (%s, %s, %s, UTC_TIMESTAMP())",
                (comment_id, user_id, reason))
    first = cur.rowcount == 1
    cur.execute("UPDATE community_comments SET report_count = (SELECT COUNT(*) FROM community_comment_reports WHERE comment_id = %s), "
                "hidden = IF((SELECT COUNT(*) FROM community_comment_reports WHERE comment_id = %s) >= %s, 1, hidden) WHERE id = %s",
                (comment_id, comment_id, AUTO_HIDE_REPORTS, comment_id))
    return first


def list_reported_comments(cur, status="all", page=1, per_page=20):
    """관리자 신고 게시판(댓글): 신고가 1건 이상인 댓글, 최근 신고 순. 어느 글의 댓글인지와 신고 사유(최근 20건)."""
    where = "c.report_count > 0" + {"all": "", "hidden": " AND c.hidden = 1", "visible": " AND c.hidden = 0"}.get(status, "")
    cur.execute(f"SELECT COUNT(*) AS n FROM community_comments c WHERE {where}")
    total = cur.fetchone()["n"]
    cur.execute(
        "SELECT c.id, c.body, c.hidden, c.report_count, c.created_at, c.post_id, p.title AS post_title, u.name AS author, u.username AS author_username, "
        "(SELECT MAX(r.created_at) FROM community_comment_reports r WHERE r.comment_id = c.id) AS last_reported_at "
        f"FROM community_comments c JOIN community_posts p ON p.id = c.post_id LEFT JOIN users u ON u.id = c.user_id WHERE {where} "
        "ORDER BY last_reported_at DESC, c.id DESC LIMIT %s OFFSET %s",
        (per_page, (max(1, page) - 1) * per_page),
    )
    rows = cur.fetchall()
    reasons = {}
    if rows:
        ids = [row["id"] for row in rows]
        marks = ",".join(["%s"] * len(ids))
        cur.execute(f"SELECT r.comment_id, r.reason, r.created_at, u.name AS reporter FROM community_comment_reports r LEFT JOIN users u ON u.id = r.user_id "
                    f"WHERE r.comment_id IN ({marks}) ORDER BY r.created_at DESC", ids)
        for row in cur.fetchall():
            bucket = reasons.setdefault(row["comment_id"], [])
            if len(bucket) < 20:
                bucket.append({"reason": row["reason"], "reporter": row["reporter"] or "회원", "created_at": _iso(row["created_at"])})
    items = [{
        "id": row["id"], "body": row["body"], "hidden": bool(row["hidden"]), "report_count": row["report_count"],
        "author": row["author"] or "회원", "author_username": row["author_username"] or "", "created_at": _iso(row["created_at"]),
        "post_id": row["post_id"], "post_title": row["post_title"], "last_reported_at": _iso(row["last_reported_at"]),
        "reasons": reasons.get(row["id"], []),
    } for row in rows]
    return {"items": items, "total": total, "page": max(1, page), "per_page": per_page}


def comment_author(cur, comment_id):
    cur.execute("SELECT c.user_id, u.username FROM community_comments c LEFT JOIN users u ON u.id = c.user_id WHERE c.id = %s", (comment_id,))
    return cur.fetchone()


def restore_comment(cur, comment_id):
    cur.execute("DELETE FROM community_comment_reports WHERE comment_id = %s", (comment_id,))
    cur.execute("UPDATE community_comments SET report_count = 0, hidden = 0 WHERE id = %s", (comment_id,))
