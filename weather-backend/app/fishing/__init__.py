"""낚시 페이지 백엔드 (담당: 낚시 페이지)

  areas.py      선택할 수 있는 해역 목록
  providers.py  기관 API 호출·응답 해석 (새 자료는 여기에 fetch_* 함수 추가)
  service.py    화면에 보낼 요약 만들기 (동시 호출, 캐시, 오류 문구)
  cache.py      기관 API 응답 메모리 캐시
API 주소는 app/routers/fishing.py 에 있습니다.
"""
from app.fishing.service import get_fishing_areas, get_fishing_summary

__all__ = ["get_fishing_areas", "get_fishing_summary"]
