import json

from yok3x.acquire import (
    QUESTION_CATEGORIES,
    build_answerer_prompt,
    build_questioner_prompt,
    parse_answer,
    parse_questions,
    render_qa_context,
    validate_answer,
)


def _valid_answer():
    return {
        "answer": "Config.load가 설정을 읽는다.",
        "evidence": [{
            "path": "yok3x/config.py",
            "symbol": "Config.load",
            "observation": "JSON 설정을 읽어 Config를 만든다.",
        }],
        "confidence": "high",
        "unknowns": [],
        "alternative_hypotheses": [],
    }


def test_build_questioner_prompt_contains_count_ban_and_categories():
    prompt = build_questioner_prompt("설정 오류", qa_count=3)
    assert "정확히 3개" in prompt
    assert "해결책·패치·수정 위치·구현 방법을 제안하지 마라" in prompt
    assert "엄격한 JSON 배열" in prompt
    assert all(key in prompt for key in QUESTION_CATEGORIES)
    assert all(description in prompt for description in QUESTION_CATEGORIES.values())


def test_parse_questions_normal_json_and_limit():
    raw = json.dumps([
        {"category": "mechanism", "question": "어떻게 흐르는가?"},
        {"category": "design", "question": "계약은 무엇인가?"},
        {"category": "locating", "question": "어디에 있는가?"},
    ], ensure_ascii=False)
    result = parse_questions(raw, qa_count=2)
    assert result == [
        {"category": "mechanism", "question": "어떻게 흐르는가?"},
        {"category": "design", "question": "계약은 무엇인가?"},
    ]


def test_parse_questions_accepts_code_fence_and_junk():
    raw = "설명 [배열 아님]\n```json\n[{\"category\":\"locating\",\"question\":\"구조는?\"}]\n``` 끝"
    assert parse_questions(raw) == [{"category": "locating", "question": "구조는?"}]


def test_parse_questions_falls_back_category_and_marks_leak():
    raw = '[{"category":"other","question":"Change old value to new value"}]'
    assert parse_questions(raw) == [{
        "category": "mechanism",
        "category_raw": "other",
        "question": "Change old value to new value",
        "leak": True,
    }]


def test_parse_questions_failure_and_zero_limit_return_empty():
    assert parse_questions("JSON이 전혀 없음") == []
    assert parse_questions('[{"category":"design","question":"계약?"}]', qa_count=0) == []


def test_build_answerer_prompt_requires_read_only_evidence_and_independence():
    prompt = build_answerer_prompt(
        "설정 오류",
        {"category": "design", "question": "오류 규칙은?"},
        repo_hint="yok3x/config.py",
    )
    assert "읽기 전용" in prompt and "코드를 수정하지 마라" in prompt
    assert "파일 경로" in prompt and "symbol" in prompt and "실제 코드 동작" in prompt
    assert "추측하지 말고 unknowns" in prompt
    assert "다른 질문이나 그 답을 참조하지 말고" in prompt
    assert "yok3x/config.py" in prompt and "엄격한 JSON 객체" in prompt


def test_validate_answer_accepts_valid_answer():
    assert validate_answer(_valid_answer()) == (True, "")


def test_validate_answer_rejects_missing_evidence():
    answer = _valid_answer()
    answer["evidence"] = []
    assert validate_answer(answer) == (False, "근거(evidence) 최소 1개 필요")


def test_validate_answer_accepts_honest_unknown_without_evidence():
    answer = _valid_answer()
    answer.update(answer="저장소에서 확인할 수 없어 모른다.", evidence=[], unknowns=["구현체 위치"])
    assert validate_answer(answer) == (True, "unknown")


def test_validate_answer_rejects_empty_answer():
    answer = _valid_answer()
    answer["answer"] = "  "
    assert validate_answer(answer) == (False, "answer 누락/빈값")


def test_validate_answer_rejects_non_list_unknowns():
    answer = _valid_answer()
    answer["unknowns"] = "없음"
    assert validate_answer(answer) == (False, "unknowns는 리스트여야 함")


def test_validate_answer_corrects_invalid_confidence():
    answer = _valid_answer()
    answer["confidence"] = "certain"
    assert validate_answer(answer) == (True, "")
    assert answer["confidence"] == "medium"


def test_parse_answer_extracts_junk_wrapped_object_and_defaults():
    raw = '앞 설명 {"answer":"요지","evidence":[]} 뒤 설명'
    result = parse_answer(raw)
    assert result == {
        "answer": "요지",
        "evidence": [],
        "confidence": "medium",
        "unknowns": [],
        "alternative_hypotheses": [],
    }


def test_parse_answer_failure_returns_none():
    assert parse_answer("객체가 아니다: [1, 2]") is None


def test_render_qa_context_contains_qa_evidence_and_recheck_instruction():
    item = {
        "category": "design",
        "question": "설정 계약은?",
        **_valid_answer(),
    }
    item["unknowns"] = ["환경 변수 우선순위"]
    item["alternative_hypotheses"] = ["기본값이 먼저 적용될 수 있음"]
    rendered = render_qa_context("설정 오류", [item])
    assert "[선수집 지식" in rendered and "이슈: 설정 오류" in rendered
    assert "Q1 (Design & Usage" in rendered and "설정 계약은?" in rendered
    assert "yok3x/config.py::Config.load" in rendered
    assert "미상: 환경 변수 우선순위" in rendered
    assert "대안가설: 기본값이 먼저 적용될 수 있음" in rendered
    assert "confirmed/partial/contradicted" in rendered
    assert "contradicted면 폐기" in rendered


def test_render_qa_context_honors_max_chars_and_marks_truncation():
    item = {
        "category": "mechanism",
        "question": "긴 질문" * 100,
        **_valid_answer(),
    }
    rendered = render_qa_context("긴 이슈" * 100, [item], max_chars=180)
    assert len(rendered) <= 180
    assert rendered.endswith("… (컨텍스트 상한으로 일부 생략)")
