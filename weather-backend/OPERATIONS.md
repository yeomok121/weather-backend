# 서비스 연결 확인과 복구

## Render 설정

기존 `weather-backend-api` 서비스의 Settings → Health Check Path를
`/api/health/live`로 지정한다. 이 저장소를 새 Blueprint로 배포할 필요는 없다.
시작 명령은 기존처럼 `uvicorn app.main:app --host 0.0.0.0 --port $PORT`를 사용한다.
이 설정은 Render 계정의 대시보드에서 한 번 적용해야 한다.

## 상태 확인

- `GET /api/health/live`: DB와 기상 기관에 의존하지 않는 HTTP 프로세스 상태. 정상 200.
- `GET /api/health`: 최근 DB 연결 확인 결과. 정상 200, 점검 중·연결 실패 503.
  서버 시작과 이후 60초마다 다시 확인한다. 주소·계정·비밀번호·예외 메시지를 공개하지 않는다.
- `GET /api/weather/summary?lat=37.5034&lon=126.7660`: 실제 예보 사용 가능 여부.
  기온 공급 기관이 모두 실패하면 임의 기온 대신 오류를 반환한다.

GitHub Actions의 `Service health`는 매시간 위 세 경로를 확인하며, Actions에서
직접 `Run workflow`로 실행할 수도 있다. 실패한 실행이 장애 기록이 된다.
저장소의 Actions 실행이 활성화돼 있어야 예약 실행이 진행된다.

## 사용자 화면 복구 동작

첫 GET 요청은 절전 복귀를 고려해 최대 90초 기다리고, 일시적인 실패에는 한 번
더 시도한다(최대 20초). 계정·파티 쓰기 요청은 중복 생성을 방지하기 위해 자동 재전송하지 않는다.
네트워크 복구 또는 탭 복귀 때 실패한 날씨 조회를 다시 시도한다.
최근 1시간 이내 같은 지역에서 정상 수신한 자료만 임시 표시하며 지난 자료임을 명시한다.
이 자료를 표시하는 동안 새 계획·파티 생성은 제한한다.

DB 연결·읽기·쓰기는 제한 시간이 있고, 연결 실패는 재시도 가능한 503과
사용자에게 보여줄 메시지로 반환한다. 기상 조회는 회원 DB 연결과 별도로 작동한다.

## 장애 시 확인 순서

1. `/api/health/live`가 응답하지 않으면 Render Events와 프로세스 시작 로그를 확인한다.
   Render 자체에서 인스턴스 생성이 멈춘 경우 대시보드의 재배포·재시작이 필요할 수 있다.
   브라우저 코드는 Render 인스턴스를 강제로 다시 생성할 권한을 갖지 않는다.
2. live는 200인데 `/api/health`만 503이면 DB 상태·네트워크를 확인한다. 날씨 조회는 유지한다.
3. 두 상태가 정상인데 실제 예보만 실패하면 기상 기관 응답·설정과 `external_end` 로그를 확인한다.
4. 화면의 문의 번호(`x-request-id`)로 `request_start`부터 `request_end`까지 연결한다.

## 회원·파티 실제 DB 검증

`python scripts/verify_member_flow.py --base-url https://weather-backend-api-goor.onrender.com --live`

독립된 QA 계정 두 개를 만들고, 개인 계획 권한·초대 수락·구성원 권한·물품 분담·
재로그인 후 복원을 확인한다. 계정과 생성 자료는 마지막에 삭제한다.
실제 사용자 자료나 계정은 수정하지 않으며, 토큰·암호는 출력하거나 저장하지 않는다.


## 회원 세션과 복구 이메일

- 회원 세션 전환 시 기존 토큰은 만료 처리되어 한 번 다시 로그인해야 합니다.
- DB에 migrations/007_member_sessions.sql과 migrations/008_member_recovery.sql을 적용합니다. 애플리케이션 계정에 테이블 생성 권한이 있는 환경에서는 첫 사용 시 자동 생성됩니다.
- 복구 이메일은 RECOVERY_SMTP_HOST와 RECOVERY_SMTP_FROM을 설정한 뒤 활성화됩니다. SMTP는 TLS 전용이며 포트는 RECOVERY_SMTP_PORT에서 바꿀 수 있습니다. 기본값은 465입니다.
- SMTP 인증이 필요한 발송 서버에서는 RECOVERY_SMTP_USER와 RECOVERY_SMTP_PASSWORD를 함께 설정합니다. 실제 발송 계정에서 허용된 발신 주소를 사용합니다.
- 로그인한 회원이 내 정보에서 현재 비밀번호로 확인한 뒤 복구 이메일을 등록할 수 있습니다. 이메일 확인을 마친 계정은 비밀번호 찾기에서 받은 일회용 코드를 사용할 수 있습니다.

## 파티 준비물과 커뮤니티 게시글

- migrations/009_hiking_party_gear.sql과 migrations/010_community_post_submissions.sql을 순서대로 적용합니다. 테이블 생성 권한이 있는 환경에서는 첫 사용 시 자동 생성됩니다.
- 기존 파티 물품은 공동 물품으로 표시됩니다. 기존 담당자와 완료 상태는 배정을 처음 수정할 때 새 저장 구조로 옮겨집니다.
