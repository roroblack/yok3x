"""yok3x config: yok3x.json(전역 설정) + backends.json(백엔드 어댑터) 로딩.

환경변수를 쓰지 않는다. 설정 파일 한 줄로 모든 동작을 제어한다(mat 원칙).
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

YOK3X_DIR_NAME = ".yok3x"

DEFAULT_YOK3X = {
    "version": "3.1",
    "flavor": "claude-orchestrator",
    "auto_approve": False,          # 승인 게이트 기본값: 사람이 y/n
    "context_max_chars": 8000,      # context.md 글자 제한
    "brief_max_chars": 1200,        # brief.md 글자 제한
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
            "max_failovers_per_run": 3      # 런당 전환 상한(스래싱 방지). sticky로 왕복도 방지
        }
    },
    # 진짜 구독 한도 조회 어댑터 — 서버 보고 사용률을 읽어 '한도 무조건 준수'.
    # codex : app-server JSON-RPC 로 '지금 이 순간' 5h/7d used_percent 라이브 조회(진짜 실측).
    #         실패 시 세션 파일(stale) → 원장 순으로 폴백.
    # claude: Anthropic이 사용률 %를 노출 안 함 → transcript 롤링 합산으로 cap 대비 '추정'.
    #         limit_*_tokens 를 본인 플랜값으로 넣어야 활성화(0=원장 폴백).
    # gemini: CLI가 잔여 한도를 노출 안 함 → 원장(자체 일일 예산)으로 준수. command 로 외부도구 연결 가능.
    "limits": {
        "claude": {
            # 라이브 실측: Max/Pro 구독 OAuth 토큰으로 /api/oauth/usage 조회(5h/7d used% + 리셋).
            # codex의 app-server 실측에 대응. 실패 시 트랜스크립트 추정 → 원장으로 명시적 열화.
            "type": "claude_oauth",     # 추정만 원하면 "claude_transcripts", 끄려면 "ledger"
            "min_interval_sec": 60,     # 실측 재조회 최소 간격(usage 엔드포인트 rate-limit 배려)
            "plan": "",                 # 추정 폴백용 상한 프리셋: pro | max5x | max20x
            "limit_5h_tokens": 0,       # 추정 폴백 직접 상한(plan보다 우선). `yok3x calibrate`로 보정
            "limit_7d_tokens": 0,
            # 적응형 열화 다운그레이드 대상(guard.degrade). lite=한도 근처에서 낮출 가벼운 모델
            "models": {"full": "", "lite": "claude-haiku-4-5-20251001"}
        },
        "codex": {
            "type": "codex_appserver",  # 라이브 실측. codex_bin/app_server_args/timeout_sec 조정 가능
            "timeout_sec": 15,
            "models": {"full": "", "lite": ""}   # codex 다운그레이드 모델(원하면 지정)
        },
        "gemini": {
            "type": "ledger"            # 라이브 실측 불가 → 원장. command probe로 tokscale 등 연결 가능
        }
    },
    "budgets": {                    # 실측 한도가 없을 때의 자체 안전망(로컬 자정 리셋)
        "claude": {"daily_usd": 5.0, "daily_calls": 200},
        "codex":  {"daily_tokens": 2000000, "daily_calls": 200},
        "gemini": {"daily_tokens": 2000000, "daily_calls": 200}
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
        "gpt-5.6":    {"backend": "codex",  "model": ""},   # 자기 환경 model_id로 채우기
        "gemini-3.5": {"backend": "gemini", "model": ""}
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
        "balanced": {"review": "opus-4.8", "build": "gpt-5.6", "design": "gemini-3.5", "*": "opus-4.8"},
        "cost":     {"design": "gemini-3.5", "*": "sonnet-5"},
        "speed":    {"design": "gemini-3.5", "*": "haiku-4.5"}
    },
    # 상황별 벤치마크 점수(S2 폴백 순위 근거). 프로파일 픽의 backend가 미설치/한도stop이면
    # 이 표에서 '가용한 다음 순위'로 폴백한다. 점수는 코드가 아니라 설정 — 사용자가 갱신(§7).
    "benchmarks": {
        "review": {"fable-5": 80.0, "opus-4.8": 69.2, "gpt-5.6": 64.6, "sonnet-5": 63.2},   # 정확한 패치
        "build":  {"gpt-5.6": 88.8, "fable-5": 83.1, "sonnet-5": 80.4, "opus-4.8": 78.9},   # 터미널/빌드
        "design": {"gemini-3.5": 83.6, "opus-4.8": 77.8}                                     # 도구/설계
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
        "parser": "claude_json",
        "timeout_sec": 600
    },
    "codex": {
        "type": "cli",
        # 검증 근거: https://developers.openai.com/codex/noninteractive
        "command": ["codex", "exec", "--json", "--skip-git-repo-check"],   # 프롬프트는 stdin
        "model_arg": ["--model", "{model}"],
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
        "parser": "gemini_json",
        "timeout_sec": 600
    },
    "mock": {
        "type": "mock",
        "latency_sec": 0.1
    }
}


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
        if p.yok3x_json.exists():
            yok3x = _deep_merge(yok3x, json.loads(p.yok3x_json.read_text(encoding="utf-8-sig")))
        if p.backends_json.exists():
            backends = _deep_merge(backends, json.loads(p.backends_json.read_text(encoding="utf-8-sig")))
        return cls(paths=p, yok3x=yok3x, backends=backends)

    def save_yok3x(self) -> None:
        self.paths.yok3x_json.write_text(
            json.dumps(self.yok3x, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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
    if not cfg.paths.yok3x_json.exists():
        cfg.paths.yok3x_json.write_text(
            json.dumps(cfg.yok3x, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not cfg.paths.backends_json.exists():
        cfg.paths.backends_json.write_text(
            json.dumps(cfg.backends, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ctx = cfg.paths.root / "context.md"
    brief = cfg.paths.root / "brief.md"
    if not ctx.exists():
        ctx.write_text("# context.md\n\n(에이전트 공유 컨텍스트 — 글자 제한 적용)\n", encoding="utf-8")
    if not brief.exists():
        brief.write_text("# brief.md\n\n(현재 작업 요약 — 글자 제한 적용)\n", encoding="utf-8")
    return cfg
