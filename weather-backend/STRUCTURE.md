# 백엔드 폴더 구조와 담당 영역

페이지별로 API 파일을 나눴습니다. 자기 담당 파일만 고치면 다른 팀원과 충돌이 거의 나지 않습니다.

| 페이지 | 담당 파일 | 주요 API |
|---|---|---|
| 날씨 | `app/routers/weather.py`, `app/weather.py`, `app/advice.py` | `/api/weather/*`, `/api/location/search` |
| 산악 | `app/routers/mountain.py`, `app/routers/hiking.py`, `app/mountain_catalog.json` | `/api/mountain/*`, `/api/users/me/hiking-plans`, `/api/hiking/parties/*` |
| 낚시 | `app/routers/fishing.py`, `app/fishing/` | `/api/fishing/areas`, `/api/fishing/summary` |
| 관리자 | `app/routers/admin.py`, `app/routers/notices.py` | `/api/admin/*`, `/api/notices` |

## 낚시 백엔드 (`app/fishing/`)

| 파일 | 내용 |
|---|---|
| `areas.py` | 선택할 수 있는 해역 목록과 물때 방식(7·8물때식). 해역 추가는 여기 한 줄만 추가하면 화면에도 나타납니다 |
| `providers.py` | 기관 API 호출·응답 해석 (해상예보, 부이 관측, 조위). 새 자료는 `fetch_*` 함수로 추가 |
| `service.py` | 화면용 요약 만들기: 기관 API 동시 호출, 캐시, 기관별 오류 문구 |
| `tides.py` | 조위 예측(오늘·내일)을 시간순으로 정리하고 만조·간조 시각·높이 추정 |
| `cache.py` | 기관 API 응답 메모리 캐시 (해상예보 10분, 부이 5분, 조위 30분) |

관리자용 해양 API 연결 점검(`app/marine_integrations.py`)은 관리자 페이지 담당입니다.

## 공통 영역 (고치기 전에 팀에 먼저 공유)

| 파일 | 내용 |
|---|---|
| `app/main.py` | 앱 생성, CORS·로그 미들웨어, DB 오류 처리, 라우터 등록 |
| `app/routers/auth.py` | 회원가입, 로그인, 관심 지역, 내 정보 수정, 탈퇴, 비밀번호 변경 |
| `app/core/auth.py` | 로그인 토큰 발급·검증, 비밀번호 해시, 아이디 검증 |
| `app/core/schema.py` | DB 테이블 자동 생성 |
| `app/database.py`, `app/health.py`, `app/weather_diagnostics.py` | DB 연결, 헬스 체크, 요청 로그 |

## 새 API를 추가할 때

1. 담당 페이지의 `app/routers/*.py`에 `@router.get(...)`처럼 추가합니다. (`@app`이 아니라 `@router`)
2. 새 라우터 파일을 만들었다면 `app/main.py`의 `include_router` 목록에 한 줄 추가합니다.
3. 로그인한 사용자가 필요하면 `app.core.auth`의 `_get_token_user`를 씁니다.
4. 테이블을 새로 만들거나 바꾸면 `app/core/schema.py`와 `migrations/`에 함께 반영합니다.

## 실행과 테스트

```bash
uvicorn app.main:app --reload
python -m unittest discover -s tests -v
```
