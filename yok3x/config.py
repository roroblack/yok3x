"""yok3x config: yok3x.json(전역 설정) + backends.json(백엔드 어댑터) 로딩.

환경변수를 쓰지 않는다. 설정 파일 한 줄로 모든 동작을 제어한다(mat 원칙).
"""
from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._version import __version__

logger = logging.getLogger(__name__)

YOK3X_DIR_NAME = ".yok3x"

DEFAULT_YOK3X = {
    "version": __version__,          # 단일 출처(_version.py) — 하드코딩 금지
    "flavor": "claude-orchestrator",
    "auto_approve": False,          # 승인 게이트 기본값: 사람이 y/n
    "default_effort": "",           # 추론 강도 전역 기본(low/medium/high). 워커별 effort가 우선. ""=미지정
    "adversarial_review": False,    # ARIS AD1: 켜면 리뷰어가 '반증/파괴' 우선 + 교차 패밀리 강제
    "context_max_chars": 8000,      # context.md 글자 제한
    "brief_max_chars": 1200,        # brief.md 글자 제한
    "examples_max_chars": 4000,     # few-shot 예시(task.examples) 주입 글자 제한(E)
    # knot(지식그물) 통합 설정 — Mem0식 consolidation(의존성 0 근사)
    "knot": {
        "recency_halflife_days": 90,  # 검색 점수 최신성 감쇠 반감기(일). 0=끔. 낡은 기억 자연 강등
        "dedup_threshold": 0.6        # lint 중복 후보 감지 유사도 임계(태그·링크·제목 자카드)
    },
    "repo_context_max_chars": 6000, # context_globs 레포 컨텍스트 주입 글자 제한
    # 작업 워크스페이스(기본 workdir). 지정하면 모든 런이 이 디렉터리에서 워커를 실행한다
    # (task의 workdir가 있으면 그것이 우선). 비면 no-workdir → 빈 격리 dir에서 실행.
    "workspace": "",
    # 산출물 검증 게이트(객관): producer-reviewer가 통과 판정 전에 실제로 돌린다.
    # 예: "pytest -q" · "npm test". 비면 게이트 없음. task 파일의 verify_cmd가 우선.
    "verify_cmd": "",
    "verify_timeout_sec": 300,
    # yok3x 기법: 코딩 작업에 '계획→구현→자가검증' 구조를 워커 프롬프트에 주입.
    # 폭주 방지 4중 브레이크(단계분할·승인·검증·예산)를 프롬프트 수준에서도 강제한다.
    "yok3x_technique": {
        "enabled": True,
        "require_plan": True,       # 구현 전 짧은 계획을 먼저 쓰게 함
        "require_selfcheck": True   # 결과 끝에 자가검증 체크리스트를 쓰게 함
    },
    # 할루시네이션 방지: 근거 없는 단정·날조 파일/API 금지 지침 주입 + 검수 시 환각 항목 점검.
    "anti_hallucination": {
        "enabled": True,
        "require_citations": True,  # 파일·경로·함수는 실제 근거를 대게 함
        "flag_unverified": True     # 검증 안 된 확신 표현을 체크리스트로 표시
    },
    "guard": {
        "enabled": True,            # coach guard on/off
        "soft_ratio": 0.8,          # 경고 임계(실측/원장 사용률)
        "hard_ratio": 1.0,          # 루프 자동 정지 임계
        "use_real_limits": True,    # 진짜 한도(limits.py 실측) 우선. 끄면 원장만 사용
        "on_probe_failure": "ledger",  # 실측 probe 실패 시: ledger(원장 폴백) | block(차단) | allow
        # C-3 진짜 병렬 호출. 호출부 이식 전까지 기본 비활성이고, 활성 시에도 backend별
        # semaphore로 동일 CLI가 한꺼번에 몰리지 않게 한다.
        "parallel": {
            "enabled": False,
            "max_workers": 4,
            "max_per_backend": 2,
            # R-7: 워커마다 HEAD의 독립 git worktree에서 실행(같은 트리를 동시에 밟는 간섭 차단).
            # **기본 off** — worktree는 HEAD 커밋을 체크아웃하므로 **커밋되지 않은 변경이 워커에게
            # 보이지 않는다**. 이 동작 차이를 조용히 강요하지 않으려고 opt-in으로 둔다.
            # 켜도 비-git·커밋없음·git부재면 사유를 로그에 남기고 공유 workdir로 폴백한다.
            "worktree_isolation": False,
        },
        # C-2 배치 예약/추정. 값은 환경·모델에 맞게 yok3x.json에서 조절한다.
        # calls는 정확 예약, token/USD는 프롬프트 길이 기반 보수적 추정 상한이다.
        "reservation": {
            "lock_ttl_sec": 300,
            "pending_ttl_sec": 1800,
            "lock_wait_sec": 5.0,
            "lock_poll_sec": 0.05,
            "chars_per_token": 2.0,
            "output_token_ratio": 2.0,
            "usd_per_1k_tokens": 0.03,
            # 런당 실지출 상한(USD). 0=off(기본). preflight는 프롬프트 길이 기반 추정이라 라운드가
            # 길어지는 런을 과소평가한다(T-2 실측: 추정 $0.03 vs 실제 $3.37) → **실측 누적**으로 끊는다.
            # 켜두면 꼬리 런이 예산을 독식하는 것을 막는다(T-2 1차: 12런 중 2런이 비용의 79%).
            "max_usd_per_run": 0,
            # 필요하면 배치 전체 hard 상한을 직접 지정한다: calls | est_tokens | est_usd.
            # 비어 있으면 backend별 budgets 중 해당 지표의 가장 작은 양수 상한을 쓴다.
            "hard_limits": {},
        },
        # 한도 인근 적응형 열화(P1: 모델 다운그레이드). opt-in. 정지(hard) 전에 가벼운
        # 모델로 낮춰 남은 한도로 계속 진행. 모든 다운그레이드는 명시 로깅된다.
        "degrade": {
            "enabled": False,               # P1: 켜면 downgrade_ratio↑에서 lite 모델로 낮춤
            "downgrade_ratio": 0.9,         # 이 사용률(가장 빡빡한 창)↑ → 다운그레이드
            "roles_no_downgrade": ["codex-critic", "gemini"],  # 리뷰어=품질 게이트라 제외
            # P2: 백엔드 폴오버 — 한도 도달 시 여유 있는 '다른 도구'로 워커를 임시 전환.
            # 결과물 품질이 달라질 수 있어 기본 OFF(별도 on/off). 켜면 정지 대신 계속 진행.
            "failover_enabled": False,      # ← on/off (기본 off = 한도 시 현행처럼 정지)
            "failover_ratio": 0.97,         # 이 사용률↑ 또는 backend stop → 다른 도구로 전환
            "roles_no_failover": [],        # 특정 역할은 전환 제외(예: 리뷰어 고정 원하면 지정)
            "max_failovers_per_run": 3,     # 런당 전환 상한(스래싱 방지). sticky로 왕복도 방지
            # P3: 오프라인 폴백 — 클라우드 대안이 전부 소진(stop)이면 로컬 모델로 강등해 무중단.
            # 로컬 서버가 실제로 떠 있을 때만 전환(도달성 확인). 품질↓라 리뷰어는 제외 권장.
            "offline_enabled": True,        # 클라우드 전멸 시 로컬로 강등(로컬 서버 있을 때만 발동)
            "offline_backend": "local"      # 강등 대상 backend(= backends.local, OpenAI 호환)
        },
        # 주간(7d) 쿼터의 '하루 페이싱' — 하루에 주간쿼터의 pct_of_weekly(%p)만 쓰도록 속도 제한.
        # 오늘 소비 = 현재 7d% − 그날 아침 7d% 스냅샷(.yok3x/pace.json). 절대 5h/7d 한도에 '덧붙는' 층.
        # 라이브 7d가 있는 backend(claude/codex)에 적용. opt-in.
        "daily_pace": {
            "enabled": False,               # 켜면 하루 소비 캡 적용
            "pct_of_weekly": 0.14,          # 하루 상한 = 주간쿼터의 14%p(≈1/7, 균등 페이싱)
            "soft_frac": 0.8,               # 캡의 80%에서 경고(그 이상은 mode에 따름)
            "mode": "warn",                 # warn(경고만) | pause(정지 후 승인 재개)
            "backends": {}                  # 백엔드별 override 예: {"claude":{"pct_of_weekly":0.15}}
        },
        "daily_pace_override": {}           # {backend: "YYYY-MM-DD"} 오늘이면 페이싱 정지 1일 해제
    },
    # v4.1.0 MCP 워커도구(a1, "설정 전달"만 — 실행은 워커 CLI가 함): 전역 **화이트리스트**.
    # 기본 빈 dict = 아무 서버도 없음 = 전체 opt-in(워커가 mcp_tools를 요청해도 여기 없으면
    # fail-closed로 거부). 여기 등록된 것만 워커별 workers.<name>.mcp_tools.servers로 요청 가능.
    # 이름 → MCP 서버 spec(그대로 claude --mcp-config JSON의 mcpServers.<name>에 들어감).
    # 예: {"filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}}
    "mcp_servers": {},
    # 진짜 구독 한도 조회 어댑터 — 서버 보고 사용률을 읽어 '한도 무조건 준수'.
    # codex : app-server JSON-RPC 로 '지금 이 순간' 5h/7d used_percent 라이브 조회(진짜 실측).
    #         실패 시 세션 파일(stale) → 원장 순으로 폴백.
    # claude: Anthropic이 사용률 %를 노출 안 함 → transcript 롤링 합산으로 cap 대비 '추정'.
    #         limit_*_tokens 를 본인 플랜값으로 넣어야 활성화(0=원장 폴백).
    # gemini: CLI가 잔여 한도를 노출 안 함 → 원장(자체 일일 예산)으로 준수. command 로 외부도구 연결 가능.
    "limits": {
        "claude": {
            # 사용량: F-08 statusline 우선(Claude Code가 stdin으로 주는 rate_limits, 1st-party·안전) →
            # 없으면 로컬 트랜스크립트 추정으로 폴백. **OAuth usage 엔드포인트는 미사용** — Anthropic
            # 2026-04-04 정책이 서드파티의 구독 OAuth 토큰 사용을 제한(429). statusline을 쓰려면
            # Claude Code settings.json의 statusLine을 `yok3x statusline`으로 지정해야 실데이터가 흐른다.
            "type": "claude_statusline",   # "claude_transcripts"(추정만)·"claude_oauth"(위험)·"ledger"(끔)
            "statusline_max_stale_sec": 900,  # statusline 캐시가 이보다 오래되면 추정 폴백
            "min_interval_sec": 60,     # 실측 재조회 최소 간격(usage 엔드포인트 rate-limit 배려)
            "max_stale_sec": 3600,      # 실측 일시 실패(429 간헐 버그/만료) 시 마지막 실측을 유지할 최대
                                        # 시간(1h). OAuth가 간헐적이라 짧으면 실측↔추정이 자주 깜빡인다
                                        # (사용자 지적 churn) — 길게 유지하고 '⚠N분 전 실측'으로 정직 표시.
            # 토큰 자체 갱신(opt-in): 만료 임박 시 refresh_token으로 access_token을 표준 OAuth 갱신 →
            # claude 실측이 토큰 만료로 끊기지 않게. 실패는 fail-safe(기존 폴백). client_id/token_url은
            # Claude Code 공개 OAuth 값(미검증 — 켜기 전 확인 권장). 회전 토큰은 credentials에 되씀.
            "auto_refresh": False,      # 기본 off(보수적) — 켜면 자체 갱신. Claude Code와 파일 공유 주의
            "refresh_margin_sec": 300,  # 만료 N초 전부터 갱신 시도
            "min_refresh_interval_sec": 60,  # 과다 갱신 방지 + 실패 시 지수 백오프 기준
            "token_url": "https://api.anthropic.com/v1/oauth/token",   # 라이브 검증됨(200)
            "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
            "plan": "max5x",            # 추정 폴백용 상한 프리셋: pro | max5x | max20x (기본 max5x)
            "limit_5h_tokens": 0,       # 추정 폴백 직접 상한(plan보다 우선). `yok3x calibrate`로 보정
            "limit_7d_tokens": 0,
            # live %(지상진실)와 로컬 transcript 합계를 맞춰 추정 상한을 자동 보정한다.
            "autocalibrate": True,
            "min_calib_pct": 1.0,       # 작은 %로 나눠 캡이 폭발하는 것 방지
            "min_calib_interval_sec": 600,  # 잦은 yok3x.json 쓰기 방지
            # 역산 캡이 plan 프리셋 대비 이 배율 밖이면 무시(불완전 transcript 방어).
            # 실측 근거: transcript는 cache read까지 합산해 7d가 plan 대비 ~153배로 나온다
            # (5h는 ~1배). 100배로 막으면 정상 보정이 거부되므로 1000배까지 허용한다.
            # min_calib_pct가 이미 '작은 %로 캡 폭발'을 막으므로 이 가드는 보조 안전망이다.
            "max_calib_multiple": 1000.0,
            "min_calib_multiple": 0.001,
            # 적응형 열화 다운그레이드 대상(guard.degrade). lite=한도 근처에서 낮출 가벼운 모델
            "models": {"full": "", "lite": "claude-haiku-4-5-20251001"}
        },
        "codex": {
            "type": "codex_appserver",  # 라이브 실측. codex_bin/app_server_args/timeout_sec 조정 가능
            "timeout_sec": 15,
            "models": {"full": "", "lite": ""}   # codex 다운그레이드 모델(원하면 지정)
        },
        "gemini": {
            "type": "ledger",           # 라이브 실측 불가 → 원장. command probe로 tokscale 등 연결 가능
            # 모델 목록: 키 있으면 실시간 Google API, 없으면 gemini CLI 번들의 GEMINI_MODELS
            # 레지스트리(설치 버전이 지원하는 실제 모델). 아래 중 하나만 채우면 실시간. 키는 커밋 금지.
            "api_key": "",              # 직접 키(비권장 — 파일/env 권장)
            "api_key_path": "",         # 키를 담은 파일 경로(예: ~/.gemini/api_key)
            "api_key_env": ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY"]
        },
        "local": {
            "type": "ledger"            # 로컬=무료·무제한 → 원장(항상 여유). P3 폴백 대상.
        }
    },
    "budgets": {                    # 실측 한도가 없을 때의 자체 안전망(로컬 자정 리셋)
        "claude": {"daily_usd": 5.0, "daily_calls": 200},
        "codex":  {"daily_tokens": 2000000, "daily_calls": 200},
        "gemini": {"daily_tokens": 2000000, "daily_calls": 200},
        "local":  {"daily_tokens": 1000000000, "daily_calls": 100000}   # 로컬=사실상 무제한
    },
    "routing": {                    # coach가 권하는 작업(코딩)→도구 기본 라우팅
        "build": "claude",          # 기능 구현
        "refactor": "codex",        # 리팩터·개선
        "review": "codex",          # 코드 리뷰(버그·엣지·보안)
        "test": "claude",           # 테스트 작성
        "design_review": "gemini"   # 설계/아키텍처 검토
    },
    # 상황별 모델 프로파일(v3.3 S1). active_profile을 고르면 작업 상황(task_kind)마다
    # 최적 모델로 라우팅한다. 비면("") 라우팅 off = 현행(워커 기본 backend·CLI 기본 모델).
    # 벤치마크 점수는 코드에 박지 않고 사용자가 설정으로 갱신한다(§5.5·§7).
    "active_profile": "",           # "" | best | balanced | cost | speed  (yok3x profile <mode>)
    "models_catalog": {             # 논리모델명 → (backend, model_id). id 비면 CLI 기본
        "fable-5":    {"backend": "claude", "model": "claude-fable-5"},
        "opus-4.8":   {"backend": "claude", "model": "claude-opus-4-8"},
        "sonnet-5":   {"backend": "claude", "model": "claude-sonnet-5"},
        "haiku-4.5":  {"backend": "claude", "model": "claude-haiku-4-5-20251001"},
        # 아래 논리모델은 '프로파일 라우팅'용 매핑일 뿐(GUI 드롭다운은 backend별 동적 조회 사용).
        "gpt-5.6":    {"backend": "codex",  "model": "gpt-5.6-sol"},          # codex 모델 slug
        "gemini-3-flash": {"backend": "gemini", "model": "gemini-3-flash-preview"}
    },
    "situations": {                 # task_kind → 상황 슬롯(프로파일 키)
        "critic": "review", "review": "review",
        "build": "build", "refactor": "build", "test": "build",
        "design_review": "design", "general": "build"
    },
    "profiles": {                   # mode → 상황 → 논리모델. "*"=기본, 특정 상황만 오버라이드
        # best: "_derive" → 상황마다 benchmarks 최고점 모델을 자동 채택(데이터 갱신만으로 최신화).
        # 벤치마크가 없는 상황은 "*"로 폴백.
        "best":     {"_derive": True, "*": "fable-5"},
        "balanced": {"review": "opus-4.8", "build": "gpt-5.6", "design": "gemini-3-flash", "*": "opus-4.8"},
        "cost":     {"design": "gemini-3-flash", "*": "sonnet-5"},
        "speed":    {"design": "gemini-3-flash", "*": "haiku-4.5"}
    },
    # 상황별 벤치마크 점수(S2 폴백 순위 근거). 프로파일 픽의 backend가 미설치/한도stop이면
    # 이 표에서 '가용한 다음 순위'로 폴백한다. 점수는 코드가 아니라 설정 — 사용자가 갱신(§7).
    "benchmarks": {
        "review": {"fable-5": 80.0, "opus-4.8": 69.2, "gpt-5.6": 64.6, "sonnet-5": 63.2},   # 정확한 패치
        "build":  {"gpt-5.6": 88.8, "fable-5": 83.1, "sonnet-5": 80.4, "opus-4.8": 78.9},   # 터미널/빌드
        "design": {"gemini-3-flash": 83.6, "opus-4.8": 77.8}                                     # 도구/설계
    },
    "flavors": {
        "claude-orchestrator": {
            "orchestrator": "claude-main",
            "workers": ["claude-main", "codex-main", "codex-critic", "gemini"]
        },
        "codex-orchestrator": {
            "orchestrator": "codex-main",
            "workers": ["codex-main", "claude-main", "codex-critic", "gemini"]
        },
        "gemini-orchestrator": {
            "orchestrator": "gemini",
            "workers": ["gemini", "claude-main", "codex-main", "codex-critic"]
        }
    },
    "workers": {
        # worker → backend + 역할 시스템 프롬프트 (코딩 워크플로우)
        "claude-main": {
            "backend": "claude",
            "role": "구현 담당. 요청된 기능의 완성 코드를 작성해 코드블록으로 응답한다(파일 편집 아님)."
        },
        "codex-main": {
            "backend": "codex",
            "role": "구현·리팩터 담당. 요청된 코드를 작성/개선해 코드블록으로 응답한다(파일 편집 아님)."
        },
        "codex-critic": {
            "backend": "codex",
            "role": "코드 리뷰어. 버그·엣지케이스·보안·성능·가독성을 점검한다. 반드시 첫 줄에 'SCORE: <0-10>'을 쓰고, 이어서 결함 목록과 구체적 수정 지시를 쓴다. 통과 기준을 못 넘으면 낮은 점수를 준다."
        },
        "gemini": {
            "backend": "gemini",
            "role": "설계/코드 검토 담당. 구조·아키텍처·요구사항 충족을 검토하고 반드시 첫 줄에 'SCORE: <0-10>'을 쓴다."
        },
        "local-main": {
            "backend": "local",
            "role": "오프라인 폴백 구현 담당. 요청된 코드를 작성해 코드블록으로 응답한다(파일 편집 아님)."
        }
    }
}

# 요금제 프리셋 — plan 이름 → 한도. codex는 app-server가 planType을 직접 주므로 프리셋 불필요(자동).
# 주의: Anthropic/Google은 정확한 토큰 상한을 공개하지 않는다. 아래는 '근사 시작값'이며,
# 정확도는 `yok3x calibrate <tool> <5h|7d> <실제%>`로 본인 실사용에 맞춰 역산하는 것이 정답이다.
# (우리 토큰 집계는 캐시 read를 포함해 값이 크므로, 캘리브레이트로 같은 단위에 맞추는 것이 핵심.)
PLAN_PRESETS = {
    "claude": {
        # (5시간, 7일) 근사 토큰 상한. 비율 pro:max5x:max20x ≈ 1:5:20 (2026-05 한도 상향 반영 근사)
        "pro":    {"limit_5h_tokens": 8_000_000,   "limit_7d_tokens": 100_000_000},
        "max5x":  {"limit_5h_tokens": 40_000_000,  "limit_7d_tokens": 500_000_000},
        "max20x": {"limit_5h_tokens": 160_000_000, "limit_7d_tokens": 2_000_000_000},
    },
    "gemini": {
        # 일일 호출·토큰 근사(ledger 예산에 반영)
        "free": {"daily_calls": 1000,  "daily_tokens": 4_000_000},
        "paid": {"daily_calls": 10000, "daily_tokens": 50_000_000},
    },
}


# backends.json — MCP / CLI / native 호출 방식 어댑터 구조.
# type: cli    → command 템플릿을 subprocess로 실행 ({prompt}, {prompt_file} 치환)
# type: native → HTTP API 직접 호출(자리 표시; endpoint/model 설정)
# type: mcp    → MCP 서버 경유(자리 표시; server 설정)
# type: mock   → 외부 도구 없이 로컬에서 시뮬레이션(테스트/드라이런)
DEFAULT_BACKENDS = {
    "claude": {
        "type": "cli",
        # 검증 근거: https://code.claude.com/docs/en/headless
        # --disallowedTools: 파일시스템/실행 도구를 막아 헤드리스 claude가 에이전트 모드로
        # 파일을 뒤지거나(brief.md 등) 쓰기 권한 승인을 기다리며 멈추는 대신, 프롬프트에
        # 주입된 컨텍스트만으로 '코드 텍스트를 1턴에 반환'하게 한다. yok3x는 context_globs로
        # 레포 컨텍스트를 이미 프롬프트에 넣으므로 claude가 Read/Glob을 쓸 필요가 없다.
        # (콤마 구분 단일 인자 — 공백 구분은 인자 파싱이 깨진다)
        # 프롬프트는 stdin으로 전달({prompt} 없음) — Windows .cmd 심의 멀티라인 argv 잘림 회피.
        "command": ["claude", "-p", "--output-format", "json",
                    "--disallowedTools", "Bash,Edit,Write,Read,Glob,Grep,TodoWrite,WebFetch"],
        "model_arg": ["--model", "{model}"],   # 다운그레이드 시 덧붙는 인자
        "effort_arg": ["--effort", "{effort}"],  # 추론 강도(low/medium/high) — 워커 effort 지정 시
        # ACQUIRE 조사 시 Read/Glob/Grep/Bash는 허용하고 쓰기 도구만 차단한다.
        "read_only_arg": ["--disallowedTools", "Edit,Write,MultiEdit,NotebookEdit"],
        # v4.1.0 MCP 워커도구(a1): mcp_policy가 화이트리스트+승인을 거쳐 판정한 grant가 있을 때만
        # 주입된다({mcp_config_path}=임시 JSON 파일, {allowed_tools}=콤마구분 mcp__server__tool 목록).
        # 이 템플릿이 없는 backend(codex/gemini 등)는 MCP 요청이 자동으로 fail-closed된다
        # (mcp_policy.resolve_mcp_grant가 아니라 _run_cli의 `spec.get("mcp_arg")` 부재로 무시).
        "mcp_arg": ["--mcp-config", "{mcp_config_path}", "--allowedTools", "{allowed_tools}"],
        "parser": "claude_json",
        "timeout_sec": 600
    },
    "codex": {
        "type": "cli",
        # 검증 근거: https://developers.openai.com/codex/noninteractive
        "command": ["codex", "exec", "--json", "--skip-git-repo-check"],   # 프롬프트는 stdin
        "model_arg": ["--model", "{model}"],
        "effort_arg": ["-c", "model_reasoning_effort={effort}"],  # 추론 강도(minimal/low/medium/high)
        "read_only_arg": ["--sandbox", "read-only"],
        "parser": "codex_jsonl",
        "timeout_sec": 600
    },
    "gemini": {
        "type": "cli",
        # 검증 근거: https://geminicli.com/docs/cli/headless/
        # --skip-trust: gemini 0.44+는 '신뢰되지 않은 디렉터리'에서 실행 거부(exit 55). 헤드리스로
        # 임의 워크스페이스/격리 dir에서 돌리려면 필수(codex의 --skip-git-repo-check 격).
        "command": ["gemini", "--output-format", "json", "--skip-trust"],   # 프롬프트는 stdin
        "model_arg": ["--model", "{model}"],   # 프로파일/다운그레이드 시 모델 주입
        "read_only_arg": ["--approval-mode", "plan"],
        "parser": "gemini_json",
        "timeout_sec": 600
    },
    "local": {
        # OpenAI 호환 로컬 서버(llama.cpp·LM Studio·vLLM·Ollama /v1 등). 의존성 0(urllib).
        # P3 오프라인 폴백: 클라우드 전부 한도 소진 시 여기로 강등해 무중단. 로컬=무료(cost 0).
        "type": "openai_http",
        "base_url": "http://localhost:8000/v1",  # 로컬 서버 주소(포트만 다르면 여기만 수정)
        "model": "",                # 비우면 서버 로드 모델 사용(/v1/models 첫 항목)
        "timeout_sec": 180,
        "temperature": 0.2
    },
    "mock": {
        "type": "mock",
        "latency_sec": 0.1
    }
}


def atomic_write_text(path: "Path | str", text: str, encoding: str = "utf-8") -> None:
    """텍스트 파일을 **원자적**으로 쓴다(임시파일 → replace).

    F2-5: 같은 패턴이 여러 곳에 흩어져 있었고 일부 경로는 아예 비원자였다. `write_text`는
    truncate-then-write라 그 사이에 프로세스가 죽으면 **0바이트 파일**이 남는다 — 실제로
    yok3x.json이 그렇게 손상돼 전체 기동이 막힌 적이 있다(BUG-32). pid 접미사 tmp를 써서
    동시 저장 경합도 피한다. replace()는 같은 볼륨에서 원자적이다.
    """
    p = Path(path)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding=encoding)
        tmp.replace(p)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)      # 중간 산출물을 남기지 않는다
        except OSError:
            pass
        raise


@dataclass
class Paths:
    root: Path
    yok3x_dir: Path
    runs: Path
    logs: Path
    knowledge: Path
    usage_file: Path
    yok3x_json: Path
    backends_json: Path

    @classmethod
    def at(cls, root: str | Path = ".") -> "Paths":
        root = Path(root).resolve()
        h = root / YOK3X_DIR_NAME
        return cls(
            root=root,
            yok3x_dir=h,
            runs=h / "runs",
            logs=h / "logs",
            knowledge=root / "knowledge",
            usage_file=h / "usage.jsonl",
            yok3x_json=root / "yok3x.json",
            backends_json=root / "backends.json",
        )


@dataclass
class Config:
    paths: Paths
    yok3x: dict[str, Any] = field(default_factory=dict)
    backends: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, root: str | Path = ".") -> "Config":
        p = Paths.at(root)
        # deepcopy: 얕은 복사면 중첩 dict(workers 등)가 모듈 전역 DEFAULT_*를 그대로
        # 가리켜, 이후 변형(scaffold의 backend=mock, 부분 yok3x.json 로드)이 전역을 오염시킨다.
        yok3x = copy.deepcopy(DEFAULT_YOK3X)
        backends = copy.deepcopy(DEFAULT_BACKENDS)
        # utf-8-sig: 윈도우 메모장·PowerShell(Out-File utf8)이 붙이는 BOM을 투명 제거.
        # BOM이 있든 없든 정상 파싱된다(쓰기는 BOM 없는 utf-8 유지).
        yok3x = _deep_merge(yok3x, _load_json_or_empty(p.yok3x_json, logger))
        backends = _deep_merge(backends, _load_json_or_empty(p.backends_json, logger))
        return cls(paths=p, yok3x=yok3x, backends=backends)

    def save_yok3x(self) -> None:
        """설정 저장. **원자적**(임시파일 후 replace) — 저장 도중 프로세스가 죽어도(강제종료 등)
        (구현은 모듈 함수 `atomic_write_text` 재사용 — F2-5에서 같은 패턴 3곳을 하나로 모았다.)
        write_text의 truncate-then-write 구간에 걸리면 파일이 0바이트로 남는 torn write를 방지한다
        (실제로 GUI 프로세스 강제종료 중 발생 확인됨). pid 고유 tmp로 동시 저장 경합도 피한다."""
        atomic_write_text(self.paths.yok3x_json,
                          json.dumps(self.yok3x, ensure_ascii=False, indent=2) + "\n")

    def worker(self, name: str) -> dict[str, Any]:
        w = self.yok3x["workers"].get(name)
        if not w:
            raise KeyError(f"unknown worker: {name} (available: {list(self.yok3x['workers'])})")
        return w

    def flavor(self) -> dict[str, Any]:
        f = self.yok3x["flavor"]
        return self.yok3x["flavors"][f]

    def ensure_dirs(self) -> None:
        for d in (self.paths.yok3x_dir, self.paths.runs, self.paths.logs, self.paths.knowledge):
            d.mkdir(parents=True, exist_ok=True)


def _load_json_or_empty(path: Path, log: logging.Logger) -> dict:
    """설정 파일을 읽는다. 없으면 {}. **있는데 손상**(torn write로 0바이트 등)이면 크래시 대신 {}로
    폴백해 기본값이 적용되게 한다 — 단, 조용히 삼키지 않고 경고 로그를 남긴다(§5.5: 값을 지어내진
    않되 — 폴백은 기본값이지 조작값이 아님 — 도구 전체가 죽는 것보다 낫다). save_yok3x는 원자적
    쓰기(임시파일+replace)라 이 경로는 정상 운영에선 안 밟히지만, 과거 비원자적 쓰기 중 강제종료로
    실제 발생을 확인했다(BUG 참고: config.py 히스토리)."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        log.warning("%s 파싱 실패(손상됨, %s) — 기본값으로 폴백. 백업(.bak)이 있으면 확인하라.",
                    path, e)
        return {}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def scaffold(root: str | Path = ".", use_mock: bool = False) -> Config:
    """`yok3x init`: 설정 파일과 디렉터리 생성."""
    cfg = Config.load(root)
    cfg.ensure_dirs()
    if use_mock:
        for w in cfg.yok3x["workers"].values():
            w["backend"] = "mock"
    # F2-5: scaffold도 save_yok3x와 같은 파일을 쓴다 — 여기만 비원자면 init 도중 종료 시
    # BUG-32와 동일한 0바이트 손상이 난다. 같은 원자적 헬퍼로 통일.
    if not cfg.paths.yok3x_json.exists():
        atomic_write_text(cfg.paths.yok3x_json,
                          json.dumps(cfg.yok3x, ensure_ascii=False, indent=2) + "\n")
    if not cfg.paths.backends_json.exists():
        atomic_write_text(cfg.paths.backends_json,
                          json.dumps(cfg.backends, ensure_ascii=False, indent=2) + "\n")
    ctx = cfg.paths.root / "context.md"
    brief = cfg.paths.root / "brief.md"
    if not ctx.exists():
        ctx.write_text("# context.md\n\n(에이전트 공유 컨텍스트 — 글자 제한 적용)\n", encoding="utf-8")
    if not brief.exists():
        brief.write_text("# brief.md\n\n(현재 작업 요약 — 글자 제한 적용)\n", encoding="utf-8")
    return cfg
