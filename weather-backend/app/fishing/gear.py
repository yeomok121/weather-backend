"""낚시 페이지: 계정별 '내 준비물' 세팅 (직접 추가한 항목 + 숨긴 기본 항목).

로그인하면 어느 기기에서든 같은 세팅을 봅니다. 체크 표시는 출조마다 달라서 브라우저에만 저장합니다(웹 쪽).
테이블은 처음 쓸 때 자동으로 만듭니다. (migrations/004_user_fishing_gear.sql 과 같은 구조)
회원이 탈퇴하면 FOREIGN KEY ... ON DELETE CASCADE 로 함께 지워집니다.
"""
import json
import re
import threading

MAX_ITEMS = 30
MAX_NAME_LENGTH = 30
MAX_NOTE_LENGTH = 60
MAX_HIDDEN = 30
ITEM_ID = re.compile(r"^my-[a-z0-9]{1,24}$")      # 웹에서 만드는 내 항목 id
BASE_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")   # 웹 lib/gear.js BASE_GEAR 의 id
CONTROL = re.compile(r"[\x00-\x1f\x7f]")

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
            CREATE TABLE IF NOT EXISTS user_fishing_gear (
                user_id INT NOT NULL PRIMARY KEY,
                setup_json TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                CONSTRAINT fk_user_fishing_gear_user FOREIGN KEY (user_id)
                    REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        _schema_ready = True


def _text(value, limit: int, label: str, required: bool) -> str:
    text = CONTROL.sub("", str(value or "")).strip()
    if required and not text:
        raise ValueError(f"{label}을(를) 입력해 주세요.")
    if len(text) > limit:
        raise ValueError(f"{label}은(는) {limit}자까지 쓸 수 있습니다.")
    return text


def clean_setup(items: list[dict], hidden: list[str]) -> dict:
    """앞뒤 공백·제어문자 제거, 중복 제거(처음 것 유지), 길이·개수 검사. 잘못되면 ValueError(사용자용 문구)."""
    cleaned, seen_ids, seen_names = [], set(), set()
    for raw in items:
        item_id = str(raw.get("id") or "")
        if not ITEM_ID.match(item_id):
            raise ValueError("준비물 항목을 확인해 주세요.")
        name = _text(raw.get("name"), MAX_NAME_LENGTH, "준비물 이름", True)
        note = _text(raw.get("note"), MAX_NOTE_LENGTH, "메모", False)
        if item_id in seen_ids or name in seen_names:
            continue
        seen_ids.add(item_id)
        seen_names.add(name)
        cleaned.append({"id": item_id, "name": name, "note": note})
    if len(cleaned) > MAX_ITEMS:
        raise ValueError(f"내 준비물은 최대 {MAX_ITEMS}개까지 저장할 수 있습니다.")
    hidden_ids = []
    for raw in hidden:
        value = str(raw or "")
        if not BASE_ID.match(value):
            raise ValueError("숨길 기본 항목을 확인해 주세요.")
        if value not in hidden_ids:
            hidden_ids.append(value)
    if len(hidden_ids) > MAX_HIDDEN:
        raise ValueError("숨길 기본 항목이 너무 많습니다.")
    return {"items": cleaned, "hidden": hidden_ids}


def get_setup(cur, user_id: int) -> dict:
    ensure_table(cur)
    cur.execute("SELECT setup_json, updated_at FROM user_fishing_gear WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if not row:
        return {"items": [], "hidden": [], "updated_at": None}
    try:
        stored = json.loads(row["setup_json"])
        setup = clean_setup(stored.get("items", []), stored.get("hidden", []))
    except (ValueError, TypeError, AttributeError):
        setup = {"items": [], "hidden": []}  # 저장 형식이 깨졌으면 빈 세팅으로
    updated = row.get("updated_at")
    return {**setup, "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else updated}


def save_setup(cur, user_id: int, setup: dict) -> None:
    ensure_table(cur)
    cur.execute(
        "INSERT INTO user_fishing_gear (user_id, setup_json) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE setup_json = VALUES(setup_json)",
        (user_id, json.dumps(setup, ensure_ascii=False)),
    )
