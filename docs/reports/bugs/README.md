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
| [17](BUG-17-token-refresh-wrong-endpoint-ua.md) | 토큰 자체갱신 엔드포인트 오류+UA 누락 → 항상 실패(라이브 검증서 발견) | 중간 | — | ✅ |
| [18](BUG-18-stale-backends-prompt-argv.md) | **스테일 backends.json {prompt}(argv) → 멀티라인 잘림(계산기 실패, BUG-10 재발)** | **높음** | — | ✅ |
| [19](BUG-19-console-html-injection-horizontal-blowout.md) | **콘솔 채팅 세로→가로 붕괴 — 에이전트 산출물 HTML이 이스케이프 없이 innerHTML DOM 파괴** | **높음** | — | ✅ |
| [20](BUG-20-usage-strip-per-window-color-and-missing-budget-ruler.md) | 사용량 스트립 창별 색상 오류(5h warn→전체 warn) + 7d 예산 눈금 누락 | 중간 | — | ✅ |
| [21](BUG-21-rename-task-self-collision.md) | 작업 이름 수정: 같은 이름(자기 자신)으로 바꾸면 '이미 있다'로 실패 | 중간 | — | ✅ |
| [22](BUG-22-send-not-attaching-to-selected-task.md) | 전송이 선택한 작업에 안 붙고 무제목/전체로 감(라벨 CRUD 이동 회귀) | 높음 | — | ✅ |
| [23](BUG-23-materialize-status-overwritten.md) | 산출물 게시 결과가 status.json에서 유실(_finish 이중 저장) | 중간 | — | ✅ |
| [24](BUG-24-gui-renders-mock-data-on-server-loss.md) | 서버 끊기면 GUI가 mock 사용량을 실데이터처럼 표시(연결 끊김 표시 없음) | 높음 | — | ✅ |
| [25](BUG-25-verify-ignored-worker-candidate.md) | verify가 워커 후보 대신 원본 트리를 검사 | 높음 | — | ✅ |
| [26](BUG-26-codex-daily-cap-anchored-on-rolling-pct.md) | codex 하루 상한이 7d 롤링 %에 앵커돼 계속 줄어듦('상한만 줄고 사용량만 늘고') | 중간 | — | ✅ |
| [27](BUG-27-claude-oauth-usage-probe-blocked-policy.md) | **claude 사용량 프로브가 정책 차단된 OAuth 엔드포인트를 60초마다 호출(계정 안전·429)** | **높음** | — | ✅ |
| [28](BUG-28-statusline-hardening-reset-garbage-and-tmp-race.md) | F-08 statusline 후속 경화 — resets_at 쓰레기값('95084일 후') + tmp 동시쓰기 경합 | 낮음~중간 | — | ✅ |
| [29](BUG-29-pace-band-vs-bar-source-mismatch.md) | 페이싱 밴드와 7d 바가 다른 소스(바=OAuth·밴드=트랜스크립트)라 '상한까지 남은 부분' 소실 | 중간 | — | ✅ |
| [30](BUG-30-oauth-stale-window-too-short-churn.md) | OAuth stale 창(15분)이 짧아 실측↔추정이 자주 깜빡임 | 낮음~중간 | — | ✅ |
| [31](BUG-31-native-title-tooltip-not-rendered.md) | 네이티브 title 툴팁이 프리뷰 렌더러에서 전혀 안 뜸 | 낮음 | — | ✅ |
| [32](BUG-32-config-torn-write-empty-yok3x-json.md) | **save_yok3x 비원자적 쓰기로 yok3x.json이 0바이트로 손상(전체 기동 불가)** | **높음** | — | ✅ |
| [33](BUG-33-pace-cap-jitter-oauth-transcript-source-mix.md) | 페이싱 상한이 하루 중 요동/증가 — OAuth current + 트랜스크립트 today 소스 혼합(BUG-29 부작용) | 중간 | — | ✅ |
| [34](BUG-34-pace-day-key-round-jitter-wipes-today.md) | **reset_at 초이하 지터가 하루키 round()를 튀게 해 오늘 소비가 0으로 리셋** | **높음** | — | ✅ |
| [35](BUG-35-autocalibrate-cap-swing-unbounded.md) | 자동 캘리브레이션 cap이 회당 무제한으로 튀어 3배 널뜀(246M→793M) | 중간 | — | ✅ |
| [36](BUG-36-oauth-live-cache-per-process-source-flip.md) | OAuth 실측 캐시가 프로세스별 인메모리라 재기동마다 소스 플립(오늘 0↔실측) | 중간 | — | ✅ |
| [37](BUG-37-oauth-snapshot-today-collapse-cap-drift.md) | **OAuth 스냅샷 모델이 '오늘'을 0으로 붕괴시키고 상한을 실시간 드리프트(재기동 취약)** | **높음** | — | ✅ |
| [38](BUG-38-transcript-rescan-build-state-hang.md) | 트랜스크립트 전량 재스캔으로 build_state 13초 → GUI 저장이 안 되는 듯(파일 캐시) | 중간 | — | ✅ |
| [39](BUG-39-log-unicode-crash-kills-run-step.md) | **cp949 콘솔에서 로그의 '—'가 UnicodeEncodeError → 작업 단계 유실(래칫 체크포인트 소실)** | 중간~높음 | — | ✅ |
| [40](BUG-40-silent-verify-false-negative-and-uncalibrated-stop.md) | **조용한 열화 2건 — workdir 미설정 시 verify 거짓 실패 · 미보정 추정(995%)으로 새 프로젝트 전체 런 차단** | **높음** | — | ✅ |
| [41](BUG-41-calib-verdict-conflates-undefined-with-low-correlation.md) | 캘리브레이션 판정이 '계산 불가(corr=None)'를 '상관 낮음'으로 뭉개 근거 없이 '게이트 무의미' 표시 | 중간 | — | ✅ |
| [42](BUG-42-absolute-verify-cmd-escapes-stage-false-candidate-label.md) | **절대경로 verify_cmd가 스테이징을 우회해 원본을 검증하고도 'candidate' 라벨(T-1 지상진실 오염)** | **높음** | — | ✅(완화) |
| [43](BUG-43-kill-tree-capture-output-hangs-server.md) | **`_kill_tree`의 capture_output이 손자 프로세스 핸들에 걸려 GUI 서버 전체를 무한 대기시킴** | **높음** | — | ✅ |
| [44](BUG-44-pace-tooltip-null-pace-crashes-render.md) | `paceTip` 계산이 `t.pace===null`을 안 가려 `render()`가 죽고 GUI가 "서버 연결 끊김"으로 오탐 | 중간 | — | ✅ |
| [45](BUG-45-pace-strategy-and-seg-buttons-never-persist.md) | `.seg` 범용 코드가 `onclick` 속성 핸들러를 덮어써 균등/유동/분산·경고만/정지+승인·plan 버튼이 전부 저장 안 됨 | **높음** | — | ✅ |
| [46](BUG-46-pace-axis-tooltip-centered-clips-left-edge.md) | 상한 배분 방식 ＋/－ 아이콘 툴팁이 중앙 정렬 때문에 좁은 화면에서 좌측으로 잘림 | 낮음 | — | ✅ |
| [47](BUG-47-autocalibrate-window-phase-mismatch-sawtooth.md) | **autocalibrate가 롤링 토큰 합계를 텀블링 창의 live %로 나눠 주 단위 톱니(최대 3.68배)로 진동 — 원리적 수렴 불가(BUG-35 클램프는 증상만 완화)** | **높음** | — | ✅ |
| [48](BUG-48-review-artifact-clip-too-small-false-reject.md) | reviewer 산출물 클립이 6000자로 너무 작아 verify=ok인 완전한 코드도 "생략됨"으로 오탈락(T-2 #11에서 실측) | 중간 | — | ✅ |
| [49](BUG-49-automation-review-edge-input-hardening.md) | 자동화·구조화 리뷰 엣지 입력 예외 및 오판 | 중간 | — | ✅ |
| [50](BUG-50-backends-config-misc-modules-edge-input-hardening.md) | **backends/config/기타 모듈 엣지 입력 하드닝 — `worktree.remove()`가 등록 안 된 임의 디렉터리를 조건 없이 `rmtree`하던 위험 포함** | **높음** | — | ✅ |
| [51](BUG-51-gui-preconnect-duration-misattribution.md) | GUI preconnect 연결의 read 대기를 `/api/state` 처리 지연으로 오기록(계측 버그 — probe/락 문제 아니었음) | 중간 | — | ✅ |
| [52](BUG-52-gui-config-save-stale-state-snapshot.md) | GUI 설정 저장 직후 5초 stale snapshot이 새 선택을 이전 값으로 되돌림 | 중간 | — | ✅ |
| [53](BUG-53-orchestrator-edge-input-and-materialize-race-hardening.md) | **orchestrator 엣지 입력 크래시·SCORE 범위 우회·resume 손상값·materialize 동시 덮어쓰기/symlink root 하드닝** | **높음** | — | ✅ |

**최대 건**: BUG-10 — Windows npm `.cmd` 심이 멀티라인 argv를 첫 줄바꿈에서 잘라, 프로듀서가
여러 세션에 걸쳐 "작업 없음"으로 실패하던 결정적 버그. stdin 전달로 근본 해결.
**주목**: BUG-13 — 에이전트 출력을 다음 입력으로 되먹여 실패가 증폭되던 자기오염 루프(계산기 실패의 원인).
