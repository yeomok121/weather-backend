"""낚시 페이지: 선택할 수 있는 해역 목록 (이 파일 한 곳에서만 관리합니다).

프론트엔드는 /api/fishing/areas 로 이 목록을 받아 화면에 보여줍니다.
해역을 추가할 때는 아래 목록에 한 줄만 추가하면 됩니다.
  id          주소·요청에 쓰는 고유 이름 (영문)
  place       화면에 보이는 이름
  region      기상청 단기 해상예보 구역 코드
  region_name 예보 구역 이름
  lat, lon    부이 거리 계산과 조위 예측에 쓰는 기준 좌표
  tide_system 물때 계산 방식: 7(서해안에서 주로 쓰는 7물때식) 또는 8(남해·동해안 8물때식)
"""

FISHING_AREAS = [
    {"id": "chuja", "place": "추자도 해상", "region": "12D00000", "region_name": "제주도 전해상", "lat": 33.96194, "lon": 126.44527, "tide_system": 8},
    {"id": "west", "place": "태안 앞바다", "region": "12A20100", "region_name": "서해중부앞바다", "lat": 36.7, "lon": 126.1, "tide_system": 7},
    {"id": "southeast", "place": "부산 앞바다", "region": "12B20100", "region_name": "남해동부앞바다", "lat": 35.1, "lon": 129.2, "tide_system": 8},
]

DEFAULT_AREA_ID = FISHING_AREAS[0]["id"]
REGION_NAMES = {area["region"]: area["region_name"] for area in FISHING_AREAS}


def get_area(area_id: str) -> dict | None:
    return next((area for area in FISHING_AREAS if area["id"] == area_id), None)
