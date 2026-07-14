"""
Tests for abkit_core.variance — CUPED readiness and adjustment helpers.

Coverage:
  - assess_cuped_readiness: recommended tier (clean fixture, coverage=1.0, r≈0.77)
  - assess_cuped_readiness: optional tier (synthetic data, coverage=0.60, r≈0.15)
  - assess_cuped_readiness: not_recommended tier (weak CUPED fixture, coverage=0.40, r≈-0.11)
  - assess_cuped_readiness: insufficient matched units (< 3 matched pairs)
  - assess_cuped_readiness: no post-period data at all
  - assess_cuped_readiness: numeric values always present in explanation
  - apply_cuped_adjustment: correct theta and adjusted values (clean fixture)
  - apply_cuped_adjustment: raises ValueError on insufficient matched units
  - apply_cuped_adjustment: adjusted mean difference is consistent with raw direction
  - CUPED threshold constants are exported with correct policy values
  - CupedReadiness model_dump() emits string enum values
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest
import yaml

from abkit_core.schemas import CupedRecommendation, ExperimentConfig
from abkit_core.variance import (
    CUPED_COVERAGE_OPTIONAL,
    CUPED_COVERAGE_RECOMMENDED,
    CUPED_R_OPTIONAL,
    CUPED_R_RECOMMENDED,
    apply_cuped_adjustment,
    assess_cuped_readiness,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(scenario: str) -> ExperimentConfig:
    path = FIXTURES / scenario / "config.yaml"
    with path.open() as f:
        return ExperimentConfig(**yaml.safe_load(f))


def _load_metrics(scenario: str) -> pd.DataFrame:
    return pd.read_csv(FIXTURES / scenario / "metrics.csv")


def _load_assignments(scenario: str) -> pd.DataFrame:
    return pd.read_csv(FIXTURES / scenario / "assignments.csv")


def _synthetic_metrics(
    n_post: int,
    n_pre: int,
    r_target: float,
    metric_name: str = "test_metric",
    seed: int = 42,
) -> pd.DataFrame:
    """
    Build a synthetic metric DataFrame with a controllable pre-post correlation.

    Strategy: post = pre + noise, where the noise variance is tuned so that the
    resulting r is approximately r_target.  The exact r will vary slightly due to
    randomness, but will be in the right tier.

    pre-period is provided for the FIRST n_pre of the n_post units.
    """
    import random
    rng = random.Random(seed)

    units = [f"u{i:04d}" for i in range(n_post)]
    post_vals = [rng.gauss(0.2, 0.05) for _ in range(n_post)]

    # Derive pre values only for the first n_pre units
    # pre = post + noise; noise variance controls correlation
    # r ≈ std(post) / sqrt(std(post)^2 + noise_std^2)
    # => noise_std = std(post) * sqrt((1-r^2)/r^2) ... for r > 0
    import statistics
    std_post = statistics.stdev(post_vals)
    if r_target > 0:
        noise_std = std_post * math.sqrt((1 - r_target**2) / r_target**2)
    else:
        noise_std = std_post * 3  # large noise => near-zero correlation

    rows = []
    for i, (uid, pv) in enumerate(zip(units, post_vals)):
        rows.append({
            "experiment_id": "exp_synthetic",
            "unit_id": uid,
            "metric_name": metric_name,
            "metric_value": pv,
            "period": "post",
        })
        if i < n_pre:
            pre_val = pv + rng.gauss(0, noise_std)
            rows.append({
                "experiment_id": "exp_synthetic",
                "unit_id": uid,
                "metric_name": metric_name,
                "metric_value": pre_val,
                "period": "pre",
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Policy threshold constants
# ---------------------------------------------------------------------------

class TestCupedThresholdConstants:
    def test_coverage_recommended_value(self):
        assert CUPED_COVERAGE_RECOMMENDED == 0.80, (
            "CUPED_COVERAGE_RECOMMENDED must be 0.80 per 03-schemas.md"
        )

    def test_r_recommended_value(self):
        assert CUPED_R_RECOMMENDED == 0.30, (
            "CUPED_R_RECOMMENDED must be 0.30 per 03-schemas.md"
        )

    def test_coverage_optional_value(self):
        assert CUPED_COVERAGE_OPTIONAL == 0.50, (
            "CUPED_COVERAGE_OPTIONAL must be 0.50 per 03-schemas.md"
        )

    def test_r_optional_value(self):
        assert CUPED_R_OPTIONAL == 0.10, (
            "CUPED_R_OPTIONAL must be 0.10 per 03-schemas.md"
        )

    def test_optional_thresholds_below_recommended(self):
        """Optional tier thresholds must be strictly below recommended thresholds."""
        assert CUPED_COVERAGE_OPTIONAL < CUPED_COVERAGE_RECOMMENDED
        assert CUPED_R_OPTIONAL < CUPED_R_RECOMMENDED


# ---------------------------------------------------------------------------
# assess_cuped_readiness — tier classification
# ---------------------------------------------------------------------------

class TestAssessCupedReadiness:
    def test_recommended_tier_clean_fixture(self):
        """
        Clean fixture: coverage=1.0, r≈0.77 — both thresholds for 'recommended'
        are satisfied.
        """
        metrics = _load_metrics("clean")
        result = assess_cuped_readiness(metrics, "conversion_rate")
        assert result.recommendation == "recommended"
        assert result.matched_coverage >= CUPED_COVERAGE_RECOMMENDED
        assert result.pre_post_correlation >= CUPED_R_RECOMMENDED

    def test_recommended_tier_has_positive_variance_reduction(self):
        metrics = _load_metrics("clean")
        result = assess_cuped_readiness(metrics, "conversion_rate")
        # estimated_variance_reduction = r^2, always >= 0
        assert result.estimated_variance_reduction > 0
        # Should be approximately r^2
        r = result.pre_post_correlation
        expected_vr = r ** 2
        assert abs(result.estimated_variance_reduction - expected_vr) < 1e-5

    def test_not_recommended_tier_weak_cuped_fixture(self):
        """
        Weak CUPED fixture: coverage=0.40 (< 0.50), r≈-0.11 — not_recommended.
        """
        metrics = _load_metrics("weak_cuped")
        result = assess_cuped_readiness(metrics, "day7_retention")
        assert result.recommendation == "not_recommended"
        assert result.matched_coverage < CUPED_COVERAGE_OPTIONAL

    def test_not_recommended_has_caveats(self):
        metrics = _load_metrics("weak_cuped")
        result = assess_cuped_readiness(metrics, "day7_retention")
        assert len(result.caveats) >= 1
        # Coverage caveat should mention the threshold
        combined = " ".join(result.caveats)
        assert "coverage" in combined.lower() or "match" in combined.lower()

    def test_optional_tier_synthetic(self):
        """
        Synthetic data: coverage=0.60, r≈0.15 — satisfies optional thresholds
        but not recommended thresholds.
        """
        # n_post=100, n_pre=60 => coverage=0.60; r_target=0.15 => in optional range
        metrics = _synthetic_metrics(n_post=100, n_pre=60, r_target=0.15, seed=7)
        result = assess_cuped_readiness(metrics, "test_metric")
        # Coverage must be in [0.50, 0.80)
        assert result.matched_coverage >= CUPED_COVERAGE_OPTIONAL
        # r must be >= CUPED_R_OPTIONAL (allow some slack for RNG variance)
        assert result.pre_post_correlation >= CUPED_R_OPTIONAL - 0.05, (
            f"r={result.pre_post_correlation:.4f} — too low for 'optional' tier"
        )
        assert result.recommendation == "optional"

    def test_insufficient_matched_units_returns_not_recommended(self):
        """
        Only 2 matched pre/post pairs — not enough for a stable correlation.
        """
        metrics = pd.DataFrame([
            {"experiment_id": "e", "unit_id": "u0", "metric_name": "m",
             "metric_value": 0.1, "period": "post"},
            {"experiment_id": "e", "unit_id": "u1", "metric_name": "m",
             "metric_value": 0.2, "period": "post"},
            {"experiment_id": "e", "unit_id": "u0", "metric_name": "m",
             "metric_value": 0.09, "period": "pre"},
            {"experiment_id": "e", "unit_id": "u1", "metric_name": "m",
             "metric_value": 0.18, "period": "pre"},
        ])
        result = assess_cuped_readiness(metrics, "m")
        assert result.recommendation == "not_recommended"
        assert "3" in result.explanation or "3" in " ".join(result.caveats)

    def test_no_post_period_data_returns_not_recommended(self):
        """
        No post-period rows at all.
        """
        metrics = pd.DataFrame([
            {"experiment_id": "e", "unit_id": "u0", "metric_name": "m",
             "metric_value": 0.1, "period": "pre"},
        ])
        result = assess_cuped_readiness(metrics, "m")
        assert result.recommendation == "not_recommended"
        assert result.matched_coverage == 0.0

    def test_explanation_always_contains_numeric_values(self):
        """The explanation must state actual coverage and correlation values."""
        metrics = _load_metrics("clean")
        result = assess_cuped_readiness(metrics, "conversion_rate")
        # The explanation must contain the numeric coverage and r
        assert str(round(result.matched_coverage, 3)) in result.explanation
        assert str(round(result.pre_post_correlation, 4)) in result.explanation

    def test_model_dump_recommendation_is_string(self):
        """model_dump() must emit 'recommended' string, not enum member."""
        metrics = _load_metrics("clean")
        result = assess_cuped_readiness(metrics, "conversion_rate")
        dumped = result.model_dump()
        assert dumped["recommendation"] == "recommended"
        assert isinstance(dumped["recommendation"], str)

    def test_variance_reduction_bounded_zero_to_one(self):
        """estimated_variance_reduction must always be in [0, 1]."""
        for scenario, metric in [
            ("clean", "conversion_rate"),
            ("weak_cuped", "day7_retention"),
        ]:
            metrics = _load_metrics(scenario)
            result = assess_cuped_readiness(metrics, metric)
            assert 0.0 <= result.estimated_variance_reduction <= 1.0, (
                f"{scenario}: variance_reduction={result.estimated_variance_reduction}"
            )

    def test_negative_correlation_still_returns_result(self):
        """
        Negative pre-post correlation should return not_recommended (not raise).
        variance_reduction = r^2 is still positive.
        """
        metrics = _load_metrics("weak_cuped")
        result = assess_cuped_readiness(metrics, "day7_retention")
        # weak_cuped has r ≈ -0.11
        assert result.pre_post_correlation < 0
        assert result.estimated_variance_reduction >= 0.0
        assert result.recommendation == "not_recommended"

    def test_all_three_tiers_reachable(self):
        """
        Acceptance criterion from 05-acceptance.md: all three tiers must be
        reachable with known inputs.
        """
        # recommended: clean fixture
        m_rec = _load_metrics("clean")
        rec = assess_cuped_readiness(m_rec, "conversion_rate")
        assert rec.recommendation == "recommended"

        # not_recommended: weak_cuped fixture
        m_not = _load_metrics("weak_cuped")
        not_rec = assess_cuped_readiness(m_not, "day7_retention")
        assert not_rec.recommendation == "not_recommended"

        # optional: synthetic
        m_opt = _synthetic_metrics(n_post=100, n_pre=60, r_target=0.15, seed=7)
        opt = assess_cuped_readiness(m_opt, "test_metric")
        assert opt.recommendation == "optional"


# ---------------------------------------------------------------------------
# apply_cuped_adjustment — adjustment correctness
# ---------------------------------------------------------------------------

class TestApplyCupedAdjustment:
    def test_clean_fixture_theta_positive(self):
        """
        Clean fixture: pre and post values of conversion_rate should be
        positively correlated (r≈0.77), so theta > 0.
        """
        a = _load_assignments("clean")
        m = _load_metrics("clean")
        ctrl_adj, treat_adj, theta = apply_cuped_adjustment(a, m, "conversion_rate")
        assert theta > 0, f"Expected positive theta, got {theta:.4f}"

    def test_clean_fixture_returns_correct_variant_sizes(self):
        """Adjusted Series lengths must equal per-variant matched unit counts."""
        a = _load_assignments("clean")
        m = _load_metrics("clean")
        ctrl_adj, treat_adj, _ = apply_cuped_adjustment(a, m, "conversion_rate")
        # Clean fixture: 100 control, 100 treatment, full coverage
        assert len(ctrl_adj) == 100
        assert len(treat_adj) == 100

    def test_adjusted_lift_direction_matches_raw(self):
        """
        CUPED adjustment changes the variance but should not reverse the
        direction of the mean difference.
        """
        a = _load_assignments("clean")
        m = _load_metrics("clean")
        ctrl_adj, treat_adj, _ = apply_cuped_adjustment(a, m, "conversion_rate")
        adj_diff = treat_adj.mean() - ctrl_adj.mean()

        # Raw means
        post = m[(m.period == "post") & (m.metric_name == "conversion_rate")]
        merged = post.merge(a[["unit_id", "variant"]], on="unit_id")
        raw_diff = (
            merged[merged.variant == "treatment"]["metric_value"].mean()
            - merged[merged.variant == "control"]["metric_value"].mean()
        )
        assert (adj_diff > 0) == (raw_diff > 0), (
            f"Direction mismatch: raw_diff={raw_diff:.6f}, adj_diff={adj_diff:.6f}"
        )

    def test_adjusted_variance_lower_than_raw(self):
        """
        For the clean fixture (r≈0.77), adjusted per-unit values should have
        lower pooled variance than raw post-period values (the whole point of CUPED).
        """
        a = _load_assignments("clean")
        m = _load_metrics("clean")
        ctrl_adj, treat_adj, _ = apply_cuped_adjustment(a, m, "conversion_rate")

        post = m[(m.period == "post") & (m.metric_name == "conversion_rate")]
        merged = post.merge(a[["unit_id", "variant"]], on="unit_id")
        raw_ctrl  = merged[merged.variant == "control"]["metric_value"]
        raw_treat = merged[merged.variant == "treatment"]["metric_value"]

        raw_var_ctrl  = float(raw_ctrl.var(ddof=1))
        adj_var_ctrl  = float(ctrl_adj.var(ddof=1))
        raw_var_treat = float(raw_treat.var(ddof=1))
        adj_var_treat = float(treat_adj.var(ddof=1))

        assert adj_var_ctrl  < raw_var_ctrl,  (
            f"ctrl variance not reduced: raw={raw_var_ctrl:.6f}, adj={adj_var_ctrl:.6f}"
        )
        assert adj_var_treat < raw_var_treat, (
            f"treat variance not reduced: raw={raw_var_treat:.6f}, adj={adj_var_treat:.6f}"
        )

    def test_raises_on_insufficient_matched_units(self):
        """
        Only 2 matched units — apply_cuped_adjustment must raise ValueError.
        """
        assignments = pd.DataFrame([
            {"unit_id": "u0", "variant": "control"},
            {"unit_id": "u1", "variant": "treatment"},
        ])
        metrics = pd.DataFrame([
            {"unit_id": "u0", "metric_name": "m", "metric_value": 0.1, "period": "post"},
            {"unit_id": "u1", "metric_name": "m", "metric_value": 0.2, "period": "post"},
            {"unit_id": "u0", "metric_name": "m", "metric_value": 0.09, "period": "pre"},
            {"unit_id": "u1", "metric_name": "m", "metric_value": 0.18, "period": "pre"},
        ])
        with pytest.raises(ValueError, match="3"):
            apply_cuped_adjustment(assignments, metrics, "m")

    def test_theta_consistent_with_manual_computation(self):
        """
        theta = Cov(pre, post) / Var(pre), pooled across both variants.
        Verify against manual pandas computation.
        """
        a = _load_assignments("clean")
        m = _load_metrics("clean")

        post_rows = m[(m.period == "post") & (m.metric_name == "conversion_rate")][
            ["unit_id", "metric_value"]
        ].rename(columns={"metric_value": "post"})
        pre_rows = m[(m.period == "pre") & (m.metric_name == "conversion_rate")][
            ["unit_id", "metric_value"]
        ].rename(columns={"metric_value": "pre"})
        matched = post_rows.merge(pre_rows, on="unit_id")

        expected_theta = matched[["pre", "post"]].cov(ddof=1).iloc[0, 1] / matched["pre"].var(ddof=1)

        _, _, theta = apply_cuped_adjustment(a, m, "conversion_rate")
        assert abs(theta - expected_theta) < 1e-10, (
            f"theta mismatch: got {theta:.8f}, expected {expected_theta:.8f}"
        )


# ---------------------------------------------------------------------------
# Hardening: zero pre-variance raises clearly (not SciPy nan)
# ---------------------------------------------------------------------------

class TestZeroPreVariance:
    def test_raises_when_pre_values_are_all_identical(self):
        """
        apply_cuped_adjustment must raise ValueError (not produce nan/inf) when
        all pre-period values are identical (zero pre-variance).
        """
        assignments = pd.DataFrame([
            {"unit_id": f"u{i}", "variant": "control" if i < 5 else "treatment"}
            for i in range(10)
        ])
        metrics = pd.DataFrame(
            # post values have spread
            [{"unit_id": f"u{i}", "metric_name": "m",
              "metric_value": float(i), "period": "post"} for i in range(10)] +
            # pre values are all constant → zero variance
            [{"unit_id": f"u{i}", "metric_name": "m",
              "metric_value": 1.0, "period": "pre"} for i in range(10)]
        )
        with pytest.raises(ValueError, match="zero"):
            apply_cuped_adjustment(assignments, metrics, "m",
                                   control_variant="control",
                                   treatment_variant="treatment")
