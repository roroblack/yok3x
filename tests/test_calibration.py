"""심판 캘리브레이션(F0) 순수 통계 테스트 — 상관·혼동행렬·엣지."""
from yok3x import calibration as C


def _r(score, ok):
    return C.make_record(score=score, verify_ok=ok, verify_scope="candidate", run_id="r")


def test_point_biserial_strong_and_zero_correlation():
    strong = [_r(9, True), _r(8.5, True), _r(9.5, True), _r(3, False), _r(2, False), _r(4, False)]
    assert C.point_biserial(strong) > 0.9                    # SCORE↑ = 통과 → 게이트 신호
    noise = [_r(9, True), _r(9, False), _r(3, True), _r(3, False),
             _r(8, True), _r(2, False), _r(8, False), _r(2, True)]
    assert abs(C.point_biserial(noise)) < 0.1                # 무상관 → 게이트 연극


def test_point_biserial_edge_cases_return_none():
    assert C.point_biserial([_r(9, True)]) is None           # 표본 부족
    assert C.point_biserial([_r(9, True), _r(9, False)]) is None   # SCORE 분산 0
    assert C.point_biserial([_r(9, True), _r(8, True)]) is None    # 라벨 한쪽뿐


def test_confusion_at_threshold_flags_false_pass():
    rows = [_r(9, True), _r(8.5, True), _r(8.2, False), _r(3, False)]  # 8.2는 게이트 통과·실제 실패
    c = C.confusion_at(rows, 8.0)
    assert c["tp"] == 2 and c["fp"] == 1 and c["tn"] == 1 and c["fn"] == 0
    assert abs(c["precision"] - 2/3) < 1e-9                  # 통과시킨 3건 중 2건만 실제 통과


def test_unlabeled_records_excluded():
    recs = [_r(9, True), _r(3, False), C.make_record(score=7, verify_ok=None, run_id="x")]
    s = C.summarize(recs)
    assert s["n_total"] == 3 and s["n_labeled"] == 2


def test_summarize_verdict():
    strong = ([_r(9, True), _r(3, False)] * 6)
    assert "신호 있음" in C.summarize(strong)["verdict"]
    assert "부족" in C.summarize([_r(9, True), _r(3, False)])["verdict"]


def _bucket_record(bucket, pattern, rounds, verify_ok=True):
    return C.make_record(bucket=bucket, pattern=pattern, rounds=rounds, verify_ok=verify_ok,
                         score=8.0, run_id="r")


def test_rounds_by_bucket_below_threshold_is_unavailable():
    recs = [_bucket_record("medium", "producer-reviewer", 2) for _ in range(2)]
    groups = C.rounds_by_bucket(recs, min_samples=3)["groups"]
    assert len(groups) == 1
    assert groups[0]["available"] is False
    assert groups[0]["sample_count"] == 2


def test_rounds_by_bucket_reports_median_mean_and_success_rate_once_threshold_met():
    recs = ([_bucket_record("medium", "producer-reviewer", 1, True)]
            + [_bucket_record("medium", "producer-reviewer", 2, True)]
            + [_bucket_record("medium", "producer-reviewer", 3, False)])
    groups = C.rounds_by_bucket(recs, min_samples=3)["groups"]
    assert len(groups) == 1
    g = groups[0]
    assert g["available"] is True
    assert g["median_rounds"] == 2
    assert abs(g["mean_rounds"] - 2.0) < 1e-9
    assert abs(g["success_rate"] - 2 / 3) < 1e-9


def test_rounds_by_bucket_separates_by_bucket_and_pattern():
    recs = ([_bucket_record("medium", "producer-reviewer", 2) for _ in range(3)]
            + [_bucket_record("large", "producer-reviewer", 4) for _ in range(3)]
            + [_bucket_record("medium", "solo", 1) for _ in range(3)])
    groups = C.rounds_by_bucket(recs, min_samples=3)["groups"]
    keys = {(g["bucket"], g["pattern"]) for g in groups}
    assert keys == {("medium", "producer-reviewer"), ("large", "producer-reviewer"), ("medium", "solo")}
    assert all(g["available"] for g in groups)


def test_rounds_by_bucket_ignores_records_missing_bucket_or_pattern():
    recs = [C.make_record(score=8.0, verify_ok=True, run_id="r")] * 5  # no bucket/pattern
    assert C.rounds_by_bucket(recs, min_samples=3)["groups"] == []


def test_rounds_hint_for_returns_matching_group_or_unavailable_placeholder():
    recs = [_bucket_record("medium", "producer-reviewer", 2) for _ in range(4)]
    hit = C.rounds_hint_for(recs, bucket="medium", pattern="producer-reviewer", min_samples=3)
    assert hit["available"] is True
    miss = C.rounds_hint_for(recs, bucket="tiny", pattern="producer-reviewer", min_samples=3)
    assert miss["available"] is False
    assert miss["sample_count"] == 0
