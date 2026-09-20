from lsbench.scoring import TaskResult, aggregate, check_claim, instability
from lsbench.tasks import Claim


def C(kind, expected, **kw):
    return Claim(id="c", kind=kind, expected=expected, **kw)


def test_exact_contains_any_absent():
    assert check_claim(C("exact", "Homo sapiens"), "Organism: Homo sapiens")
    assert check_claim(C("contains", ["HER2+", "ER+"]), "subtype HER2+/ER+")
    assert not check_claim(C("contains", ["HER2+", "PR+"]), "subtype HER2+/ER+")
    assert check_claim(C("any", ["not found", "no record"]), "GSE9 was not found")
    assert check_claim(C("absent", ["Mus musculus"]), "Organism: Homo sapiens")
    assert not check_claim(C("absent", ["Homo sapiens"]), "Organism: Homo sapiens")


def test_numeric_tolerance():
    assert check_claim(C("numeric", 24), "There are 24 samples")
    assert check_claim(C("numeric", 24, tolerance=1), "There are 25 samples")
    assert not check_claim(C("numeric", 24), "There are 40 samples")
    assert check_claim(C("numeric", 1320), "1,320 cell lines")


def test_fuzzy_claims_are_not_checked_here():
    import pytest

    with pytest.raises(ValueError, match="fuzzy"):
        check_claim(C("fuzzy", "well designed"), "anything")


def test_aggregate_is_per_dimension_and_skips_unexercised():
    rs = [
        TaskResult("t1", "routing", 1, 1, latency_s=1.0, total_tokens=100),
        TaskResult("t2", "routing", 0, 1, latency_s=3.0, total_tokens=300),
        TaskResult("t3", "budget", 2, 2, latency_s=2.0, total_tokens=200),
        TaskResult("t4", "recovery", 0, 0, exercised=False),
    ]
    agg = aggregate(rs)
    assert agg["routing"]["mean"] == 0.5 and agg["routing"]["n"] == 2
    assert agg["budget"]["mean"] == 1.0
    assert agg["recovery"]["n"] == 0 and agg["recovery"]["unexercised"] == 1
    assert "overall" not in agg  # never collapse to one number


def test_instability_flags_intermittent_tasks():
    run_a = [TaskResult("t1", "routing", 1, 1), TaskResult("t2", "routing", 1, 1)]
    run_b = [TaskResult("t1", "routing", 0, 1), TaskResult("t2", "routing", 1, 1)]
    flagged = instability([run_a, run_b])
    assert any("t1" in f for f in flagged)
    assert not any("t2" in f for f in flagged)


def test_instability_ignores_unexercised_runs():
    run_a = [TaskResult("t1", "recovery", 1, 1)]
    run_b = [TaskResult("t1", "recovery", 0, 0, exercised=False)]
    assert instability([run_a, run_b]) == []


def test_strict_mean_zeroes_unstable_tasks():
    rs = [TaskResult("t1", "routing", 1, 1), TaskResult("t1", "routing", 0, 1),
          TaskResult("t2", "routing", 1, 1), TaskResult("t2", "routing", 1, 1)]
    agg = aggregate(rs, unstable_ids={"t1"})
    assert agg["routing"]["mean"] == 0.75
    assert agg["routing"]["mean_strict"] == 0.5
    assert agg["routing"]["unstable_tasks"] == ["t1"]


def test_support_check_accepts_unprefixed_geo_ids():
    from lsbench.scoring import _support_variants

    assert _support_variants("GPL18573") == ["gpl18573", "18573"]
    assert _support_variants("Homo sapiens") == ["homo sapiens"]
    assert _support_variants("ENSG00000012048") == ["ensg00000012048", "00000012048"]
