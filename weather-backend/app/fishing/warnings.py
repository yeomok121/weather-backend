"""낚시 페이지: 기상청 특보현황(wrn_now_data.php)을 해역별 기상특보 목록으로 정리합니다.

응답 한 줄 = 특보구역 하나의 특보 하나입니다. (2026-11-17 실제 응답으로 형식 확인)
  REG_UP, REG_UP_KO, REG_ID, REG_KO, TM_FC, TM_EF, WRN, LVL, CMD, ED_TM, =
  WRN: 풍랑·강풍·호우 등 / LVL: 예비·주의·경보 / CMD: 발표 등 / ED_TM: 해제예고 (예: 21일 오전(09시~12시))
해역마다 areas.py의 warning_regions(특보구역 이름 일부)와 겹치는 특보만 골라 보여줍니다.
"""

from datetime import datetime

from app.fishing.providers import SEOUL

FIELDS = ["REG_UP", "REG_UP_KO", "REG_ID", "REG_KO", "TM_FC", "TM_EF", "WRN", "LVL", "CMD", "ED_TM"]
LEVEL_RANK = {"경보": 0, "주의": 1, "예비": 2}

# 예비특보 발효시각은 시간대를 뜻하는 약속된 시각으로 옵니다. (응답 도움말 6번 항목)
PRELIMINARY_TIMES = {
    "0259": "새벽(00시~03시)", "0559": "새벽(03시~06시)", "0859": "아침(06시~09시)", "1159": "오전(09시~12시)",
    "1459": "낮(12시~15시)", "1759": "늦은 오후(15시~18시)", "2059": "저녁(18시~21시)", "2359": "밤(21시~24시)",
    "1158": "오전(06시~12시)", "1758": "오후(12시~18시)", "0558": "새벽(00시~06시)", "2358": "밤(18시~24시)",
    "1458": "오후(12시~18시)",
}


def parse_warnings(text: str) -> list[dict]:
    if text.lstrip().startswith("{"):
        raise ValueError("warning API returned an error message")  # 예: 활용신청 필요(403) JSON
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cols = [part.strip() for part in line.split(",")]
        if cols and cols[-1] == "=":
            cols = cols[:-1]
        if len(cols) < 9:
            continue
        rows.append(dict(zip(FIELDS, cols + [""] * (len(FIELDS) - len(cols)))))
    return rows


def _iso(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%Y%m%d%H%M").replace(tzinfo=SEOUL).isoformat(timespec="minutes")
    except (TypeError, ValueError):
        return None


def _title(kind: str, level: str) -> str:
    if level == "예비":
        return f"{kind} 예비특보"
    if level == "주의":
        return f"{kind}주의보"
    if level == "경보":
        return f"{kind}경보"
    return f"{kind} {level}".strip()


def _effective_label(row: dict) -> str:
    value = row.get("TM_EF", "")
    if row.get("LVL") == "예비" and len(value) == 12 and value[8:] in PRELIMINARY_TIMES:
        return f"{int(value[6:8])}일 {PRELIMINARY_TIMES[value[8:]]}"
    if len(value) == 12 and value.isdigit():
        return f"{int(value[6:8])}일 {value[8:10]}:{value[10:]}"
    return value


def warnings_for_regions(rows: list[dict], keywords: list[str]) -> list[dict]:
    """특보구역 이름에 keywords 중 하나가 들어간 특보를 심한 순서로."""
    matched = []
    for row in rows:
        region = row.get("REG_KO", "")
        # '인천·경기남부앞바다'와 '인천경기남부앞바다중 평수구역'처럼 가운뎃점 유무가 달라도 같은 구역으로 봅니다.
        plain = region.replace("·", "")
        if row.get("CMD") == "해제" or not any(keyword and keyword.replace("·", "") in plain for keyword in keywords):
            continue
        matched.append({
            "region": region,
            "region_group": row.get("REG_UP_KO", ""),
            "kind": row.get("WRN", ""),
            "level": row.get("LVL", ""),
            "title": _title(row.get("WRN", ""), row.get("LVL", "")),
            "command": row.get("CMD", ""),
            "issued_at": _iso(row.get("TM_FC", "")),
            "effective": _effective_label(row),
            "release_hint": row.get("ED_TM", ""),
        })
    matched.sort(key=lambda item: (LEVEL_RANK.get(item["level"], 9), item["kind"], item["region"]))
    return matched
