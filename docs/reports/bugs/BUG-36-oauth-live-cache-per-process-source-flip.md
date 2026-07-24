# BUG-36 · OAuth 실측 캐시가 프로세스별 인메모리라 재기동마다 소스 플립(오늘 0↔실측)

- **상태**: 수정 완료 (2026-07-24)
- **심각도**: 중간(claude '오늘 소비'가 프로세스 경계마다 실측↔트랜스크립트로 깜빡임 — 사용자 지적)
- **영역**: `yok3x/limits.py` `_probe_claude_oauth` · `_OAUTH_LIVE_CACHE`

## 증상

claude '오늘 소비'가 0(트랜스크립트 추정)↔실측(OAuth 66% 기반)으로 반복해서 튀었다. OAuth 엔드포인트가
간헐 429(#31637 알려진 버그)를 내면 마지막 실측을 유지(stale-while-error, BUG-30)하도록 돼 있었지만,
**GUI 재기동·매 CLI 호출마다** 다시 트랜스크립트로 떨어졌다.

## 근본 원인

stale-while-error가 쓰는 `_OAUTH_LIVE_CACHE`가 **모듈 전역 인메모리 dict**(프로세스별). 그래서:
- `yok3x pace`/`limits` 같은 CLI는 매 호출이 새 프로세스 → 캐시 비어 있음 → OAuth 재시도 → (백오프/429)
  → max_stale 유지할 이전 실측이 없어 트랜스크립트 폴백.
- GUI 서버를 재기동하면(디버깅 중 잦음) 인메모리 캐시·백오프가 통째로 소실 → 같은 플립.

즉 stale-while-error가 **프로세스 경계를 못 넘어** 무력화됐다. 트랜스크립트는 주간을 ~48%로 과소계상
(OAuth 66% 대비)하므로, 플립할 때마다 '오늘/주간'이 실제보다 낮게 튀었다.

## 수정

성공 실측을 **디스크에 영속화**(`.yok3x/oauth_live.json`, 원자적 쓰기 — BUG-32와 동일 이유):
- 실측 성공 시 `_save_oauth_live`로 windows(name/used_percent/resets_at/tokens)·detail·타임스탬프 저장.
- 새 프로세스에서 인메모리 캐시가 비면 `_load_oauth_live`로 디스크 실측을 이어받는다 →
  stale-while-error(max_stale=1h)가 프로세스 경계를 넘어 유지되고, CLI/GUI가 **같은 실측을 공유**.
- 429/백오프여도 디스크의 마지막 실측(1h 이내)을 stale 라벨로 유지 → 트랜스크립트로 안 떨어짐.

추가(additive)·격리(저장 실패해도 라이브 실측은 유지). 네트워크 호출 증가 없음.

## 검증

- 신규 테스트 `test_oauth_live_persists_to_disk_across_process`: 성공 실측이 디스크에 저장되고,
  인메모리 캐시를 비운(=새 프로세스) 뒤 OAuth 429여도 디스크의 66% 실측을 stale 라벨로 유지(트랜스크립트
  플립 안 함) 확인. 310 passed.

## 교훈

프로세스별 인메모리 캐시로 구현한 stale-while-error는 **단일 장수 프로세스에서만** 성립한다. CLI처럼
매번 새로 뜨거나 서버를 자주 재기동하면 캐시가 사라져 폴백이 상시 발동한다. 프로세스 경계를 넘어야 하는
상태는 디스크에 (원자적으로) 영속화하라. 관련: [[BUG-30]](stale 창 연장), [[BUG-34]](오늘 0 리셋), [[BUG-35]].
