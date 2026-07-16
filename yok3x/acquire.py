# ref: ACQUIRE(Know-Before-Fix), 상하이교통대 2026-07-13 — 수정 전 QA 지식 선수집
"""수정 전에 필요한 저장소 지식을 질문·검증·렌더링하는 순수 모듈."""
from __future__ import annotations

import json
import re


QUESTION_CATEGORIES = {
    "mechanism": "Mechanism & Behavior — 실제 로직·상태변화·데이터 흐름이 어떻게 동작하는가",
    "design": "Design & Usage — API 계약·클래스 구조·오류 규칙·설계 관례는 무엇인가",
    "locating": "Locating & Structure — 기능이 어디에 구현되어 있고 모듈들이 어떻게 연결되는가",
    "ecosystem": "Ecosystem & Standards — 외부 라이브러리·프로토콜·언어 명세가 요구하는 것은 무엇인가",
}

_LEAK_RE = re.compile(
    r"수정하|고쳐|패치|should\s+be|replace|the\s+fix\s+is|change\s+.*?\s+to",
    re.IGNORECASE,
)
_UNKNOWN_ANSWER_RE = re.compile(
    r"모른|알\s*수\s*없|확인(?:할\s*수\s*없|하지\s*못)|"
    r"파악(?:할\s*수\s*없|하지\s*못)|찾(?:을\s*수\s*없|지\s*못)|"
    r"unknown|do\s+not\s+know|cannot\s+(?:determine|find|confirm)",
    re.IGNORECASE,
)


def build_questioner_prompt(issue: str, qa_count: int = 2) -> str:
    """Questioner가 수정 전 지식 질문만 만들도록 프롬프트를 구성한다."""
    categories = "\n".join(
        f'- "{key}": {description}'
        for key, description in QUESTION_CATEGORIES.items()
    )
    return (
        "당신은 수정 전 저장소 지식 수집을 담당하는 Questioner다.\n"
        f"이슈 해결에 필요한 '저장소에 관해 알아야 할 모르는 지식'을 정확히 {qa_count}개 질문으로 분해하라.\n"
        "해결책·패치·수정 위치·구현 방법을 제안하지 마라. 조기 가설을 고정하지 말고 "
        "오직 '무엇을 알아야 하는가'만 질문하라.\n"
        "아래 4범주를 참고하되 한 범주에 몰리지 말고, 가능하면 서로 다른 범주를 사용하라.\n"
        f"{categories}\n"
        "출력은 엄격한 JSON 배열만 허용한다. 설명이나 코드펜스를 덧붙이지 마라.\n"
        '[{"category":"<키>","question":"..."}, ...]\n'
        f"이슈:\n{issue}"
    )


def _first_json(raw: str, opener: str, expected_type: type):
    """잡텍스트 안에서 처음으로 완전히 해석되는 JSON 값을 찾는다."""
    if not isinstance(raw, str):
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != opener:
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(value, expected_type):
            return value
    return None


def parse_questions(raw: str, qa_count: int = 2) -> list[dict]:
    """Questioner 출력에서 질문 배열을 추출하고 안전한 형태로 정규화한다."""
    values = _first_json(raw, "[", list)
    if values is None:
        return []

    questions: list[dict] = []
    limit = max(0, qa_count)
    if limit == 0:
        return questions
    for value in values:
        if not isinstance(value, dict):
            continue
        category_raw = value.get("category")
        category = category_raw if category_raw in QUESTION_CATEGORIES else "mechanism"
        question_raw = value.get("question", "")
        question = question_raw.strip() if isinstance(question_raw, str) else str(question_raw or "").strip()
        item = {"category": category, "question": question}
        if category != category_raw:
            item["category_raw"] = category_raw
        if _LEAK_RE.search(question):
            item["leak"] = True
        questions.append(item)
        if len(questions) >= limit:
            break
    return questions


def build_answerer_prompt(issue: str, question: dict, repo_hint: str = "") -> str:
    """한 질문을 독립적으로 조사할 Answerer 프롬프트를 구성한다."""
    hint = f"\n저장소 힌트(출발점일 뿐이며 직접 확인할 것):\n{repo_hint}" if repo_hint else ""
    return (
        "당신은 수정 전 저장소 조사를 담당하는 Answerer다.\n"
        "읽기 전용으로 셸을 사용해 저장소를 탐색하고 근거를 찾아라. 코드를 수정하지 마라.\n"
        "답변에는 구체 근거인 파일 경로, 함수/클래스명(symbol), 실제 코드 동작을 반드시 포함하라. "
        "행번호보다 symbol을 우선하라.\n"
        "근거를 못 찾으면 추측하지 말고 unknowns에 명시하라.\n"
        "다른 질문이나 그 답을 참조하지 말고, 아래 질문만 독립적으로 조사하라.\n"
        "출력은 다음 스키마의 엄격한 JSON 객체만 허용한다. 설명이나 코드펜스를 덧붙이지 마라.\n"
        '{"answer":"요지",'
        '"evidence":[{"path":"...","symbol":"...","observation":"..."}],'
        '"confidence":"high|medium|low","unknowns":[],"alternative_hypotheses":[]}\n'
        f"이슈:\n{issue}\n"
        f"질문 범주: {question.get('category', 'mechanism')}\n"
        f"질문: {question.get('question', '')}"
        f"{hint}"
    )


def validate_answer(obj: dict) -> tuple[bool, str]:
    """QA 답변 스키마를 검증하고 교정 가능한 confidence는 보정한다."""
    if not isinstance(obj, dict):
        return False, "answer 누락/빈값"

    answer = obj.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return False, "answer 누락/빈값"
    unknowns = obj.get("unknowns")
    if not isinstance(unknowns, list):
        return False, "unknowns는 리스트여야 함"
    alternatives = obj.get("alternative_hypotheses")
    if not isinstance(alternatives, list):
        return False, "alternative_hypotheses는 리스트여야 함"

    if obj.get("confidence", "medium") not in {"high", "medium", "low"}:
        obj["confidence"] = "medium"
    elif "confidence" not in obj:
        obj["confidence"] = "medium"

    evidence = obj.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        if unknowns and _UNKNOWN_ANSWER_RE.search(answer):
            return True, "unknown"
        return False, "근거(evidence) 최소 1개 필요"
    for item in evidence:
        if not isinstance(item, dict):
            return False, "근거(evidence) 항목 형식 오류"
        path = item.get("path")
        observation = item.get("observation")
        if not isinstance(path, str) or not path.strip():
            return False, "근거(evidence) 항목 형식 오류"
        if not isinstance(observation, str):
            return False, "근거(evidence) 항목 형식 오류"
    return True, ""


def parse_answer(raw: str) -> dict | None:
    """Answerer 출력에서 첫 JSON 객체를 추출하고 누락 기본값을 채운다."""
    obj = _first_json(raw, "{", dict)
    if obj is None:
        return None
    obj.setdefault("confidence", "medium")
    obj.setdefault("unknowns", [])
    obj.setdefault("alternative_hypotheses", [])
    return obj


def _split_qa_item(item: dict) -> tuple[dict, dict]:
    """병합형과 {question, answer} 중첩형 QA를 같은 형태로 읽는다."""
    question_value = item.get("question")
    answer_value = item.get("answer")
    if isinstance(question_value, dict):
        question = question_value
    else:
        question = {"category": item.get("category"), "question": question_value}
    if isinstance(answer_value, dict):
        answer = answer_value
    else:
        answer = {
            "answer": answer_value,
            "evidence": item.get("evidence"),
            "confidence": item.get("confidence", "medium"),
            "unknowns": item.get("unknowns"),
            "alternative_hypotheses": item.get("alternative_hypotheses"),
        }
    return question, answer


def render_qa_context(issue: str, qa_items: list[dict], max_chars: int = 4000) -> str:
    """검증된 QA만 Resolver가 재확인할 선수집 지식 블록으로 렌더링한다."""
    issue_summary = " ".join(str(issue).split())
    lines = [
        "[선수집 지식 — 수정 전 참고, 맹신 금지: 저장소를 다시 확인하라]",
        f"이슈: {issue_summary}",
        "",
    ]
    number = 0
    for item in qa_items:
        if not isinstance(item, dict):
            continue
        question, answer = _split_qa_item(item)
        valid, _ = validate_answer(answer)
        if not valid:
            continue
        number += 1
        category = question.get("category", "mechanism")
        category_name = QUESTION_CATEGORIES.get(category, QUESTION_CATEGORIES["mechanism"])
        lines.append(f"Q{number} ({category_name}): {question.get('question', '')}")
        lines.append(f"A{number}: {answer['answer']}")
        for evidence in answer.get("evidence") or []:
            symbol = evidence.get("symbol")
            location = f"{evidence['path']}::{symbol}" if symbol else evidence["path"]
            lines.append(f"  근거: {location} — {evidence['observation']}")
        if answer.get("unknowns"):
            lines.append(f"  미상: {'; '.join(str(value) for value in answer['unknowns'])}")
        if answer.get("alternative_hypotheses"):
            alternatives = "; ".join(str(value) for value in answer["alternative_hypotheses"])
            lines.append(f"  대안가설: {alternatives}")
        lines.append("")
    lines.append(
        "[위 QA는 이슈별 일시 메모리다. 수정 전 QA마다 핵심 주장 하나를 직접 재확인하라"
        "(confirmed/partial/contradicted). contradicted면 폐기, partial이면 위치 힌트로만 사용하라.]"
    )
    rendered = "\n".join(lines)
    if len(rendered) <= max_chars:
        return rendered
    if max_chars <= 0:
        return ""
    marker = "… (컨텍스트 상한으로 일부 생략)"
    if max_chars <= len(marker):
        return marker[:max_chars]
    return rendered[:max_chars - len(marker)] + marker
