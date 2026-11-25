"""낚시 페이지: 낚시 포인트 근처 낚싯배 출항지.

자료: 해양경찰청 '낚시어선 주조업지 출항지별 낚시어선 현황'(공공데이터포털 15110664, 2022-11 기준, 이용허락범위 제한 없음).
원본은 SHP(EPSG:5179 좌표)라서 위경도로 바꿔 ports_data.json에 넣어 두었습니다. 자주 바뀌지 않는 자료라 외부 호출 없이 이 파일만 씁니다.
  ships  그 출항지를 주로 쓰는(주조업지) 낚싯배 수. 0척인 곳은 낚싯배를 찾기 어려워 기본 목록에서 뺍니다.
배 이름·연락처·가격·예약 정보는 원본에 없습니다.
"""
import json
from functools import lru_cache
from pathlib import Path

from app.fishing.providers import distance_km

DATA_FILE = Path(__file__).with_name("ports_data.json")
NEARBY_KM = 50  # 이 거리 안의 출항지만 '근처'로 보여줍니다(섬 포인트는 없을 수 있음)


@lru_cache(maxsize=1)
def load_ports() -> dict:
    with DATA_FILE.open(encoding="utf-8") as file:
        return json.load(file)


def nearby_ports(lat: float, lon: float, limit: int = 3, radius_km: float = NEARBY_KM) -> dict:
    """가까운 출항지(낚싯배 1척 이상) limit곳. 반경 안에 없으면 가장 가까운 한 곳을 nearest로 알려줍니다."""
    data = load_ports()
    ranked = sorted(
        ({**port, "distance_km": distance_km(lat, lon, port["lat"], port["lon"])} for port in data["ports"] if port["ships"] > 0),
        key=lambda port: port["distance_km"],
    )
    within = [port for port in ranked if port["distance_km"] <= radius_km][:limit]
    return {
        "ports": within,
        "nearest": None if within or not ranked else ranked[0],
        "radius_km": radius_km,
        "source": data["source"],
        "source_url": data["source_url"],
        "base_date": data["base_date"],
        "notice": "해양경찰청 출항지 자료(배 수 포함)입니다. 배 이름·연락처·요금·예약은 제공되지 않으니 현지 선사나 낚시점에 확인하세요.",
    }
