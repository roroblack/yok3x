# BUG-48 · reviewer 산출물 클립(6000자)이 너무 작아 완전하고 통과하는 코드도 오탈락

- **상태**: 수정 완료 (2026-08-16)
- **심각도**: 중간(리뷰 점수를 조용히 왜곡 — verify는 정상인데 score만 낮게 나옴, T-2 캘리브레이션 데이터도 오염)
- **영역**: `yok3x/orchestrator.py` `run_producer_reviewer()`(reviewer/revision 산출물 클립), `yok3x/config.py` `DEFAULT_YOK3X`

## 증상

T-2 2차 캘리브레이션 작업 #11(BoundedWorkerPool, stdlib threading/queue)을 실행했더니
`verify=ok`(pytest 14개 전부 통과)인데도 codex-critic이 2라운드 모두 `SCORE: 4/10`을 줘
`score_gate_mode=strict` 게이트를 통과하지 못했다.

## 진단

`run_producer_reviewer()`가 reviewer에게 보여줄 산출물을 다음처럼 잘랐다(수정 전):

```python
rev_blocks = [f"[산출물]\n{knot.clip(artifact, 6000)}"]
```

producer 원문(설명 + 코드 + SELF-CHECK)이 7,415자였는데 6,000자로 잘렸다. `knot.clip`은
잘림을 `"…(글자 제한으로 중간 생략)…"`로 명시하므로 이건 "조용한" 버그는 아니다 —
실제로 codex-critic은 이 마커를 정확히 읽고 "제출된 코드가 글자 제한으로 중간 생략되어
독립 검증이 불가능합니다"라고 명시했다. 즉 리뷰어는 정직하게 반응했지만, **6000자라는
예산 자체가 흔한 단일 파일 구현체 하나를 통째로 보여주기엔 너무 작았다.**

실제로 산출된 `workerpool.py`(238줄)를 직접 꺼내 독립적으로 `pytest`를 돌리면 14개 전부
통과한다 — 코드 자체는 완전하고 정상 동작한다. 리뷰가 본 건 잘린 절반짜리 산출물이었다.

부수적으로 라운드 재작업 시 producer에게 보여주는 "직전 산출물"도 같은 패턴으로
4,000자에서 잘렸다(`{knot.clip(artifact, 4000)}`) — 같은 이유로 producer가 자기 이전
구현의 뒷부분을 못 보고 수정하게 될 수 있다.

## 수정

`yok3x/config.py`의 `DEFAULT_YOK3X`에 두 설정을 추가했다.

```python
"review_artifact_max_chars": 20000,   # reviewer가 보는 산출물 글자 제한
"revision_artifact_max_chars": 20000, # 재작업 시 producer가 보는 직전 산출물 글자 제한
```

`orchestrator.py`의 두 호출부를 하드코딩 상수 대신 이 설정을 읽도록 바꿨다(기존 fallback
값은 각각 6000/4000으로 유지해 설정이 없을 때도 안전).

## 검증

- `python -m pytest -q` → 465 passed, 1 skipped(회귀 없음).
- T-2 작업 #11을 클린 디렉터리에서 재실행해 수정 전/후 점수 비교 예정(별도 후속 실행).

## 남은 사항

- 20000자는 "흔한 단일 파일 구현체"를 기준으로 고른 값이며 상한이 아니다 — 더 큰 산출물
  (여러 파일·긴 pipeline)에는 여전히 부족할 수 있다. 근본적으로는 파일 단위 청크나 diff
  기반 표시가 더 견고하겠지만 이번 수정 범위 밖이다.
- `[직전 단계 출력]`(fanout/pipeline, line ~1527)과 `[fanin]`(line ~1559)도 각각 4000/6000
  고정값을 쓴다 — 같은 클래스의 문제일 수 있으나 이번엔 실측으로 확인된 두 지점만 고쳤다.

## 교훈

리뷰어가 "잘렸다"고 정직하게 보고해도, 그 잘림 자체가 점수를 왜곡한다는 사실은 로그를
직접 읽기 전까진 드러나지 않는다. `verify=ok`인데 `score`만 낮으면 "리뷰어가 코드 품질에
불만이 있나 보다"로 넘기지 말고, 리뷰어가 **실제로 무엇을 봤는지**부터 확인해야 한다.
