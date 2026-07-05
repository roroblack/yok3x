# yok3x — 멀티 에이전트 코딩 코치 v3.0

Claude Code · Codex · Gemini CLI를 하나의 오케스트레이터로 묶어, **구독 한도를 지키면서** 코딩 에이전트 루프(구현→리뷰→수정)를 돌리는 프로그램. Python 3.10+ 표준 라이브러리만 사용(의존성 0). 한 모델이 코드를 만들고 다른 모델이 리뷰·채점한다.

- 개발·운영 규칙: [RULE.md](RULE.md) · 변경 이력: [HISTORY.md](HISTORY.md)
- **yok3x 기법**(계획→구현→자가검증)과 **할루시네이션 방지** 지침이 코딩 워커에 자동 주입된다.
- `yok3x gui`로 브라우저 콘솔(실데이터) 프로토타입 실행.

## 빠른 시작

```bash
# 1) 실제 CLI 없이 전체 흐름 검증(드라이런)
python3 yok3x.py init --mock
python3 yok3x.py setup
python3 yok3x.py run task-producer-reviewer.json --auto
python3 yok3x.py mat

# 2) 실전: CLI 설치 후 mock 없이 초기화
python3 yok3x.py init          # backends.json이 claude/codex/gemini CLI를 호출
python3 yok3x.py loop task-pipeline.json -n 5   # 가드가 예산 초과 시 루프를 스스로 정지
```

`yok3x setup`이 샘플 태스크 3종을 생성한다("멀티에이전트 시스템 구성해줘" 자동 셋팅에 해당).

## 명령어

| 명령 | 기능 |
|---|---|
| `init [--mock]` | yok3x.json / backends.json / 디렉터리 생성 |
| `setup` | 샘플 태스크 3종 + 초기화 |
| `run <task.json> [--auto]` | 태스크 1회 실행. `--auto` 없으면 매 단계 승인 게이트(y/N/q) |
| `loop <task.json> -n N` | 에이전트 루프. **요금 가드가 stop이면 스스로 멈춘다** |
| `mat [--watch]` | 세 도구 사용량 + 코칭 + 워커 진행 상태 한 화면 |
| `coach` | '어느 작업을 · 왜 · 언제' 5시간/7일 사용량 코칭 |
| `coach guard on\|off` | 요금 가드 켜기/끄기 |
| `limits` | 실제 구독 한도 probe 원본 확인(진단) |
| `plan [tool] [name]` | 요금제 프리셋 확인/설정(claude: pro/max5x/max20x, gemini: free/paid) |
| `calibrate claude <5h\|7d> <실제%>` | 실사용 기반 claude 한도 역산 보정(정확) |
| `gui [--port N] [--no-open]` | 브라우저 GUI 프로토타입 실행(실데이터 `/api/state` 연동) |
| `knot save\|ingest\|query\|lint` | 지식그물(평문 md 공유 기억) |
| `flavor [이름]` | 오케스트레이터/워커 구조 전환 |

## 기능 ↔ 구현 매핑

| v2.2 매뉴얼 항목 | 구현 위치 |
|---|---|
| 요금 가드(루프 자동 정지, guard on/off) | `usage.py: check_backend/guard_allows` — **실측 한도 우선**, soft 80% 경고·hard 100% 정지 |
| 실제 구독 한도 조회(진짜 한도 준수) | `limits.py` — codex는 서버 보고 5h/7d 실측, claude는 transcript 롤링 추정, 외부 도구(ccusage/tokscale) command probe |
| 사용량 코치 coach | `usage.py: coach_messages` — 작업·이유·시점(자정 리셋 잔여 시간) 제시 |
| mat 사용량·코칭 뷰 | `matview.py` — 환경변수 없이 yok3x.json 하나로 동작 |
| 멀티 에이전트 검수(제작↔채점) | `orchestrator.py: run_producer_reviewer` — `SCORE: n` 파싱, 기준 미달 시 재작업 |
| 카파시 4원칙(폭주 브레이크) | 단계 분할 실행·승인 게이트·검증 체크리스트·예산 가드 4중 브레이크 |
| 지식그물 knot | `knot.py` — frontmatter md, `[[위키링크]]`, save/ingest/query/lint |
| 3개 실행 환경 구성 | `backends.json` — 백엔드별 command 템플릿 교체만으로 전환 |
| flavor별 오케스트레이터/워커 | `yok3x.json: flavors` — claude/codex/gemini-orchestrator 3종 |
| claude-main·codex-main·codex-critic·gemini 워커 | `yok3x.json: workers` — 역할 프롬프트 포함 |
| MCP·CLI·native 호출 + backends.json 어댑터 | `backends.py` — type: cli/native/mcp/mock |
| 승인 게이트·파일 로그·검증 체크리스트 | `orchestrator.py` — `.yok3x/runs/<id>/step_NN.json`, status.json |
| context.md·brief.md 글자 제한 | `knot.py: clip/write_context/write_brief` — 초과분 중간 절단 |
| Fan-out/Fan-in·Pipeline·Producer-Reviewer | `orchestrator.py: run_fanout/run_pipeline/run_producer_reviewer` |
| mat로 워커 진행 상태 확인 | `.yok3x/runs/*/status.json`을 mat이 집계 |

## 태스크 파일 형식

```json
{ "pattern": "producer-reviewer",
  "task": "…", "producer": "claude-main", "reviewer": "codex-critic",
  "max_rounds": 2, "pass_score": 8.0 }

{ "pattern": "pipeline", "task": "…",
  "stages": [ {"worker": "claude-main", "kind": "build", "task": "…"}, … ] }

{ "pattern": "fanout-fanin", "task": "…",
  "workers": ["claude-main", "codex-main", "gemini"], "join_worker": "claude-main" }
```

### 코딩 태스크 옵션(모든 패턴 공통, v3.0+)

```json
{ "workdir": "F:/my/repo",                  // 워커·검증 실행 디렉터리(실 CLI가 파일 편집)
  "verify_cmd": "pytest -q",                 // 테스트/린트 게이트 — 비정상 종료면 높은 SCORE여도 통과 불가
  "verify_timeout_sec": 300,                 // 게이트 제한시간
  "context_globs": ["src/**/*.py"],          // 레포 컨텍스트 주입(글자 제한 repo_context_max_chars)
  "rubric": "rubric.md" }                    // 채점표를 검수 프롬프트에 주입
```

- 스톨 감지: 점수·이슈가 2회 연속 동일하면 수렴 실패로 조기 종료(+knot 기록).

## 한도·예산 설정(yok3x.json)

가드는 **실제 구독 한도(실측)를 최우선**으로 지키고, 실측이 없을 때만 자체 일일 예산으로 폴백한다. '한도는 무조건 지켜야 한다'가 설계 원칙이다.

```json
"guard": {
  "enabled": true, "soft_ratio": 0.8, "hard_ratio": 1.0,
  "use_real_limits": true,        // 진짜 한도 우선(끄면 원장만)
  "on_probe_failure": "ledger"    // 실측 실패 시: ledger(폴백) | block(차단) | allow
},
"limits": {
  "claude": { "type": "claude_transcripts", "limit_5h_tokens": 0, "limit_7d_tokens": 0 },
  "codex":  { "type": "codex_appserver", "timeout_sec": 15 },
  "gemini": { "type": "ledger" }
},
"budgets": {                      // 실측 한도가 없을 때의 안전망(로컬 자정 리셋)
  "claude": { "daily_usd": 5.0,      "daily_calls": 200 },
  "codex":  { "daily_tokens": 2000000, "daily_calls": 200 },
  "gemini": { "daily_tokens": 2000000, "daily_calls": 200 }
}
```

### 실제 한도 조회 방식(limits.py) — 도구별 정확도

| backend | `type` | 출처 | 정확도 |
|---|---|---|---|
| **codex** | `codex_appserver` | `codex app-server` JSON-RPC `account/rateLimits/read` → **지금 이 순간** 5h/7d `usedPercent`·리셋시각 | ✅ **라이브 실측** |
| codex(폴백) | `codex_sessions` | `~/.codex/sessions/**/rollout-*.jsonl`의 마지막 `rate_limits` | 파일값(오래되면 stale) |
| claude | `claude_transcripts` | `~/.claude/projects/**/*.jsonl`의 `usage`를 5h/7d 롤링 합산 → `limit_*_tokens` 대비 | 롤링 **추정** |
| gemini | `ledger` | 자체 일일 예산(호출/토큰) | 자체 준수 |
| 기타 | `command` | 외부 도구(ccusage·tokscale 등) JSON 매핑 | 도구에 따름 |

정확도 현실(2026-07 기준, 실측 검증):

- **codex만 진짜 실시간 %가 나온다.** `codex app-server`가 서버에 물어 지금 값을 준다(CodexBar와 같은 경로). `codex exec --json`은 `rate_limits: null` 버그가 있어 못 쓴다. 라이브 조회 실패 시 세션 파일(stale)→원장 순으로 자동 폴백한다.
- **claude는 라이브 %가 불가능하다.** Anthropic이 CLI로 잔여 한도를 노출하지 않는다(CodexBar도 결국 transcript 스캔으로 폴백). 본인 플랜의 5h/7d 토큰 상한을 `limit_5h_tokens`·`limit_7d_tokens`에 넣으면 롤링 사용량 대비 추정치가 나온다(0이면 원장 폴백).
- **gemini도 라이브 %가 불가능하다.** 헤드리스 JSON은 호출당 토큰만 주고 잔여 한도가 없다. 자체 일일 예산(원장)으로 지킨다. `command` probe로 외부 도구를 붙일 수 있다.
- `command` probe는 범용 어댑터: `windows:[{name,percent_path,resets_at_path}]`로 임의 JSON 필드를 매핑하거나 `parse:"ccusage_active"` + `limit_5h_usd`로 ccusage를 붙인다.
- `yok3x limits`로 각 probe의 라이브/stale/추정 원본을 확인한다.

### claude 한도 설정 — 요금제 프리셋 vs 캘리브레이트

토큰 상한을 직접 몰라도 되게 두 가지를 제공한다.

```bash
yok3x plan claude max20x         # 요금제 프리셋(근사 상한 자동 적용). pro | max5x | max20x
yok3x calibrate claude 7d 30     # ← 정확: 지금 claude 앱 /usage 가 7일 30%면 이렇게 입력
```

- **plan**: 빠른 시작용 근사치. Anthropic이 정확한 토큰 상한을 공개하지 않아 어디까지나 어림값이다.
- **calibrate**(권장): claude 앱/CLI에서 실제 5h·7d 사용률을 한 번 확인해 입력하면, `현재 롤링토큰 ÷ 실제%`로 **본인 정확한 상한을 역산**한다. 우리 토큰 집계는 캐시 read를 포함해 값이 크지만, 캘리브레이트는 같은 단위로 맞추므로 이후 사용률이 실제와 일치한다. codex는 라이브 실측이라 이 과정이 불필요하다.

> CodexBar(macOS 전용)가 `codex app-server`·`claude /usage`로 하는 일을, 이 프로그램은 **윈도우 포함 크로스플랫폼**으로 재현한다. codex는 동일한 app-server RPC라 정확도가 같고, claude/gemini는 라이브 소스가 없어 CodexBar와 같은 한계를 갖는다.

## 백엔드 CLI 사양(공식 문서 검증, 2026-07-04)

- Claude Code: `claude -p "<prompt>" --output-format json` — 응답에 `result`, `session_id`, `total_cost_usd`, `usage` 포함. https://code.claude.com/docs/en/headless
- Codex: `codex exec --json "<prompt>"` — JSONL 이벤트 스트림(`item.completed`의 agent 메시지가 최종 응답), `--skip-git-repo-check`로 비Git 디렉터리 실행. https://developers.openai.com/codex/noninteractive
- Gemini CLI: `gemini -p "<prompt>" --output-format json` — `{response, stats.models[*].tokens}` 반환. JSON 앞에 "Loaded cached credentials." 노이즈가 붙는 사례가 보고되어 첫 `{`부터 파싱한다. https://geminicli.com/docs/cli/headless/

CLI 미설치 백엔드는 명확한 오류를 내며, `backends.json`에서 해당 항목의 `"type": "mock"` 전환으로 즉시 드라이런 가능하다.

## 윈도우 호환성

- CLI 호출은 `shutil.which()`로 npm `.cmd` 심(claude.cmd 등)을 해석한다(CreateProcess가 PATHEXT를 안 보는 문제 회피).
- 모든 stdout/stderr를 UTF-8로 재구성해 레거시 콘솔(cp949)에서도 `mat` 게이지(█░╭╰)가 깨지지 않는다.
- 모든 설정·태스크·노트 읽기는 `utf-8-sig`라, 메모장·PowerShell이 붙이는 **UTF-8 BOM이 있어도 크래시하지 않는다**.
- 한도 조회 경로는 `Path.home()` 기반(`%USERPROFILE%\.codex`, `\.claude`)이라 윈도우에서 그대로 동작한다.
- 실제 Windows 11 / Python 3.14 / PowerShell 5.1에서 전 명령·전 패턴·가드 정지·BOM 설정 로드까지 스모크 테스트 완료.

## 디렉터리 구조

```
yok3x.json  backends.json  context.md  brief.md  task-*.json
knowledge/            # knot 지식그물(md)
.yok3x/
  usage.jsonl         # 사용량 원장
  runs/<run_id>/      # status.json, step_NN_<worker>.json, final_output.md, run.log
```
