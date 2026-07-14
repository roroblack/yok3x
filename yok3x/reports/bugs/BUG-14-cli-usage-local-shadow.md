# BUG-14 · cli.py `usage` 지역변수 섀도잉 → pace/limits 명령 UnboundLocalError

- **시점**: 2026-07-14 · **심각도**: 중간 · **커밋**: `ffb85d8` · **상태**: 수정됨 ✅

## 증상
`yok3x pace approve codex` 실행 시 크래시:
```
UnboundLocalError: cannot access local variable 'usage' where it is not associated with a value
```
`pace status`도 헤더만 찍고 usage 참조에서 실패. `limits` 명령도 잠재적으로 동일 위험.

## 근본 원인
`cli.py`의 `main()`은 상단에서 `from . import ... usage`로 **모듈 수준 usage**를 임포트한다.
그런데 `profile` 분기 안에 **중복** `from . import usage`가 있었다. Python은 함수 내에서 어떤 이름에
**대입(=import 포함)** 이 한 번이라도 있으면 그 이름을 **함수 전체에서 지역변수로** 취급한다.
→ `main()` 전체에서 `usage`가 지역변수가 되고, `pace`/`limits` 분기(중복 import 실행 전)에서
`usage.X`를 참조하면 **아직 대입되지 않은 지역변수** 접근 → UnboundLocalError.

## 진단
`pace` 분기의 `usage.pace_approve` 라인에서 트레이스백. 함수 상단엔 이미 usage import가 있는데도
UnboundLocal이 나는 것에서 '함수 내 재대입에 의한 섀도잉'을 의심 → `profile` 분기의 중복 import 발견.

## 수정
`profile` 분기의 중복 `from . import usage` **제거**(모듈 수준 import로 충분).

## 검증
`yok3x pace approve codex`·`yok3x profile` 모두 정상. 이 수정으로 `pace`/`limits`/`profile`가
같은 섀도잉에서 벗어남(잠재 버그 동시 해소).

## 교훈
**함수 안에서 이미 전역 import된 이름을 지역 재import하지 말 것.** 파이썬의 '함수 스코프 이름은
컴파일 타임에 결정' 규칙 때문에, 조건 분기 안의 재대입이 함수 전체를 오염시킨다.
(관련: BUG-11 GUI JS 식별자 충돌 — 스코프/섀도잉 계열)
