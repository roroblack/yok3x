# 버전 아카이브 (backup/versions/)

이전 버전들을 삭제하지 않고 이 폴더에 zip으로 보존한다. 각 zip은 내부 `__version__`으로 검증했다.

| 파일 | 버전 | 파일수 | 내용 |
|---|---|---|---|
| `harness-multiagent-v2.2.zip` | 2.2.0 | 11 | git 초기커밋 스냅샷. 라이브 한도(codex app-server)·plan/calibrate까지 반영된 상태이나 `__version__`은 아직 2.2.0, **GUI(guiserver) 이전**. |
| `harness-multiagent-v2.3.0.zip` | 2.3.0 | 15 | 리네임 전 완성판. GUI 프로토타입·guiserver·RULE·HISTORY 포함. `backup/harness-multiagent-v2.3.0-backup-*/` 폴더를 zip으로 만든 것. |
| `yok3x-v3.0.1.zip` | 3.0.1 | 15 | 현재 정본(yok3x 리네임 + 코딩 게이트 + 폴백/하드코딩 감사). `release/`와 동일. |

## 복원 출처(정직 표기)

- v2.2 → git 커밋 `c6a91af`에서 복원(`git show c6a91af:harness-multiagent-v2.2.zip`).
- v2.3.0 → 리네임 착수 전 만든 폴더 백업에서 zip.
- v3.0.1 → 현재 릴리스 복사본.

## 복원 불가 항목(정직 표기)

- **원본 pristine v2.2 (유저 최초 제공, ~69KB, __pycache__ 포함)**: 이후 재패키징으로 덮어써져 정확본 없음. 위 v2.2.0 zip이 그 소스의 가장 가까운 보존본(내용은 동일 계열, 재패키징으로 pycache 제거·일부 파일 신버전).
- **v3.0.0 (yok3x 리네임 직후, 감사 이전)**: 별도 커밋 없이 v3.0.1 편집으로 소스가 덮어써지고 zip도 삭제됨 → 정확 복원 불가. **v3.0.1이 v3.0.0 + 감사 수정**이며, 그 차이는 `yok3x/HISTORY.md`의 v3.0.1 항목에 전부 기록돼 있다.

## 앞으로

RULE.md §8에 따라, 새 릴리스를 만들 때 이전 릴리스 zip을 **삭제하지 말고 이 폴더로 옮긴다**.
