"""낚시 페이지: 국립해양조사원 바다낚시지수를 화면용으로 정리합니다.

기관 응답은 전국 포인트 × 날짜 × 오전/오후 × 어종 한 줄씩입니다.
선택한 해역 좌표에서 가장 가까운 포인트를 골라 날짜·시간대별로 묶습니다.
"""

from app.fishing.providers import distance_km

# 좋은 순서 (화면에서 어종을 좋은 지수부터 보여줄 때 사용)
GRADE_ORDER = ["매우좋음", "좋음", "보통", "나쁨", "매우나쁨"]
SESSION_ORDER = {"오전": 0, "오후": 1}
MAX_POINT_DISTANCE_KM = 80


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _range(row, key):
    low, high = _number(row.get(f"min{key}")), _number(row.get(f"max{key}"))
    if low is None and high is None:
        return None
    return {"min": low if low is not None else high, "max": high if high is not None else low}


def _grade_rank(grade: str) -> int:
    return GRADE_ORDER.index(grade) if grade in GRADE_ORDER else len(GRADE_ORDER)


def list_points(items: list[dict], lat: float, lon: float) -> list[dict]:
    """응답에 들어 있는 낚시 포인트를 기준 좌표에서 가까운 순으로."""
    points = {}
    for row in items:
        name = row.get("seafsPstnNm")
        p_lat, p_lon = _number(row.get("lat")), _number(row.get("lot"))
        if not name or p_lat is None or p_lon is None or name in points:
            continue
        points[name] = {"name": name, "lat": p_lat, "lon": p_lon, "distance_km": distance_km(lat, lon, p_lat, p_lon)}
    return sorted(points.values(), key=lambda point: point["distance_km"])


def summarize(items: list[dict], lat: float, lon: float, max_distance_km: float = MAX_POINT_DISTANCE_KM) -> dict:
    """가장 가까운 포인트의 날짜별 오전/오후 낚시지수와 해양 조건."""
    points = list_points(items, lat, lon)
    nearby = points[:3]
    point = points[0] if points and points[0]["distance_km"] <= max_distance_km else None
    if point is None:
        return {"point": None, "nearby": nearby, "days": []}

    sessions = {}
    for row in items:
        if row.get("seafsPstnNm") != point["name"]:
            continue
        key = (row.get("predcYmd") or "", row.get("predcNoonSeCd") or "")
        session = sessions.setdefault(key, {
            "date": key[0], "session": key[1],
            "tide_phase": row.get("tdlvHrCn") or "",
            "wave_m": _range(row, "Wvhgt"), "water_temp_c": _range(row, "Wtem"),
            "air_temp_c": _range(row, "Artmp"), "current_speed": _range(row, "Crsp"),
            "wind_ms": _range(row, "Wspd"), "species": [],
        })
        species = row.get("seafsTgfshNm")
        if species and all(item["name"] != species for item in session["species"]):
            session["species"].append({"name": species, "index": row.get("totalIndex") or ""})

    days = {}
    for (date, _), session in sorted(sessions.items(), key=lambda item: (item[0][0], SESSION_ORDER.get(item[0][1], 9))):
        session["species"].sort(key=lambda item: (_grade_rank(item["index"]), item["name"] == "기타어종", item["name"]))
        days.setdefault(date, []).append(session)
    return {
        "point": point,
        "nearby": nearby,
        "days": [{"date": date, "sessions": day_sessions} for date, day_sessions in days.items()],
    }


def today_grades(items: list[dict], now) -> dict:
    """포인트별 오늘 가장 좋은 지수와 그 시간대. 정오가 지나면 이미 끝난 오전 지수는 빼고 봅니다.
    반환: {포인트 이름: {"grade": "좋음", "session": "오후"}}"""
    today = now.strftime("%Y-%m-%d")
    afternoon = now.hour >= 12
    best = {}
    for row in items:
        name, session, grade = row.get("seafsPstnNm"), row.get("predcNoonSeCd") or "", row.get("totalIndex") or ""
        if not name or row.get("predcYmd") != today or grade not in GRADE_ORDER:
            continue
        if afternoon and session == "오전":
            continue
        current = best.get(name)
        if current is None or _grade_rank(grade) < _grade_rank(current["grade"]):
            best[name] = {"grade": grade, "session": session}
    return best
