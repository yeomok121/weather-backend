"""낚시 페이지: TideBED 예측 조위를 화면용으로 정리하고 만조·간조 시각을 찾습니다.

TideBED는 1시간 간격 예측값을 주므로, 꼭짓점 앞뒤 세 점으로 포물선을 그려
실제 만조·간조 시각과 높이를 분 단위로 추정합니다. (참고용 추정값)
"""

from datetime import datetime, timedelta

from app.fishing.providers import SEOUL


def _items(raw) -> list[dict]:
    items = (((raw or {}).get("body") or {}).get("items") or {}).get("item") or []
    return items if isinstance(items, list) else [items]


def _parse_time(value) -> datetime | None:
    try:
        return datetime.strptime(str(value).strip()[:16], "%Y-%m-%d %H:%M").replace(tzinfo=SEOUL)
    except ValueError:
        return None


def _parse_height(value) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def normalize_predictions(*raw_responses) -> list[dict]:
    """여러 날짜의 TideBED 응답을 시간순 [{time, height_cm, station}] 목록으로 합칩니다."""
    points = {}
    for raw in raw_responses:
        for item in _items(raw):
            at = _parse_time(item.get("obsrvnDt"))
            height = _parse_height(item.get("obsrvnHgt"))
            if at is None or height is None:
                continue
            points[at] = {"time": at.isoformat(timespec="minutes"), "height_cm": round(height, 1), "station": item.get("obsvtrNm") or ""}
    return [points[key] for key in sorted(points)]


def find_extremes(predictions: list[dict]) -> list[dict]:
    """1시간 간격 예측에서 만조(high)·간조(low) 시각과 높이를 추정합니다."""
    extremes = []
    for i in range(1, len(predictions) - 1):
        before, here, after = (predictions[i + k]["height_cm"] for k in (-1, 0, 1))
        if here > before and here >= after:
            kind = "high"
        elif here < before and here <= after:
            kind = "low"
        else:
            continue
        t_before, t_here, t_after = (datetime.fromisoformat(predictions[i + k]["time"]) for k in (-1, 0, 1))
        if t_here - t_before != timedelta(hours=1) or t_after - t_here != timedelta(hours=1):
            continue  # 자료가 빠진 구간은 계산하지 않습니다.
        curvature = before - 2 * here + after
        offset = (before - after) / (2 * curvature) if curvature else 0.0
        offset = max(-0.5, min(0.5, offset))
        height = here - (before - after) * offset / 4
        at = t_here + timedelta(minutes=round(offset * 60))
        extremes.append({
            "type": kind,
            "time": at.isoformat(timespec="minutes"),
            "height_cm": round(height, 1),
        })
    return extremes
