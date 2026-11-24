"""낚시 페이지: 계정별 즐겨찾는 낚시 포인트 저장 (최대 10곳, 고른 순서 유지).

테이블은 처음 쓸 때 자동으로 만듭니다. (migrations/003_user_fishing_points.sql 과 같은 구조)
회원이 탈퇴하면 FOREIGN KEY ... ON DELETE CASCADE 로 함께 지워집니다.
"""
import threading

MAX_POINTS = 10
MAX_NAME_LENGTH = 80

_schema_ready = False
_schema_lock = threading.Lock()


def ensure_table(cur):
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_fishing_points (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                point_name VARCHAR(80) NOT NULL,
                sort_order TINYINT UNSIGNED NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_user_fishing_points_name (user_id, point_name),
                CONSTRAINT fk_user_fishing_points_user FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        _schema_ready = True


def clean_names(names: list[str]) -> list[str]:
    """앞뒤 공백 제거, 중복 제거(처음 것 유지), 길이·개수 검사. 잘못되면 ValueError(사용자용 문구)."""
    cleaned = []
    for raw in names:
        name = str(raw or "").strip()
        if not name or len(name) > MAX_NAME_LENGTH:
            raise ValueError("낚시 포인트 이름을 확인해 주세요.")
        if name not in cleaned:
            cleaned.append(name)
    if len(cleaned) > MAX_POINTS:
        raise ValueError(f"즐겨찾는 포인트는 최대 {MAX_POINTS}곳까지 저장할 수 있습니다.")
    return cleaned


def get_points(cur, user_id: int) -> list[str]:
    ensure_table(cur)
    cur.execute("SELECT point_name FROM user_fishing_points WHERE user_id = %s ORDER BY sort_order", (user_id,))
    return [row["point_name"] for row in cur.fetchall()]


def replace_points(cur, user_id: int, names: list[str]) -> None:
    ensure_table(cur)
    cur.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))  # 동시에 저장해도 순서가 꼬이지 않게
    cur.execute("DELETE FROM user_fishing_points WHERE user_id = %s", (user_id,))
    if names:
        cur.executemany(
            "INSERT INTO user_fishing_points (user_id, point_name, sort_order) VALUES (%s, %s, %s)",
            [(user_id, name, index) for index, name in enumerate(names)],
        )


def apply_point_changes(cur, user_id, additions, removals):
    ensure_table(cur)
    cur.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))
    cur.execute("SELECT point_name FROM user_fishing_points WHERE user_id = %s ORDER BY sort_order FOR UPDATE", (user_id,))
    current = [row["point_name"] for row in cur.fetchall()]
    removed = set(removals)
    names = [name for name in current if name not in removed]
    names.extend(name for name in additions if name not in names and name not in removed)
    names = names[:MAX_POINTS]
    if names != current:
        replace_points(cur, user_id, names)
    return names
