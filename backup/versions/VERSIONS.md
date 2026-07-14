# 버전 아카이브 (backup/versions/)

이전 버전들을 삭제하지 않고 이 폴더에 zip으로 보존한다. 각 zip은 내부 `__version__`으로 검증했다.

| 파일 | 버전 | 파일수 | 내용 |
|---|---|---|---|
| `harness-multiagent-v2.2.zip` | 2.2.0 | 11 | git 초기커밋 스냅샷. 라이브 한도(codex app-server)·plan/calibrate까지 반영된 상태이나 `__version__`은 아직 2.2.0, **GUI(guiserver) 이전**. |
| `harness-multiagent-v2.3.0.zip` | 2.3.0 | 15 | 리네임 전 완성판. GUI 프로토타입·guiserver·RULE·HISTORY 포함. `backup/harness-multiagent-v2.3.0-backup-*/` 폴더를 zip으로 만든 것. |
| `yok3x-v3.0.1.zip` | 3.0.1 | 15 | yok3x 리네임 + 코딩 게이트 + 폴백/하드코딩 감사. |
| `yok3x-v3.1.0.zip` | 3.1.0 | 16 | 오케스트레이션 콘솔(작업 채팅+실시간 사용량+배치 보드, `/api/run`·`/api/config`) + 도움말 탭. 폴더 스냅샷: `backup/yok3x-v3.1.0-*/`. |
| `yok3x-v3.3.0.zip` | 3.3.0 | 21 | 라이브 한도 실측(claude OAuth·codex)·적응형 열화 P1·상황별 모델 프로파일 S1~S3·전역 워크스페이스·GUI 임계선. 프로듀서 병목 근본 해결(멀티라인 프롬프트 stdin). |
| `yok3x-v3.4.0.zip` | 3.4.0 | 34 | v3.3.0 + **적응형 열화 P2(백엔드 폴오버, GUI on/off 토글)** · 워크스페이스 폴더선택 · claude 모델별 주간(Fable) · 프로파일 설명·워커 수동모델 · codex 창라벨 수정(BUG-12) · **버그 리포트 아카이브 12건**. 폴더 스냅샷: `backup/yok3x-v3.4.0-20260711/`. |
| `yok3x-v3.5.0.zip` | 3.5.0 | 41 | v3.4.0 + **CLI 모델 동적조회**(claude API·codex 캐시·gemini 번들 GEMINI_MODELS) · **P3 오프라인 폴백**(로컬 OpenAI 호환, 의존성0, GUI on/off) · **추론강도(effort)** claude/codex 통과+보드 UI · **knot consolidation**(Mem0식: 요점저장·중복통합·최신성감쇠) · claude stale-while-error·1003% 차단 · 버전 단일출처 · RULE §5.6(GUI 승인규율). |
| `yok3x-v3.6.0.zip` | 3.6.0 | 42 | **현재 정본.** v3.5.0 + **주간쿼터 하루 페이싱**(일일 소비 캡): 오늘소비='현재7d%−아침스냅샷'의 양의증분 누적 vs 캡(기본 **14%p**≈주간 1/7), 모드 경고/정지+승인. codex 리뷰 반영 하드닝: **sticky pause**(롤오프·probe실패에도 정지 유지)·실측7d에만 적용·자정 자동리셋·pace.json 원자적 쓰기·값검증. CLI `yok3x pace status\|approve`, GUI 설정탭 UI(도구별 소비 미터·재개승인·기본값 버튼). `release/`와 동일. |

## 복원 출처(정직 표기)

- v2.2 → git 커밋 `c6a91af`에서 복원(`git show c6a91af:harness-multiagent-v2.2.zip`).
- v2.3.0 → 리네임 착수 전 만든 폴더 백업에서 zip.
- v3.0.1 → 현재 릴리스 복사본.

## 복원 불가 항목(정직 표기)

- **원본 pristine v2.2 (유저 최초 제공, ~69KB, __pycache__ 포함)**: 이후 재패키징으로 덮어써져 정확본 없음. 위 v2.2.0 zip이 그 소스의 가장 가까운 보존본(내용은 동일 계열, 재패키징으로 pycache 제거·일부 파일 신버전).
- **v3.0.0 (yok3x 리네임 직후, 감사 이전)**: 별도 커밋 없이 v3.0.1 편집으로 소스가 덮어써지고 zip도 삭제됨 → 정확 복원 불가. **v3.0.1이 v3.0.0 + 감사 수정**이며, 그 차이는 `yok3x/HISTORY.md`의 v3.0.1 항목에 전부 기록돼 있다.

## 앞으로

RULE.md §8에 따라, 새 릴리스를 만들 때 이전 릴리스 zip을 **삭제하지 말고 이 폴더로 옮긴다**.
