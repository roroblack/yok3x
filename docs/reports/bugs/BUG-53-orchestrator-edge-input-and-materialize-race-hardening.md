# BUG-53 — orchestrator 엣지 입력·재개 상태·materialize 경쟁 하드닝

## 증상

`yok3x/orchestrator.py`의 task 실행, 모델 라우팅, legacy reviewer 점수,
resume step, 파일 게시 경계에서 계획서에 없던 입력/상태 조합이 다음과 같이
크래시하거나 안전 정책을 우회했다.

- task JSON 최상위가 배열이거나 `max_rounds=None`/비수치 문자열, 빈 pipeline/fanout,
  없는 worker 참조이면 `AttributeError`·`TypeError`·`KeyError`가 전파됐다.
- `max_rounds=-1`은 worker를 한 번도 부르지 않고 빈 산출물을 `done/max_rounds`로 기록했다.
- legacy reviewer의 `SCORE: 999`가 strict 점수 게이트를 통과했다.
- `resolve_model()`은 availability probe 예외를 그대로 전파했고, NaN benchmark가 정상
  고득점 모델을 제치기도 했다.
- JSON 파싱은 되지만 usage/score/checklist 값 타입이 깨진 과거 step은 resume cache에
  들어간 뒤 실제 재생 시 변환 예외를 냈다.
- 같은 `materialize.root`를 쓰는 동시 런은 고정 tmp 파일을 서로 replace할 수 있었고,
  `overwrite=false`여도 기존 파일 스냅샷 이후의 TOCTOU 경쟁으로 다른 런의 파일을
  덮어쓸 수 있었다. 게시 root 자체가 symlink/junction이면 검증된 상대 파일명이
  root 밖 실제 위치에 기록될 수도 있었다.

## 근본 원인

- GUI 저장 검증을 우회하는 CLI/automation 직접 실행 경계에는 task spec 형태·범위 검증이
  없었고, 변환과 worker 조회 일부가 `RunAborted` 처리 범위 밖에서 실행됐다.
- SCORE 정규식은 숫자 형식만 확인하고 계약 범위(0~10)는 확인하지 않았다.
- benchmark 정렬은 모든 값을 서로 비교 가능하고 유한하다고 가정했으며 availability
  콜러블도 예외가 없다고 가정했다.
- resume loader는 필수 키 존재만 확인하고 값 타입/범위를 확인하지 않았다.
- materialize는 `exists/rglob` 사전 스냅샷 뒤 무조건 `os.replace`했고, tmp 이름도 대상별
  고정값이었다. 경로 포함 검사는 해석된 root를 기준으로 해서 root 자체의 symlink를
  신뢰했다.

## 진단

각 경계를 외부 호출 없는 최소 재현과 mock backend로 확인했다. 특히 음수 라운드는
`_finish(task, "")`가 호출되는 것을, 범위 밖 점수는 `evaluate_score_gate(...,
score=999)`가 `passed=true`를 반환하는 것을 확인했다. materialize는 두 실행을 barrier로
기존 파일 조회 이후에 정렬해 동일 파일 게시 경쟁을 결정적으로 재현했다.

## 수정

- 실행 전에 pattern별 필수 컬렉션, worker 참조, 숫자/점수 범위, changes/materialize의
  위험한 bool 옵션, acquire/escalate 형태를 검증하고 잘못된 spec은 backend 호출 전에
  `config_error`로 중단한다. 기존 호환을 위해 정수/점수 문자열과 명시적
  `max_rounds=0`은 유지한다.
- 직접 `run_pipeline()`/`run_fanout()`/`run_producer_reviewer()` 호출에도 핵심 불변식을
  적용한다.
- reviewer SCORE와 pass_score는 유한한 0~10만 인정하고, 범위 밖 관측은 미채점으로
  fail-closed 처리한다.
- 모델 라우팅은 유효한 유한 benchmark만 순위에 넣고, probe 예외가 난 backend는
  불가 후보로 처리해 다음 후보를 확인한다. 동점의 기존 입력 순서는 유지한다.
- resume step은 문자열/list/score/usage 값 타입과 범위를 확인해 손상 항목만 제외한다.
  과거 `status.json`은 애초 복원 근거로 소비하지 않고 strict manifest+step만 사용한다.
- materialize는 고유 임시파일을 사용하고, `overwrite=false`는 같은 파일시스템 hard link의
  원자적 create-if-absent로 게시한다. 동시 생성은 덮어쓰지 않고 거부 기록으로 남기며,
  root 구성요소의 symlink/junction도 게시 전에 거부한다.

## 검증

- `python -m py_compile yok3x/orchestrator.py` 통과.
- `tests/test_orchestrator_hardening.py`에 task 경계, legacy SCORE, 모델 라우팅,
  resume 손상 값, 동시 materialize, symlink root, 직접 pattern 호출 회귀를 추가했다.
- 관련 테스트 531개 및 별도 고위험 묶음 141개/71개 통과.
- 전체 `pytest -q` 첫 실행은 변경 전 baseline에서도 같은 `test_parallel.py` 시간 임계
  flaky 1건만 실패(`maximum == 3` 동시성 자체는 충족), 즉시 관련 단독 실행 통과.
- 전체 독립 재실행 2회가 각각 `638 passed, 1 skipped`, `639 passed, 1 skipped`
  (마지막 join-worker 계약 회귀 1개 추가 후)로 통과.

## 잔여 위험

- 파일 하나의 게시는 원자적이지만 여러 파일 묶음은 트랜잭션이 아니다. 두 번째 이후 파일
  게시가 실패하면 앞서 게시된 파일은 남는다. 기존 V-2 지적과 같은 큰 설계 변경(전체 staging,
  commit/rollback)이 필요해 이번 보수적 감사에서는 변경하지 않았다.
- malformed guard/reservation 설정은 일부 경로에서 실행을 중단하지만, NaN hard limit 등의
  근본 검증 책임은 `config.py`/`reserve.py`에 걸친다. 이번 단일 모듈 범위에서는 확장하지 않았다.

## 교훈

외부 JSON은 GUI가 만들었다고 가정하지 말고 실제 실행 경계에서 다시 검증해야 한다.
`overwrite=false`는 사전 `exists()` 확인만으로 보장되지 않으며, 게시 연산 자체가
create-if-absent여야 한다. 점수·쿼터처럼 안전 결정을 내리는 수치는 타입뿐 아니라
유한성·계약 범위까지 검증해야 한다.
