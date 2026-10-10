"""RFM quintile scoring, segment labelling and value scores.

    python3 tests/test_scoring.py        # or: pytest tests/test_scoring.py

``app.analytics.scoring`` is deliberately free of SQLAlchemy, pandas and
scikit-learn, so it imports and runs with nothing installed. These are real
executions of the shipped functions, not source inspection.

``tests/check_rfm_passthrough.py`` covers the import-passthrough half of the
module (aliases, refusal of partial data, union scoring). This file covers the
maths that half depends on and that nothing else exercised: the quantile cut
points, the bucket mapping, the recency inversion, the segment grid, and the
0..100 customer value score.

The segment grid gets an exhaustive sweep of all 125 score combinations. Rules
there are ordered and the first match wins, so a reordering can silently make a
label unreachable — a bug with no exception and no wrong answer, just a segment
that never appears on the dashboard again.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

# Resolved from this file's location so the checks run from any checkout.
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.analytics.scoring import (  # noqa: E402
    RFM_SEGMENT_ORDER,
    SCORE_BUCKETS,
    SOURCE_IMPORT,
    RfmRow,
    bucket_of,
    customer_scores,
    normalise,
    quantile_thresholds,
    rfm_segment_label,
    score_rfm,
    segment_distribution,
)


def make_row(customer_id: str, recency: float | None, frequency: int, monetary: float) -> RfmRow:
    """An ``RfmRow`` with the derived fields filled in consistently."""
    orders = max(frequency, 1)
    return RfmRow(
        customer_id=customer_id,
        frequency=frequency,
        monetary=monetary,
        total_orders=orders,
        average_order_value=round(monetary / orders, 2),
        recency=recency,
    )


# ---------------------------------------------------------------------------
# Quantile cut points
# ---------------------------------------------------------------------------


def test_thresholds_produce_one_fewer_cut_than_buckets() -> None:
    cuts = quantile_thresholds(list(range(1, 101)))
    assert len(cuts) == SCORE_BUCKETS - 1, f"5 buckets need 4 cut points, got {len(cuts)}"
    assert cuts == sorted(cuts), "cut points must ascend or bucket_of counts them wrongly"


def test_a_uniform_population_splits_evenly() -> None:
    """The point of quintiles: each bucket holds about a fifth of the customers."""
    values = list(range(1, 101))
    cuts = quantile_thresholds(values)

    sizes: dict[int, int] = {}
    for value in values:
        bucket = bucket_of(value, cuts)
        sizes[bucket] = sizes.get(bucket, 0) + 1

    assert set(sizes) == {1, 2, 3, 4, 5}, f"some bucket was never used: {sorted(sizes)}"
    for bucket, size in sizes.items():
        assert 18 <= size <= 22, f"bucket {bucket} holds {size} of 100, which is not a fifth"


def test_degenerate_inputs_do_not_raise() -> None:
    assert quantile_thresholds([]) == []
    assert quantile_thresholds([5.0], buckets=1) == []

    # No cut points means everything is bucket 1. A single-customer tenant gets a
    # meaningless but well-defined score rather than a crash on the dashboard.
    assert bucket_of(999.0, []) == 1


def test_an_entirely_flat_population_lands_in_one_bucket() -> None:
    """Every customer spending exactly the same amount.

    Asserted so the behaviour is a recorded decision rather than an accident: the
    cut points all equal that value, `value > threshold` is false everywhere, and
    all customers score 1. Nobody is ranked above anybody else, which is the
    honest answer when the data contains no variation.
    """
    cuts = quantile_thresholds([50.0] * 20)
    assert {bucket_of(50.0, cuts)} == {1}


def test_bucket_is_clamped_to_the_score_range() -> None:
    # More thresholds than buckets would otherwise let the score run past 5.
    assert bucket_of(1e9, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]) == SCORE_BUCKETS
    assert bucket_of(-1e9, [1.0, 2.0, 3.0, 4.0]) == 1


def test_bucket_never_decreases_as_the_value_grows() -> None:
    """Monotonicity, over random populations.

    If this fails, a customer who spends more can score lower than one who spends
    less — the kind of bug that survives review because no single example looks
    wrong.
    """
    rng = random.Random(20260815)

    for _ in range(200):
        values = [rng.uniform(0, 5000) for _ in range(rng.randint(2, 60))]
        cuts = quantile_thresholds(values)
        scored = sorted((value, bucket_of(value, cuts)) for value in values)

        for (low_value, low_score), (high_value, high_score) in zip(scored, scored[1:]):
            assert low_score <= high_score, (
                f"{high_value:.2f} scored {high_score} but {low_value:.2f} scored {low_score}"
            )


# ---------------------------------------------------------------------------
# score_rfm
# ---------------------------------------------------------------------------


def test_recency_is_inverted_so_recent_is_high() -> None:
    """Fewer days since the last order must mean a *better* R score.

    Recency is the one dimension where a smaller number is better, so it is the
    one place an off-by-inversion hides. Frequency and monetary are checked in the
    same pass to confirm they are *not* inverted.
    """
    rows = [make_row(f"c{n}", recency=float(n), frequency=n, monetary=float(n * 10)) for n in range(1, 21)]
    score_rfm(rows)

    by_id = {row.customer_id: row for row in rows}
    most_recent = by_id["c1"]
    least_recent = by_id["c20"]

    assert most_recent.r_score == 5, f"1 day since ordering should be R=5, got {most_recent.r_score}"
    assert least_recent.r_score == 1, f"20 days since ordering should be R=1, got {least_recent.r_score}"

    assert least_recent.f_score == 5, "the most frequent buyer should be F=5"
    assert least_recent.m_score == 5, "the biggest spender should be M=5"
    assert most_recent.f_score == 1 and most_recent.m_score == 1


def test_scores_stay_inside_one_to_five() -> None:
    rng = random.Random(7)
    rows = [
        make_row(f"c{n}", recency=rng.uniform(0, 400), frequency=rng.randint(1, 90), monetary=rng.uniform(1, 90000))
        for n in range(60)
    ]
    score_rfm(rows)

    for row in rows:
        for field in ("r_score", "f_score", "m_score"):
            value = getattr(row, field)
            assert value in {1, 2, 3, 4, 5}, f"{row.customer_id}.{field} = {value!r}"


def test_a_customer_missing_recency_is_left_unscored() -> None:
    """Nothing is invented. This is the module's governing rule.

    A customer with orders but no dated order cannot be placed on the recency
    axis. The alternative — substituting a default — produces a confident segment
    label derived from a number nobody measured.
    """
    known = [make_row(f"c{n}", recency=float(n), frequency=n, monetary=float(n * 5)) for n in range(1, 11)]
    unknown = make_row("no-date", recency=None, frequency=4, monetary=250.0)

    score_rfm([*known, unknown])

    assert unknown.r_score is None, "recency was invented"
    assert unknown.f_score is None, "a partial row was scored on the dimensions it did have"
    assert unknown.m_score is None
    assert unknown.rfm_segment is None, "an unscored customer must not carry a segment label"

    # And its absence must not distort the scored population.
    assert all(row.r_score is not None for row in known)


def test_a_population_with_nothing_scorable_is_returned_untouched() -> None:
    rows = [make_row("a", recency=None, frequency=0, monetary=0.0)]
    result = score_rfm(rows)

    assert result == rows
    assert result[0].rfm_segment is None


def test_scores_are_relative_to_the_tenants_own_customers() -> None:
    """A "5" means top quintile *for this business*, not an absolute threshold.

    The same customer is top-quintile among small spenders and bottom-quintile
    among large ones. Asserted because a shared or cached threshold table would
    break exactly this, and the symptom would be one tenant's scores drifting
    when an unrelated tenant imported data.
    """
    subject = 500.0

    small = [make_row(f"s{n}", 10.0, 5, float(n * 20)) for n in range(1, 21)]  # up to 400
    large = [make_row(f"l{n}", 10.0, 5, float(n * 500)) for n in range(1, 21)]  # up to 10000

    in_small = make_row("subject", 10.0, 5, subject)
    in_large = make_row("subject", 10.0, 5, subject)

    score_rfm([*small, in_small])
    score_rfm([*large, in_large])

    assert in_small.m_score == 5, f"500 is the top spender among small accounts, got {in_small.m_score}"
    assert in_large.m_score == 1, f"500 is the smallest spender among large accounts, got {in_large.m_score}"


def test_source_is_preserved_through_scoring() -> None:
    """Scoring must not overwrite provenance.

    ``source`` records whether the numbers came from real orders or were passed
    through from an upload, which is what lets the UI say where a figure came from.
    """
    imported = RfmRow(
        customer_id="from-file",
        frequency=3,
        monetary=90.0,
        total_orders=3,
        average_order_value=30.0,
        recency=12.0,
        source=SOURCE_IMPORT,
    )
    others = [make_row(f"c{n}", float(n), n, float(n * 10)) for n in range(1, 11)]

    score_rfm([imported, *others])

    assert imported.source == SOURCE_IMPORT
    assert imported.r_score is not None, "a passthrough row should still be scored"


# ---------------------------------------------------------------------------
# Segment labelling
# ---------------------------------------------------------------------------


def test_every_score_combination_yields_a_known_segment() -> None:
    """All 125 combinations, checked against the dashboard's legend.

    An unlisted label would render in a chart with no colour and no position in
    the legend order.
    """
    produced = set()

    for r in range(1, 6):
        for f in range(1, 6):
            for m in range(1, 6):
                label = rfm_segment_label(r, f, m)
                assert isinstance(label, str) and label, f"({r},{f},{m}) produced {label!r}"
                assert label in RFM_SEGMENT_ORDER, f"({r},{f},{m}) -> {label!r} is not in RFM_SEGMENT_ORDER"
                produced.add(label)

    # Every declared segment must be reachable. An unreachable one means an
    # earlier rule shadows a later one — no error, just a label that can never
    # appear again.
    unreachable = set(RFM_SEGMENT_ORDER) - produced
    assert not unreachable, f"these segments can never be assigned: {sorted(unreachable)}"


def test_the_headline_segments_mean_what_they_say() -> None:
    assert rfm_segment_label(5, 5, 5) == "Champions"
    assert rfm_segment_label(4, 4, 4) == "Champions"

    # Buys often, spends well, has not been seen lately. The distinction from
    # "Loyal Customers" is the whole reason this segment exists.
    assert rfm_segment_label(1, 5, 5) == "Cannot Lose Them"
    assert rfm_segment_label(2, 5, 5) == "Cannot Lose Them"
    assert rfm_segment_label(3, 5, 5) == "Loyal Customers"

    # Just arrived: recent, but only one order.
    assert rfm_segment_label(5, 1, 1) == "New Customers"
    assert rfm_segment_label(5, 1, 5) == "New Customers"

    assert rfm_segment_label(1, 1, 1) == "Hibernating"


def test_champions_requires_all_three_dimensions() -> None:
    for r, f, m in ((3, 5, 5), (5, 3, 5), (5, 5, 3)):
        assert rfm_segment_label(r, f, m) != "Champions", f"({r},{f},{m}) should not be a Champion"


def test_distribution_counts_only_labelled_rows_and_sums_to_one() -> None:
    rows = [make_row(f"c{n}", float(n), n, float(n * 10)) for n in range(1, 26)]
    rows.append(make_row("unscored", None, 2, 40.0))

    score_rfm(rows)
    distribution = segment_distribution(rows)

    counted = sum(entry["customers"] for entry in distribution)
    assert counted == 25, f"the unscored customer was counted: {counted} of 25"

    assert abs(sum(entry["share"] for entry in distribution) - 1.0) < 1e-6

    # Stable display order, so bars do not reshuffle between refreshes.
    positions = [RFM_SEGMENT_ORDER.index(entry["segment"]) for entry in distribution]
    assert positions == sorted(positions), f"segments came back out of order: {positions}"


def test_distribution_of_nothing_is_empty_not_an_error() -> None:
    assert segment_distribution([]) == []


# ---------------------------------------------------------------------------
# normalise / customer_scores
# ---------------------------------------------------------------------------


def test_normalise_spans_zero_to_one() -> None:
    scaled = normalise([10.0, 20.0, 30.0, 40.0])
    assert scaled[0] == 0.0 and scaled[-1] == 1.0
    assert scaled == sorted(scaled)


def test_a_flat_series_normalises_to_the_midpoint() -> None:
    # Not 0.0: with no spread, no customer is at the bottom of the range. Also
    # avoids a divide-by-zero on a single-customer tenant.
    assert normalise([7.0, 7.0, 7.0]) == [0.5, 0.5, 0.5]
    assert normalise([]) == []


def test_customer_value_score_is_bounded_and_ranks_correctly() -> None:
    best = make_row("best", recency=1.0, frequency=50, monetary=50000.0)
    worst = make_row("worst", recency=365.0, frequency=1, monetary=5.0)
    middle = make_row("middle", recency=60.0, frequency=10, monetary=2500.0)

    scores = customer_scores([best, worst, middle])

    assert set(scores) == {"best", "worst", "middle"}
    for customer_id, score in scores.items():
        assert 0.0 <= score <= 100.0, f"{customer_id} scored {score}, outside 0..100"

    assert scores["best"] > scores["middle"] > scores["worst"]

    # The weights are 0.50 monetary + 0.35 frequency + 0.15 inverted recency, and
    # they must total 1.0 or the extremes cannot reach 0 and 100.
    assert scores["best"] == 100.0
    assert scores["worst"] == 0.0


def test_customer_score_treats_missing_recency_as_zero_days() -> None:
    """``customer_scores`` reads ``row.recency or 0.0``.

    Recorded here deliberately, because it differs from ``score_rfm``, which
    refuses to score such a row at all. The value score is a soft ranking aid
    rather than a reported segment, so a present-day default is acceptable — but
    it is a real inconsistency and worth being explicit about rather than
    discovering later.
    """
    unknown = make_row("unknown", recency=None, frequency=5, monetary=100.0)
    scores = customer_scores([unknown, make_row("other", 30.0, 5, 100.0)])

    assert scores["unknown"] >= scores["other"], "a missing recency is treated as most-recent"


def test_customer_scores_of_nothing_is_empty() -> None:
    assert customer_scores([]) == {}


# ---------------------------------------------------------------------------


def main() -> int:
    tests = [(key, value) for key, value in sorted(globals().items()) if key.startswith("test_")]
    failed = 0

    for name, test in tests:
        try:
            test()
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - report, do not mask
            failed += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"  ok   {name}")

    print()
    if failed:
        print(f"{failed} of {len(tests)} SCORING CHECKS FAILED")
        return 1

    print(f"ALL {len(tests)} SCORING CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
