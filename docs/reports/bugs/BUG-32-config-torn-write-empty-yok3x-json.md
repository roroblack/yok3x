# BUG-32 · save_yok3x 비원자적 쓰기로 yok3x.json이 0바이트로 손상

- **상태**: 수정 완료 (2026-07-23)
- **심각도**: 높음(GUI/CLI 전체 기동 불가 — `Config.load` JSONDecodeError로 크래시)
- **영역**: `yok3x/config.py` `Config.save_yok3x` · `Config.load`

## 증상

GUI 프리뷰 서버 기동이 `json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)`로
실패. `yok3x.json`이 **0바이트**였다. `yok3x`를 쓰는 모든 명령(`gui`·`limits`·`pace`·`run` 등)이 전부
크래시하는 상태.

## 근본 원인

`save_yok3x`가 `Path.write_text()`를 직접 썼다. `write_text`는 파일을 **열면서 먼저 0바이트로 truncate**한
뒤 내용을 쓴다 — truncate와 실제 쓰기 사이에 프로세스가 죽으면(diskI/O 타이밍) 파일이 영구히 0바이트로
남는다(torn write). 이 세션에서 GUI 서버 프로세스를 코드 반영을 위해 `Stop-Process -Force`로 여러 번
강제종료했는데, 그 순간이 `save_yok3x()`(예: `autocalibrate_claude`의 설정 저장) 호출 중이었을 가능성이
높다. 같은 위험을 `usage._save_pace`·`_write_oauth_atomic`은 이미 임시파일+replace로 막고 있었는데,
`save_yok3x`만 이 패턴이 빠진 **불일치**였다.

## 수정

1. `save_yok3x`를 원자적으로 교체: pid 고유 임시파일에 쓴 뒤 `tmp.replace(p)`(단일 볼륨 내 원자적 교체).
   강제종료가 임시파일 쓰기 중 일어나도 원본 `yok3x.json`은 손상 전 내용 그대로 남는다.
2. `Config.load`를 방어적으로: 손상된(빈 파일·깨진 JSON) 설정 파일을 만나면 **크래시 대신 기본값으로
   폴백**하고 `logging.warning`으로 명시 경고(§5.5: 조용히 삼키지 않음 — 폴백은 기본값이지 조작값이 아님).
   `_load_json_or_empty` 헬퍼로 `yok3x.json`·`backends.json` 둘 다 적용.
3. 손상된 live `yok3x.json`을 `.bak`(3일 전)에서 복구 후, 오늘 세션에서 바꾼 값(캘리브레이션
   `limit_5h/7d_tokens`·`max_stale_sec=3600`·`auto_refresh=False`)을 재적용해 정확히 맞춤.

## 검증

- 신규 테스트 4: 손상(0바이트) 폴백+경고로그, 깨진(비어있지않은) JSON 폴백, 원자적 저장(tmp만 쓰고
  replace 전 중단 시뮬레이션 — 원본 불변 확인).
- 전체 스위트 304 passed(1 skipped=live 캐너리, 정상).
- 복구된 live config로 `Config.load()`·GUI 기동 정상 확인.

## 교훈

설정처럼 **여러 프로세스가 반복적으로 저장하는 파일**은 반드시 원자적 쓰기(임시파일+replace)여야 한다.
같은 코드베이스에 이미 올바른 패턴(`_save_pace`)이 있었는데 `save_yok3x`만 놓친 것 — **저장 함수를
새로 추가할 때 기존 원자적 쓰기 패턴을 그대로 재사용하는 체크리스트**가 있었으면 예방됐다. 로더는
손상 파일에 대해 크래시보다 명시적 경고+기본값 폴백이 낫다(도구 전체 불가용보다 나음).
