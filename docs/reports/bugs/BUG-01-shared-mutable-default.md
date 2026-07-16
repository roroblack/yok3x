# BUG-01 · 공유 가변 기본값 오염 (얕은 복사)

- **시점**: 2026-07-10 · **심각도**: 낮음(잠재적, 장기 실행 시 발현) · **커밋**: `39dff80` · **상태**: 수정됨 ✅

## 증상
`Config.load()`로 얻은 설정을 변형하면 **모듈 전역 `DEFAULT_YOK3X`가 오염**된다.
`scaffold(use_mock=True)`가 `w["backend"]="mock"`을 하면 그 프로세스 내내 전역 기본값이
mock으로 바뀌어, 이후 `Config.load`가 오염된 기본값을 반환한다. 재현:
```
DEFAULT claude-main backend  before: claude  after scaffold(mock): mock
=> module-level default polluted!
```

## 근본 원인
`config.py`의 `Config.load`가 `yok3x = dict(DEFAULT_YOK3X)`로 **얕은 복사**만 했다. 최상위
dict만 새로 만들고 중첩 dict(`workers` 등)는 전역과 같은 객체를 가리켰다. `_deep_merge`도
override에 없는 키의 하위 dict는 전역을 그대로 두어, 부분 `yok3x.json` 로드 시에도 오염 가능.

## 진단
얕은 복사임을 코드에서 확인 후, 별도 스크립트로 scaffold(mock) 전후 `DEFAULT_YOK3X`를 비교해
전역 오염을 재현.

## 수정
`Config.load` 진입부를 `copy.deepcopy(DEFAULT_YOK3X)`/`deepcopy(DEFAULT_BACKENDS)`로 변경.
`_deep_merge`가 아니라 진입부에서 통째로 깊은 복사해 부분 설정 케이스까지 차단.

## 검증
재현 스크립트 두 케이스(scaffold-mock, 부분설정 로드 후 변형) 모두 CLEAN. 단위 테스트 추가
(`test_scaffold_mock_does_not_pollute_global`, `test_partial_config_load_does_not_alias_global`).

## 교훈
"기본값 dict를 복사해서 쓴다"는 패턴은 **중첩 구조에선 deepcopy가 아니면 공유 참조**가 남는다.
전역 기본값은 진입부에서 깊은 복사로 격리한다.
