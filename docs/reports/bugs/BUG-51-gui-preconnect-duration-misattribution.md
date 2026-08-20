# BUG-51 — GUI preconnect 대기를 `/api/state` 처리 지연으로 기록

## 증상

브라우저 preview가 요청 전에 TCP 연결을 열어 둔 뒤 6~7초 후 `/api/state`를 보내면, 응답은 `200`인데 `guiserver.log`에 `duration_ms=6000~7000`으로 기록됐다. 요청 라인을 끝내 보내지 않은 연결은 15초 후 `path=` 빈 timeout으로 기록됐다.

## 원인

`BaseHTTPRequestHandler.handle_one_request()` 전체를 감싼 계측이 요청 라인 수신 전 `socket.readinto()` 대기까지 포함했다. snapshot/backend probe가 HTTP 요청 스레드를 막은 것이 아니었다.

## 수정 및 검증

`guiserver.py`에서 HTTP/1.0과 `Connection: close`를 명시하고, 정상 요청은 `parse_request()` 완료 시점부터 duration을 측정하도록 수정했다. py-spy는 문제 스레드가 `socket.readinto()`에 있음을 확인했고, 수정 후 8초 preconnect 요청은 로그 `1.6ms`, 클라이언트 측 요청 후 측정 `2.0ms`였다. 상세 결과는 [후속 조사 리포트](../v4.9.0-gui-server-alternating-slow-request-2026-08-20.md)에 기록했다.
