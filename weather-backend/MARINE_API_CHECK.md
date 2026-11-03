# 해양 API 연결 점검

관리자 전용 점검 API는 설정된 키를 사용해 아래 5개 API를 한 번씩 호출합니다.

- `KHOA_FISHING_API_KEY`: 바다낚시지수
- `KHOA_TIDEBED_API_KEY`: TideBED 예측 조위
- `KMA_API_HUB_KEY`: 단기 해상예보, 기상특보, 해양기상부이·파고부이 관측

배포 후 관리자 비밀번호를 `x-admin-password` 헤더로 전달해 호출합니다.

```http
POST /api/admin/integrations/marine/check
x-admin-password: <Render의 ADMIN_PASSWORD>
```

응답에는 환경변수 설정 여부, 성공 여부, HTTP 상태와 일반화된 설명만 포함됩니다. 인증키와 기관의 원문 응답은 반환하지 않습니다. API 호출 제한을 아끼기 위해 필요할 때만 점검하세요.
