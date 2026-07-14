# 버그 리포트 (reports/bugs/)

디버깅 세션에서 발견·수정한 버그를 건별로 기록한다(2026-07-10~ 계속 갱신). 각 파일 형식:
증상 · 근본원인 · 진단 · 수정 · 검증 · 교훈. **새 버그를 잡으면 여기 표에 추가하고 파일을 만든다.**

| # | 제목 | 심각도 | 커밋 | 상태 |
|---|---|---|---|---|
| [01](BUG-01-shared-mutable-default.md) | 공유 가변 기본값 오염(얕은 복사) | 낮음(잠재) | `39dff80` | ✅ |
| [02](BUG-02-version-drift.md) | 버전 표기 드리프트(3.1.0 vs v2.2/v3.0) | 낮음 | `39dff80` | ✅ |
| [03](BUG-03-stall-signal-weak.md) | 스톨 감지 신호 약함 + 사변수 | 중간 | `39dff80` | ✅ |
| [04](BUG-04-cli-stdin-deadlock.md) | CLI 백엔드 stdin 데드락(무한 대기) | 높음 | `44e5ee0` | ✅ |
| [05](BUG-05-codex-jsonl-parser.md) | codex 0.144 JSONL 파서 비호환(SCORE 미파싱) | 높음 | `c898105` | ✅ |
| [06](BUG-06-mat-oauth-label.md) | mat 대시보드 실측 라벨 누락(raw 표시) | 낮음 | `282ba45` | ✅ |
| [07](BUG-07-run-id-collision.md) | run_id 초 단위 충돌 → 동시 런 손상 | 중간 | `5d00b00` | ✅ |
| [08](BUG-08-worker-cwd-contamination.md) | 워커 레포 cwd 컨텍스트 오염 | 중간 | `8313d0e` | ✅ |
| [09](BUG-09-gemini-skip-trust.md) | gemini 신뢰 안 된 디렉터리 실행 거부 | 높음 | `df05cdf` | ✅ |
| [10](BUG-10-producer-multiline-argv-truncation.md) | **프로듀서 병목: 멀티라인 argv 잘림** | **치명** | `43ec4db` | ✅ |
| [11](BUG-11-gui-js-syntax-collisions.md) | GUI JS 식별자 충돌(스크립트 전체 무력화) | 높음 | `06ed52a` | ✅ |
| [12](BUG-12-codex-window-mislabel.md) | codex 한도 창 오라벨(7일을 5h로, 주간 사라짐) | 중간 | — | ✅ |
| [13](BUG-13-self-poisoning-brief-knot-loop.md) | **자기오염 루프 — 런 출력이 brief.md/knot로 다음 런 오염(계산기 실패)** | **높음** | `0979ca6` | ✅ |
| [14](BUG-14-cli-usage-local-shadow.md) | cli.py `usage` 지역 섀도잉 → pace/limits UnboundLocalError | 중간 | `ffb85d8` | ✅ |
| [15](BUG-15-claude-uncalibrated-1003pct.md) | claude 미보정 추정 1003% 오표시(429/토큰만료) | 중간 | `5811eb3` | ✅ |
| [16](BUG-16-lint-false-broken-links.md) | knot lint 깨진 링크 오탐(자동 런 노트 [[..]]) | 낮음 | `0979ca6` | ✅ |

**최대 건**: BUG-10 — Windows npm `.cmd` 심이 멀티라인 argv를 첫 줄바꿈에서 잘라, 프로듀서가
여러 세션에 걸쳐 "작업 없음"으로 실패하던 결정적 버그. stdin 전달로 근본 해결.
**주목**: BUG-13 — 에이전트 출력을 다음 입력으로 되먹여 실패가 증폭되던 자기오염 루프(계산기 실패의 원인).
