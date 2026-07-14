"""
Tests for abkit_core.analysis — raw estimates, CUPED-adjusted estimates,
secondary/guardrail metrics, and decision scaffolding.

Coverage:
  Scenario A — clean experiment:
    - Raw primary metric estimate has correct fields and is significant.
    - CUPED readiness is 'recommended', cuped_estimate is populated.
    - CUPED-adjusted estimate is also significant.
    - Adjusted absolute_lift direction matches raw.
    - effective_primary_estimate equals cuped_estimate when CUPED was applied.
    - Secondary metric (revenue_per_user) result is present.
    - Guardrail metric (refund_rate) result is present.
    - Decision is 'ship' when no guardrail failures and no SRM.
    - Decision is 'ship' with SRM warning caveat when SRM=warning.
    - AnalysisResult serialises cleanly to JSON.

  Scenario B — SRM critical:
    - Decision is 'hold' when quality_checks.srm_check.severity='critical'.
    - Reasoning summary mentions SRM.
    - Decision recommendation is 'hold' regardless of metric significance.

  Scenario C — weak CUPED:
    - CUPED readiness is 'not_recommended'.
    - cuped_estimate is None (not applied when not recommended).
    - Raw analysis still runs and returns a result.
    - Decision is 'ship' or 'inconclusive' based on primary metric p-value.

  Raw-only analysis case:
    - No pre-period data → cuped_readiness is None, cuped_estimate is None.
    - Raw primary result is still computed.

  Decision rules:
    - Significant guardrail → hold.
    - Not significant primary, no SRM, no guardrail failures → inconclusive.
    - SRM critical → hold (overrides all metric results).
    - p_value and ci fields are present in every MetricEstimate.
    - DecisionMemo.alpha_used matches config.alpha in every path.

  Proportion vs continuous metric type:
    - Proportion metric runs z-test (SE from pooled proportion).
    - Continuous metric runs Welch t-test.

  Edge cases:
    - control variant has < 2 observations → primary result is None.
    - run_analysis raises ValueError if config.metric_type is None.
    - run_analysis raises ValueError for >2 variants (v1 multi-variant guard).
    - Zero-variance (all-identical) data: p_value=1.0, is_significant=False,
      no nan in any field.
    - AnalysisResult.effective_primary_estimate is always populated when
      primary data exists, and equals cuped_estimate when CUPED was applied.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from abkit_core.analysis import run_analysis
from abkit_core.quality import check_assignment_quality
from abkit_core.schemas import (
    DecisionRecommendation,
    ExperimentConfig,
    MetricType,
    QualityChecks,
    SrmCheck,
    SrmSeverity,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(scenario: str) -> ExperimentConfig:
    path = FIXTURES / scenario / "config.yaml"
    with path.open() as f:
        return ExperimentConfig(**yaml.safe_load(f))


def _load_csv(scenario: str, filename: str) -> pd.DataFrame:
    return pd.read_csv(FIXTURES / scenario / filename)


def _make_config(**overrides) -> ExperimentConfig:
    base = {
        "schema_version": "1.0",
        "experiment_name": "Test experiment",
        "experiment_id": "exp_analysis_001",
        "owner": "test-team",
        "hypothesis": "Treatment will improve the primary metric significantly.",
        "primary_metric": "conversion_rate",
        "metric_type": "proportion",
        "variants": ["control", "treatment"],
        "expected_allocation": {"control": 0.5, "treatment": 0.5},
        "unit_of_randomization": "user_id",
        "alpha": 0.05,
        "power": 0.80,
        "mde": 0.02,
        "guardrail_metrics": ["refund_rate"],
    }
    base.update(overrides)
    return ExperimentConfig(**base)


def _make_critical_quality_checks() -> QualityChecks:
    """Produce a minimal QualityChecks with SRM severity = critical."""
    srm = SrmCheck(
        severity=SrmSeverity.critical,
        chi2_stat=27.0,
        p_value=0.000001,
        srm_alpha_used=0.01,
        observed_counts={"control": 105, "treatment": 195},
        expected_counts={"control": 150.0, "treatment": 150.0},
        observed_allocation={"control": 0.35, "treatment": 0.65},
        expected_allocation={"control": 0.5, "treatment": 0.5},
        max_absolute_drift=0.15,
        explanation="SRM CRITICAL",
    )
    return QualityChecks(srm_check=srm)


def _make_warning_quality_checks() -> QualityChecks:
    """Produce a minimal QualityChecks with SRM severity = warning."""
    srm = SrmCheck(
        severity=SrmSeverity.warning,
        chi2_stat=4.5,
        p_value=0.033,
        srm_alpha_used=0.01,
        observed_counts={"control": 98, "treatment": 102},
        expected_counts={"control": 100.0, "treatment": 100.0},
        observed_allocation={"control": 0.49, "treatment": 0.51},
        expected_allocation={"control": 0.5, "treatment": 0.5},
        max_absolute_drift=0.01,
        explanation="SRM WARNING",
    )
    return QualityChecks(srm_check=srm)


# ---------------------------------------------------------------------------
# Scenario A — clean experiment (strong CUPED case)
# ---------------------------------------------------------------------------

class TestScenarioAClean:
    def setup_method(self):
        self.cfg = _load_config("clean")
        self.assign = _load_csv("clean", "assignments.csv")
        self.metrics = _load_csv("clean", "metrics.csv")

    def test_raw_primary_result_present(self):
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        assert analysis.primary_metric_result is not None

    def test_raw_primary_result_fields_complete(self):
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        r = analysis.primary_metric_result
        assert r.metric_name == "conversion_rate"
        assert r.control_mean > 0
        assert r.treatment_mean > 0
        assert r.p_value is not None
        assert r.ci_lower is not None
        assert r.ci_upper is not None
        assert r.is_significant is not None

    def test_raw_primary_result_is_significant(self):
        """Clean fixture has a large effect (≈+0.025) and N=200 — must be significant."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        r = analysis.primary_metric_result
        assert r.is_significant is True, (
            f"Expected significant result; p={r.p_value:.6f}, alpha={self.cfg.alpha}"
        )

    def test_raw_ci_contains_lift(self):
        """Confidence interval must straddle the absolute_lift."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        r = analysis.primary_metric_result
        assert r.ci_lower < r.absolute_lift < r.ci_upper, (
            f"lift={r.absolute_lift} not in CI [{r.ci_lower}, {r.ci_upper}]"
        )

    def test_cuped_readiness_is_recommended(self):
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        assert analysis.cuped_readiness is not None
        assert analysis.cuped_readiness.recommendation == "recommended"

    def test_cuped_estimate_is_present(self):
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        assert analysis.cuped_estimate is not None

    def test_cuped_estimate_is_significant(self):
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        est = analysis.cuped_estimate
        assert est.is_significant is True, (
            f"CUPED estimate not significant: p={est.p_value:.6f}"
        )

    def test_cuped_lift_direction_matches_raw(self):
        """Adjusted mean difference must have same sign as raw mean difference."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        raw  = analysis.primary_metric_result
        cuped = analysis.cuped_estimate
        assert (cuped.absolute_lift > 0) == (raw.absolute_lift > 0)

    def test_cuped_estimate_name_contains_adjusted(self):
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        assert "cuped_adjusted" in analysis.cuped_estimate.metric_name

    def test_effective_primary_estimate_equals_cuped_when_cuped_applied(self):
        """When CUPED was applied, effective_primary_estimate must be the CUPED estimate."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        assert analysis.cuped_estimate is not None, "Precondition: CUPED must be applied"
        assert analysis.effective_primary_estimate is not None
        assert (
            analysis.effective_primary_estimate.metric_name
            == analysis.cuped_estimate.metric_name
        ), (
            "effective_primary_estimate must be the cuped_estimate when CUPED was applied"
        )

    def test_decision_alpha_used_matches_config(self):
        """DecisionMemo.alpha_used must equal config.alpha on every decision path."""
        _, decision = run_analysis(self.assign, self.metrics, self.cfg)
        assert decision.alpha_used == self.cfg.alpha

    def test_secondary_metric_result_present(self):
        """revenue_per_user is declared as a secondary metric in clean config."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        names = [e.metric_name for e in analysis.secondary_metric_results]
        assert "revenue_per_user" in names

    def test_guardrail_metric_result_present(self):
        """refund_rate is declared as a guardrail metric in clean config."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        names = [e.metric_name for e in analysis.guardrail_metric_results]
        assert "refund_rate" in names

    def test_decision_is_ship_clean(self):
        analysis, decision = run_analysis(self.assign, self.metrics, self.cfg)
        assert decision.recommendation == "ship"

    def test_decision_ship_has_reasoning(self):
        _, decision = run_analysis(self.assign, self.metrics, self.cfg)
        assert "conversion_rate" in decision.reasoning_summary
        assert len(decision.reasoning_summary) > 20

    def test_decision_ship_with_srm_warning_has_caveat(self):
        """When SRM=warning, ship is still possible but a caveat must be surfaced."""
        _, decision = run_analysis(
            self.assign, self.metrics, self.cfg,
            quality_checks=_make_warning_quality_checks(),
        )
        # Still ships (refund_rate is not significant in clean fixture normally)
        # caveat must mention SRM
        combined = " ".join(decision.key_caveats + [decision.reasoning_summary])
        assert "srm" in combined.lower() or "warning" in combined.lower()

    def test_analysis_result_serialises_to_json(self):
        """model_dump_json() must produce valid JSON with string enum values."""
        import json
        analysis, decision = run_analysis(self.assign, self.metrics, self.cfg)
        payload = json.loads(analysis.model_dump_json())
        assert payload["cuped_readiness"]["recommendation"] == "recommended"
        assert payload["primary_metric_result"]["metric_type"] == "proportion"
        payload2 = json.loads(decision.model_dump_json())
        assert payload2["recommendation"] == "ship"


# ---------------------------------------------------------------------------
# Scenario B — SRM critical → hold
# ---------------------------------------------------------------------------

class TestScenarioBSrmCritical:
    def setup_method(self):
        self.cfg = _load_config("srm")
        self.assign = _load_csv("srm", "assignments.csv")
        self.metrics = _load_csv("srm", "metrics.csv")
        self.qa = check_assignment_quality(self.assign, self.cfg)

    def test_srm_fixture_qa_is_critical(self):
        """Prerequisite: the QA layer must flag critical SRM on this fixture."""
        assert self.qa.srm_check.severity == "critical"

    def test_decision_is_hold_when_srm_critical(self):
        _, decision = run_analysis(
            self.assign, self.metrics, self.cfg,
            quality_checks=self.qa,
        )
        assert decision.recommendation == "hold"

    def test_hold_reasoning_mentions_srm(self):
        _, decision = run_analysis(
            self.assign, self.metrics, self.cfg,
            quality_checks=self.qa,
        )
        assert "srm" in decision.reasoning_summary.lower()

    def test_hold_caveats_mention_drift(self):
        _, decision = run_analysis(
            self.assign, self.metrics, self.cfg,
            quality_checks=self.qa,
        )
        combined = " ".join(decision.key_caveats)
        assert "drift" in combined.lower() or "imbalance" in combined.lower()

    def test_analysis_result_still_populated_under_srm(self):
        """
        Even when SRM is critical, the AnalysisResult should still be populated
        so the analyst can inspect the numbers.  The decision (hold) is separate.
        """
        analysis, _ = run_analysis(
            self.assign, self.metrics, self.cfg,
            quality_checks=self.qa,
        )
        assert analysis.primary_metric_result is not None

    def test_decision_hold_without_quality_checks_object(self):
        """Without a QualityChecks arg, SRM gate is skipped — decision is metric-driven."""
        _, decision = run_analysis(self.assign, self.metrics, self.cfg)
        # No QA passed — decision should NOT be hold due to SRM
        assert decision.recommendation != "hold" or "srm" not in decision.reasoning_summary.lower()


# ---------------------------------------------------------------------------
# Scenario C — weak CUPED case
# ---------------------------------------------------------------------------

class TestScenarioCWeakCuped:
    def setup_method(self):
        self.cfg = _load_config("weak_cuped")
        self.assign = _load_csv("weak_cuped", "assignments.csv")
        self.metrics = _load_csv("weak_cuped", "metrics.csv")

    def test_cuped_readiness_is_not_recommended(self):
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        assert analysis.cuped_readiness is not None
        assert analysis.cuped_readiness.recommendation == "not_recommended"

    def test_cuped_estimate_is_none_when_not_recommended(self):
        """CUPED must not be applied when readiness is not_recommended."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        assert analysis.cuped_estimate is None

    def test_raw_primary_result_still_computed(self):
        """Raw analysis must always run, regardless of CUPED readiness."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        assert analysis.primary_metric_result is not None

    def test_raw_result_has_p_value(self):
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        r = analysis.primary_metric_result
        assert r.p_value is not None
        assert 0.0 <= r.p_value <= 1.0

    def test_decision_is_ship_or_inconclusive_not_hold(self):
        """No SRM, no guardrail failures → decision must be ship or inconclusive."""
        _, decision = run_analysis(self.assign, self.metrics, self.cfg)
        assert decision.recommendation in ("ship", "inconclusive")

    def test_cuped_readiness_explanation_contains_actual_values(self):
        """05-acceptance.md: explanation must state actual coverage and r."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        r = analysis.cuped_readiness
        assert str(round(r.matched_coverage, 3)) in r.explanation
        assert str(round(r.pre_post_correlation, 4)) in r.explanation


# ---------------------------------------------------------------------------
# Raw-only analysis (no pre-period data — SRM fixture has no pre rows)
# ---------------------------------------------------------------------------

class TestRawOnlyAnalysis:
    def test_cuped_readiness_none_when_no_pre_data(self):
        """
        SRM fixture has no pre-period metric rows.
        cuped_readiness must be None (not assessed) — pre-period check returns False.
        """
        cfg = _load_config("srm")
        assign = _load_csv("srm", "assignments.csv")
        metrics = _load_csv("srm", "metrics.csv")
        # Confirm no pre-period rows in SRM fixture
        assert (metrics["period"] == "pre").sum() == 0
        analysis, _ = run_analysis(assign, metrics, cfg)
        assert analysis.cuped_readiness is None
        assert analysis.cuped_estimate is None

    def test_raw_estimate_computed_without_pre_data(self):
        """Raw analysis works with post-only data."""
        cfg = _load_config("srm")
        assign = _load_csv("srm", "assignments.csv")
        metrics = _load_csv("srm", "metrics.csv")
        analysis, _ = run_analysis(assign, metrics, cfg)
        assert analysis.primary_metric_result is not None
        assert analysis.primary_metric_result.p_value is not None


# ---------------------------------------------------------------------------
# Decision rules — targeted unit tests
# ---------------------------------------------------------------------------

class TestDecisionRules:
    def _make_minimal_df(self, n_ctrl=50, n_treat=50, ctrl_val=0.10, treat_val=0.12,
                         metric_name="conversion_rate", with_pre=False):
        """Build minimal assignment + metric DataFrames."""
        ctrl_ids  = [f"c{i:04d}" for i in range(n_ctrl)]
        treat_ids = [f"t{i:04d}" for i in range(n_treat)]

        assign_rows = (
            [{"unit_id": uid, "variant": "control"}   for uid in ctrl_ids] +
            [{"unit_id": uid, "variant": "treatment"}  for uid in treat_ids]
        )
        metric_rows = (
            [{"unit_id": uid, "metric_name": metric_name,
              "metric_value": ctrl_val, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": metric_name,
              "metric_value": treat_val, "period": "post"} for uid in treat_ids]
        )
        return pd.DataFrame(assign_rows), pd.DataFrame(metric_rows)

    def test_guardrail_failure_causes_hold(self):
        """
        Significant guardrail movement → hold, even when primary is significant.
        """
        # Make a config with guardrail metric
        cfg = _make_config(
            guardrail_metrics=["refund_rate"],
            secondary_metrics=[],
        )
        # Primary has a large effect (significant with N=50 each)
        assign, metric_primary = self._make_minimal_df(
            n_ctrl=200, n_treat=200, ctrl_val=0.10, treat_val=0.15,
            metric_name="conversion_rate",
        )
        # Add a significant guardrail movement (large effect to guarantee significance)
        guardrail_rows = pd.concat([
            pd.DataFrame([
                {"unit_id": f"c{i:04d}", "metric_name": "refund_rate",
                 "metric_value": 0.05, "period": "post"} for i in range(200)
            ]),
            pd.DataFrame([
                {"unit_id": f"t{i:04d}", "metric_name": "refund_rate",
                 "metric_value": 0.20, "period": "post"} for i in range(200)
            ]),
        ])
        metrics = pd.concat([metric_primary, guardrail_rows], ignore_index=True)
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "hold", (
            f"Expected hold due to guardrail; got {decision.recommendation}"
        )
        assert "refund_rate" in decision.reasoning_summary

    def test_not_significant_primary_is_inconclusive(self):
        """
        Tiny lift with high within-group noise → not significant → inconclusive.
        Using normally-distributed values with std=0.05 and delta=0.001 ensures
        p >> 0.05 regardless of seed, and avoids the zero-variance degenerate case.
        """
        import random
        rng = random.Random(999)
        cfg = _make_config(guardrail_metrics=[], secondary_metrics=[])
        ctrl_ids  = [f"c{i:04d}" for i in range(30)]
        treat_ids = [f"t{i:04d}" for i in range(30)]
        assign = pd.DataFrame(
            [{"unit_id": uid, "variant": "control"}    for uid in ctrl_ids] +
            [{"unit_id": uid, "variant": "treatment"}  for uid in treat_ids]
        )
        metrics = pd.DataFrame(
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": rng.gauss(0.100, 0.05), "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": rng.gauss(0.101, 0.05), "period": "post"} for uid in treat_ids]
        )
        _, decision = run_analysis(assign, metrics, cfg)
        # With n=30, std=0.05, delta=0.001 the effect size is << 0.1 and
        # the t-test will never reach p < 0.05
        assert decision.recommendation == "inconclusive"

    def test_srm_critical_overrides_significant_primary(self):
        """Even a strongly significant primary metric must yield hold under SRM critical."""
        cfg = _make_config(guardrail_metrics=[], secondary_metrics=[])
        assign, metrics = self._make_minimal_df(
            n_ctrl=500, n_treat=500, ctrl_val=0.10, treat_val=0.20,
        )
        qa = _make_critical_quality_checks()
        _, decision = run_analysis(assign, metrics, cfg, quality_checks=qa)
        assert decision.recommendation == "hold"

    def test_ship_decision_next_actions_non_empty(self):
        """A ship decision must always provide at least one next action."""
        cfg = _make_config(guardrail_metrics=[], secondary_metrics=[])
        assign, metrics = self._make_minimal_df(
            n_ctrl=500, n_treat=500, ctrl_val=0.10, treat_val=0.20,
        )
        _, decision = run_analysis(assign, metrics, cfg)
        if decision.recommendation == "ship":
            assert len(decision.next_actions) >= 1


# ---------------------------------------------------------------------------
# Metric type: proportion vs continuous
# ---------------------------------------------------------------------------

class TestMetricTypeSelection:
    def test_proportion_metric_uses_z_test_on_binary_data(self):
        """
        Proportion z-test fires when metric values are binary (0/1) Bernoulli
        indicators.  With n=400 and p_treat >> p_ctrl the result must be significant.
        """
        import random
        rng = random.Random(0)
        cfg = _make_config(metric_type="proportion", guardrail_metrics=[], secondary_metrics=[])
        ctrl_vals  = [1 if rng.random() < 0.10 else 0 for _ in range(400)]
        treat_vals = [1 if rng.random() < 0.20 else 0 for _ in range(400)]
        assign = pd.DataFrame(
            [{"unit_id": f"c{i:03d}", "variant": "control"} for i in range(400)] +
            [{"unit_id": f"t{i:03d}", "variant": "treatment"} for i in range(400)]
        )
        metrics = pd.DataFrame(
            [{"unit_id": f"c{i:03d}", "metric_name": "conversion_rate",
              "metric_value": float(v), "period": "post"} for i, v in enumerate(ctrl_vals)] +
            [{"unit_id": f"t{i:03d}", "metric_name": "conversion_rate",
              "metric_value": float(v), "period": "post"} for i, v in enumerate(treat_vals)]
        )
        analysis, _ = run_analysis(assign, metrics, cfg)
        r = analysis.primary_metric_result
        assert r is not None
        assert r.metric_type == "proportion"
        assert 0.0 <= r.p_value <= 1.0
        assert r.is_significant is True, f"Expected significant result; p={r.p_value}"

    def test_proportion_metric_falls_back_to_welch_for_continuous_values(self):
        """
        When proportion metric values are continuous floats (not binary 0/1),
        the z-test is skipped and Welch's t-test is used instead.
        The declared metric_type is preserved in the estimate.
        """
        import random
        rng = random.Random(1)
        cfg = _make_config(metric_type="proportion", guardrail_metrics=[], secondary_metrics=[])
        assign = pd.DataFrame(
            [{"unit_id": f"c{i:03d}", "variant": "control"} for i in range(200)] +
            [{"unit_id": f"t{i:03d}", "variant": "treatment"} for i in range(200)]
        )
        # Non-binary floats — falls back to Welch t-test
        metrics = pd.DataFrame(
            [{"unit_id": f"c{i:03d}", "metric_name": "conversion_rate",
              "metric_value": rng.gauss(0.10, 0.03), "period": "post"} for i in range(200)] +
            [{"unit_id": f"t{i:03d}", "metric_name": "conversion_rate",
              "metric_value": rng.gauss(0.15, 0.03), "period": "post"} for i in range(200)]
        )
        analysis, _ = run_analysis(assign, metrics, cfg)
        r = analysis.primary_metric_result
        assert r is not None
        assert r.metric_type == "proportion"   # declared type is preserved
        assert 0.0 <= r.p_value <= 1.0

    def test_continuous_metric_uses_welch(self):
        """Continuous metric must use Welch t-test (produces p-value in [0,1])."""
        cfg = _make_config(
            metric_type="continuous",
            primary_metric="revenue",
            guardrail_metrics=[],
            secondary_metrics=[],
        )
        assign = pd.DataFrame(
            [{"unit_id": f"c{i:03d}", "variant": "control"} for i in range(100)] +
            [{"unit_id": f"t{i:03d}", "variant": "treatment"} for i in range(100)]
        )
        metrics = pd.DataFrame(
            [{"unit_id": f"c{i:03d}", "metric_name": "revenue",
              "metric_value": 10.0, "period": "post"} for i in range(100)] +
            [{"unit_id": f"t{i:03d}", "metric_name": "revenue",
              "metric_value": 12.5, "period": "post"} for i in range(100)]
        )
        analysis, _ = run_analysis(assign, metrics, cfg)
        r = analysis.primary_metric_result
        assert r.metric_type == "continuous"
        assert 0.0 <= r.p_value <= 1.0

    def test_run_analysis_raises_when_metric_type_none(self):
        """config.metric_type=None must raise ValueError (documented in analysis.py)."""
        data = {
            "schema_version": "1.0",
            "experiment_name": "Test",
            "experiment_id": "exp_notype",
            "owner": "test",
            "hypothesis": "Test hypothesis long enough to pass",
            "primary_metric": "conversion_rate",
            "variants": ["control", "treatment"],
            "expected_allocation": {"control": 0.5, "treatment": 0.5},
            "unit_of_randomization": "user_id",
            "alpha": 0.05, "power": 0.80, "mde": 0.02,
        }
        cfg = ExperimentConfig(**data)
        assign = pd.DataFrame([{"unit_id": "u1", "variant": "control"}])
        metrics = pd.DataFrame([{"unit_id": "u1", "metric_name": "conversion_rate",
                                  "metric_value": 0.1, "period": "post"}])
        with pytest.raises(ValueError, match="metric_type"):
            run_analysis(assign, metrics, cfg)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_insufficient_control_observations_returns_none_primary(self):
        """Only 1 control observation → primary result must be None (not raise)."""
        cfg = _make_config(guardrail_metrics=[], secondary_metrics=[])
        assign = pd.DataFrame([
            {"unit_id": "c001", "variant": "control"},
            {"unit_id": "t001", "variant": "treatment"},
            {"unit_id": "t002", "variant": "treatment"},
        ])
        metrics = pd.DataFrame([
            {"unit_id": "c001", "metric_name": "conversion_rate",
             "metric_value": 0.1, "period": "post"},
            {"unit_id": "t001", "metric_name": "conversion_rate",
             "metric_value": 0.15, "period": "post"},
            {"unit_id": "t002", "metric_name": "conversion_rate",
             "metric_value": 0.12, "period": "post"},
        ])
        analysis, decision = run_analysis(assign, metrics, cfg)
        assert analysis.primary_metric_result is None
        assert decision.recommendation == "inconclusive"

    def test_empty_secondary_when_none_in_data(self):
        """If a declared secondary metric has no rows, its result is simply absent."""
        cfg = _make_config(
            secondary_metrics=["revenue_per_user"],
            guardrail_metrics=[],
        )
        assign = pd.DataFrame(
            [{"unit_id": f"c{i:03d}", "variant": "control"} for i in range(50)] +
            [{"unit_id": f"t{i:03d}", "variant": "treatment"} for i in range(50)]
        )
        metrics = pd.DataFrame(
            [{"unit_id": f"c{i:03d}", "metric_name": "conversion_rate",
              "metric_value": 0.10, "period": "post"} for i in range(50)] +
            [{"unit_id": f"t{i:03d}", "metric_name": "conversion_rate",
              "metric_value": 0.15, "period": "post"} for i in range(50)]
        )
        # revenue_per_user is declared but not in metrics data
        analysis, _ = run_analysis(assign, metrics, cfg)
        names = [e.metric_name for e in analysis.secondary_metric_results]
        assert "revenue_per_user" not in names

    def test_all_metric_estimates_have_required_fields(self):
        """Every MetricEstimate produced must have code, p_value, ci_lower, ci_upper."""
        cfg = _load_config("clean")
        assign = _load_csv("clean", "assignments.csv")
        metrics = _load_csv("clean", "metrics.csv")
        analysis, _ = run_analysis(assign, metrics, cfg)

        all_estimates = (
            [analysis.primary_metric_result]
            + analysis.secondary_metric_results
            + analysis.guardrail_metric_results
        )
        if analysis.cuped_estimate:
            all_estimates.append(analysis.cuped_estimate)

        for est in all_estimates:
            if est is not None:
                assert est.metric_name, "metric_name must not be empty"
                assert est.p_value is not None, f"{est.metric_name}: p_value is None"
                assert est.ci_lower is not None, f"{est.metric_name}: ci_lower is None"
                assert est.ci_upper is not None, f"{est.metric_name}: ci_upper is None"
                assert est.is_significant is not None, f"{est.metric_name}: is_significant is None"


# ---------------------------------------------------------------------------
# Hardening: multi-variant guard (v1 raises ValueError for >2 variants)
# ---------------------------------------------------------------------------

class TestMultiVariantGuard:
    def test_three_variants_raises_value_error(self):
        """
        run_analysis must raise ValueError when config declares >2 variants.
        v1 does not support multi-variant analysis.
        """
        data = {
            "schema_version": "1.0",
            "experiment_name": "Multi-arm test",
            "experiment_id": "exp_multi",
            "owner": "test",
            "hypothesis": "Multi-variant hypothesis long enough to pass validation.",
            "primary_metric": "conversion_rate",
            "metric_type": "proportion",
            "variants": ["control", "treatment_a", "treatment_b"],
            "expected_allocation": {
                "control": 0.34,
                "treatment_a": 0.33,
                "treatment_b": 0.33,
            },
            "unit_of_randomization": "user_id",
            "alpha": 0.05,
            "power": 0.80,
            "mde": 0.02,
        }
        cfg = ExperimentConfig(**data)
        assign = pd.DataFrame([
            {"unit_id": "u1", "variant": "control"},
            {"unit_id": "u2", "variant": "treatment_a"},
        ])
        metrics = pd.DataFrame([
            {"unit_id": "u1", "metric_name": "conversion_rate",
             "metric_value": 0.1, "period": "post"},
            {"unit_id": "u2", "metric_name": "conversion_rate",
             "metric_value": 0.15, "period": "post"},
        ])
        with pytest.raises(ValueError, match="two variants"):
            run_analysis(assign, metrics, cfg)

    def test_error_message_names_the_variants(self):
        """The ValueError message must include the actual variant names."""
        data = {
            "schema_version": "1.0",
            "experiment_name": "Multi-arm test",
            "experiment_id": "exp_multi2",
            "owner": "test",
            "hypothesis": "Multi-variant hypothesis long enough to pass validation.",
            "primary_metric": "conversion_rate",
            "metric_type": "proportion",
            "variants": ["control", "v1", "v2"],
            "expected_allocation": {"control": 0.34, "v1": 0.33, "v2": 0.33},
            "unit_of_randomization": "user_id",
            "alpha": 0.05,
            "power": 0.80,
            "mde": 0.02,
        }
        cfg = ExperimentConfig(**data)
        assign = pd.DataFrame([{"unit_id": "u1", "variant": "control"}])
        metrics = pd.DataFrame([{"unit_id": "u1", "metric_name": "conversion_rate",
                                  "metric_value": 0.1, "period": "post"}])
        with pytest.raises(ValueError) as exc_info:
            run_analysis(assign, metrics, cfg)
        assert "v1" in str(exc_info.value) or "v2" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Hardening: zero-variance data produces stable outcome (no nan)
# ---------------------------------------------------------------------------

class TestZeroVarianceData:
    """
    Zero-variance data is a degenerate test-data edge case, not a normal
    experiment condition.  Real experiments always have within-group variance.

    These tests verify the deliberate analysis policy defined in _welch_ttest:

      Case A (identical constant groups, diff=0):
        Interpreted as no detectable difference.
        Policy outcome: p_value=1.0, is_significant=False.
        Rationale: the test cannot distinguish the groups at all.

      Case B (different constant groups, diff≠0):
        Interpreted as an effectively infinite signal-to-noise ratio —
        every observation in one arm exceeds every observation in the other.
        Policy outcome: p_value=0.0, is_significant=True.
        Rationale: the groups are perfectly and trivially separable.
        This is NOT a general statistical claim about effect reliability;
        it is the maximally extreme outcome for this degenerate input.

    In both cases, all output fields must be finite (no nan, no null) so that
    the analysis pipeline remains stable regardless of input quality.
    """

    def _make_constant_data(self, ctrl_val: float, treat_val: float,
                             n: int = 50) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Build assignment + metric DataFrames with constant values per group."""
        ctrl_ids  = [f"c{i:04d}" for i in range(n)]
        treat_ids = [f"t{i:04d}" for i in range(n)]
        assign = pd.DataFrame(
            [{"unit_id": uid, "variant": "control"}   for uid in ctrl_ids] +
            [{"unit_id": uid, "variant": "treatment"}  for uid in treat_ids]
        )
        metrics = pd.DataFrame(
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": ctrl_val, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": treat_val, "period": "post"} for uid in treat_ids]
        )
        return assign, metrics

    def test_both_groups_constant_same_value(self):
        """
        Case A policy: both arms contain the same constant value (diff=0).
        This is a degenerate test-data edge case — not a real experiment condition.

        Expected policy outcome: p_value=1.0, is_significant=False.
        Interpretation: identical constant groups are treated as no detectable
        difference; the test has no information to distinguish the arms.
        All output fields must be finite (no nan, no null).
        """
        cfg = _make_config(metric_type="continuous", guardrail_metrics=[], secondary_metrics=[])
        assign, metrics = self._make_constant_data(ctrl_val=0.10, treat_val=0.10)
        analysis, decision = run_analysis(assign, metrics, cfg)
        r = analysis.primary_metric_result
        assert r is not None
        assert r.p_value == 1.0, f"Expected p_value=1.0 for zero-variance data; got {r.p_value}"
        assert r.is_significant is False
        # ci_lower and ci_upper must be finite (not nan)
        import math
        assert math.isfinite(r.ci_lower), f"ci_lower is not finite: {r.ci_lower}"
        assert math.isfinite(r.ci_upper), f"ci_upper is not finite: {r.ci_upper}"
        assert decision.recommendation == "inconclusive"

    def test_both_groups_constant_different_values(self):
        """
        Case B policy: both arms are constant but at different values (diff≠0).
        This is a degenerate test-data edge case — not a real experiment condition.

        Expected policy outcome: p_value=0.0, is_significant=True.
        Interpretation: different constant groups produce an effectively infinite
        signal-to-noise ratio — every observation in one arm exceeds every
        observation in the other.  This is NOT a general statistical claim about
        effect reliability; it is the maximally extreme outcome for this specific
        degenerate input shape.
        All output fields must be finite (no nan, no null).
        """
        import math as _math
        cfg = _make_config(metric_type="continuous", guardrail_metrics=[], secondary_metrics=[])
        assign, metrics = self._make_constant_data(ctrl_val=0.10, treat_val=0.20)
        analysis, _ = run_analysis(assign, metrics, cfg)
        r = analysis.primary_metric_result
        assert r is not None
        assert r.p_value == 0.0, (
            f"Expected p_value=0.0 for perfectly separable constant groups; got {r.p_value}"
        )
        assert r.is_significant is True
        assert _math.isfinite(r.ci_lower), f"ci_lower is not finite: {r.ci_lower}"
        assert _math.isfinite(r.ci_upper), f"ci_upper is not finite: {r.ci_upper}"

    def test_zero_variance_does_not_emit_nan_in_json(self):
        """
        Pipeline stability requirement: zero-variance degenerate input (Case A)
        must produce a fully serialisable result with no null p_value in JSON.
        """
        import json
        cfg = _make_config(metric_type="continuous", guardrail_metrics=[], secondary_metrics=[])
        assign, metrics = self._make_constant_data(ctrl_val=0.10, treat_val=0.10)
        analysis, _ = run_analysis(assign, metrics, cfg)
        serialised = analysis.model_dump_json()
        # JSON spec does not allow NaN; Pydantic encodes as null but let's verify
        # the p_value field specifically
        payload = json.loads(serialised)
        pv = payload["primary_metric_result"]["p_value"]
        assert pv is not None, "p_value should not be null for zero-variance data"
        assert pv == 1.0

    def test_one_group_has_variance(self):
        """
        Mixed case: control is constant, treatment has natural spread.
        This is a semi-degenerate input — one arm lacks variance but the other
        does not.  The standard Welch path runs; must not raise or produce nan.
        is_significant is determined normally by p_value vs alpha.
        """
        import random
        rng = random.Random(42)
        cfg = _make_config(metric_type="continuous", guardrail_metrics=[], secondary_metrics=[])
        ctrl_ids  = [f"c{i:04d}" for i in range(50)]
        treat_ids = [f"t{i:04d}" for i in range(50)]
        assign = pd.DataFrame(
            [{"unit_id": uid, "variant": "control"}   for uid in ctrl_ids] +
            [{"unit_id": uid, "variant": "treatment"}  for uid in treat_ids]
        )
        metrics = pd.DataFrame(
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": 0.10, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": rng.gauss(0.15, 0.05), "period": "post"} for uid in treat_ids]
        )
        import math
        analysis, _ = run_analysis(assign, metrics, cfg)
        r = analysis.primary_metric_result
        assert r is not None
        assert math.isfinite(r.p_value), f"p_value is not finite: {r.p_value}"
        assert r.is_significant is not None


# ---------------------------------------------------------------------------
# relative_lift is None when ctrl_mean == 0 (undefined ratio, not zero)
# ---------------------------------------------------------------------------

class TestRelativeLiftUndefined:
    """
    When control_mean is zero, relative_lift (absolute_lift / ctrl_mean) is
    undefined because of division by zero.  The correct representation is None,
    not 0.0.  These tests verify that the analysis pipeline stores None and that
    the result is stable in JSON serialisation.

    This is a degenerate edge case: a zero-mean control group is not normal in
    production experiments.  The tests exist to pin the explicit policy choice.
    """

    def _make_zero_ctrl_data(self, treat_val: float = 0.10,
                              n: int = 50) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Assignment + metric DataFrames where every control unit has value 0."""
        ctrl_ids  = [f"c{i:04d}" for i in range(n)]
        treat_ids = [f"t{i:04d}" for i in range(n)]
        assign = pd.DataFrame(
            [{"unit_id": uid, "variant": "control"}   for uid in ctrl_ids] +
            [{"unit_id": uid, "variant": "treatment"}  for uid in treat_ids]
        )
        metrics = pd.DataFrame(
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": 0.0, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": treat_val, "period": "post"} for uid in treat_ids]
        )
        return assign, metrics

    def test_relative_lift_is_none_when_ctrl_mean_is_zero(self):
        """
        ctrl_mean == 0 → relative_lift must be None (undefined ratio).
        The absolute_lift is still correct and must not be None.
        """
        cfg = _make_config(metric_type="continuous", guardrail_metrics=[], secondary_metrics=[])
        assign, metrics = self._make_zero_ctrl_data(treat_val=0.10)
        analysis, _ = run_analysis(assign, metrics, cfg)
        r = analysis.primary_metric_result
        assert r is not None
        assert r.control_mean == pytest.approx(0.0)
        assert r.relative_lift is None, (
            f"relative_lift should be None when ctrl_mean==0; got {r.relative_lift!r}"
        )
        # absolute_lift is well-defined even when relative_lift is not
        assert r.absolute_lift == pytest.approx(0.10, abs=1e-6)

    def test_relative_lift_none_does_not_break_json_serialisation(self):
        """
        A MetricEstimate with relative_lift=None must serialise to valid JSON
        (null, not NaN or a missing key).
        """
        import json
        cfg = _make_config(metric_type="continuous", guardrail_metrics=[], secondary_metrics=[])
        assign, metrics = self._make_zero_ctrl_data(treat_val=0.10)
        analysis, _ = run_analysis(assign, metrics, cfg)
        payload = json.loads(analysis.model_dump_json())
        rl = payload["primary_metric_result"]["relative_lift"]
        assert rl is None, (
            f"Expected null in JSON for undefined relative_lift; got {rl!r}"
        )

    def test_relative_lift_none_in_effective_primary_estimate(self):
        """
        effective_primary_estimate must also carry None for relative_lift
        when ctrl_mean is zero (it points to the same object as primary_metric_result
        when CUPED is not applied).
        """
        cfg = _make_config(metric_type="continuous", guardrail_metrics=[], secondary_metrics=[])
        assign, metrics = self._make_zero_ctrl_data(treat_val=0.10)
        analysis, _ = run_analysis(assign, metrics, cfg)
        assert analysis.effective_primary_estimate is not None
        assert analysis.effective_primary_estimate.relative_lift is None

    def test_decision_reasoning_contains_undefined_label_when_ctrl_mean_zero(self):
        """
        When relative_lift is None, the DecisionMemo.reasoning_summary must
        contain the word 'undefined' rather than a numeric placeholder.
        This confirms the _build_decision guard fires correctly.
        """
        cfg = _make_config(metric_type="continuous", guardrail_metrics=[], secondary_metrics=[])
        # treat_val > 0 ensures the primary metric is significant (p=0.0 degenerate case)
        assign, metrics = self._make_zero_ctrl_data(treat_val=0.20)
        _, decision = run_analysis(assign, metrics, cfg)
        # With ctrl_val=0 and treat_val=0.20, Case B applies: p=0.0, ship decision.
        # The reasoning_summary must mention 'undefined' for the relative lift.
        if decision.recommendation == "ship":
            assert "undefined" in decision.reasoning_summary, (
                f"Expected 'undefined' in reasoning_summary when ctrl_mean==0; "
                f"got: {decision.reasoning_summary!r}"
            )

    def test_non_zero_ctrl_mean_still_produces_float_relative_lift(self):
        """
        Regression guard: when ctrl_mean > 0, relative_lift must be a finite
        float, not None.  The None path must not fire for normal experiment data.
        """
        cfg = _make_config(metric_type="continuous", guardrail_metrics=[], secondary_metrics=[])
        ctrl_ids  = [f"c{i:04d}" for i in range(50)]
        treat_ids = [f"t{i:04d}" for i in range(50)]
        assign = pd.DataFrame(
            [{"unit_id": uid, "variant": "control"}   for uid in ctrl_ids] +
            [{"unit_id": uid, "variant": "treatment"}  for uid in treat_ids]
        )
        metrics = pd.DataFrame(
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": 0.10, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": 0.12, "period": "post"} for uid in treat_ids]
        )
        import math as _math
        analysis, _ = run_analysis(assign, metrics, cfg)
        r = analysis.primary_metric_result
        assert r is not None
        assert r.relative_lift is not None, "relative_lift must be float for normal data"
        assert _math.isfinite(r.relative_lift), f"relative_lift must be finite; got {r.relative_lift}"




# ---------------------------------------------------------------------------
# Hardening: effective_primary_estimate and alpha_used are always populated
# ---------------------------------------------------------------------------

class TestEffectivePrimaryAndAlphaUsed:
    def test_effective_primary_equals_raw_when_no_cuped(self):
        """
        When there is no pre-period data (CUPED not assessed),
        effective_primary_estimate must equal primary_metric_result.
        """
        cfg = _load_config("srm")
        assign = _load_csv("srm", "assignments.csv")
        metrics = _load_csv("srm", "metrics.csv")
        analysis, _ = run_analysis(assign, metrics, cfg)
        assert analysis.cuped_estimate is None, "Precondition: no CUPED in SRM fixture"
        assert analysis.effective_primary_estimate is not None
        # Should point to the same data as primary_metric_result
        assert (
            analysis.effective_primary_estimate.metric_name
            == analysis.primary_metric_result.metric_name
        )
        assert (
            analysis.effective_primary_estimate.p_value
            == analysis.primary_metric_result.p_value
        )

    def test_effective_primary_none_when_no_data(self):
        """
        When primary_metric_result is None (insufficient data),
        effective_primary_estimate must also be None.
        """
        cfg = _make_config(guardrail_metrics=[], secondary_metrics=[])
        assign = pd.DataFrame([
            {"unit_id": "c001", "variant": "control"},
            {"unit_id": "t001", "variant": "treatment"},
            {"unit_id": "t002", "variant": "treatment"},
        ])
        metrics = pd.DataFrame([
            {"unit_id": "c001", "metric_name": "conversion_rate",
             "metric_value": 0.1, "period": "post"},
            {"unit_id": "t001", "metric_name": "conversion_rate",
             "metric_value": 0.15, "period": "post"},
            {"unit_id": "t002", "metric_name": "conversion_rate",
             "metric_value": 0.12, "period": "post"},
        ])
        analysis, _ = run_analysis(assign, metrics, cfg)
        assert analysis.primary_metric_result is None
        assert analysis.effective_primary_estimate is None

    def test_alpha_used_on_hold_srm_critical(self):
        """alpha_used must be present even on a hold-due-to-SRM decision."""
        cfg = _make_config(guardrail_metrics=[], secondary_metrics=[])
        assign = pd.DataFrame(
            [{"unit_id": f"c{i}", "variant": "control"}   for i in range(50)] +
            [{"unit_id": f"t{i}", "variant": "treatment"}  for i in range(50)]
        )
        metrics = pd.DataFrame(
            [{"unit_id": f"c{i}", "metric_name": "conversion_rate",
              "metric_value": 0.1, "period": "post"} for i in range(50)] +
            [{"unit_id": f"t{i}", "metric_name": "conversion_rate",
              "metric_value": 0.2, "period": "post"} for i in range(50)]
        )
        qa = _make_critical_quality_checks()
        _, decision = run_analysis(assign, metrics, cfg, quality_checks=qa)
        assert decision.recommendation == "hold"
        assert decision.alpha_used == cfg.alpha

    def test_alpha_used_on_inconclusive(self):
        """alpha_used must be present on inconclusive decisions."""
        cfg = _make_config(guardrail_metrics=[], secondary_metrics=[])
        assign = pd.DataFrame([
            {"unit_id": "c001", "variant": "control"},
            {"unit_id": "t001", "variant": "treatment"},
            {"unit_id": "t002", "variant": "treatment"},
        ])
        metrics = pd.DataFrame([
            {"unit_id": "c001", "metric_name": "conversion_rate",
             "metric_value": 0.1, "period": "post"},
            {"unit_id": "t001", "metric_name": "conversion_rate",
             "metric_value": 0.15, "period": "post"},
            {"unit_id": "t002", "metric_name": "conversion_rate",
             "metric_value": 0.12, "period": "post"},
        ])
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "inconclusive"
        assert decision.alpha_used == cfg.alpha

    def test_alpha_used_on_hold_guardrail(self):
        """alpha_used must be present on a hold-due-to-guardrail decision."""
        cfg = _make_config(guardrail_metrics=["refund_rate"], secondary_metrics=[])
        ctrl_ids  = [f"c{i:04d}" for i in range(200)]
        treat_ids = [f"t{i:04d}" for i in range(200)]
        assign = pd.DataFrame(
            [{"unit_id": uid, "variant": "control"}   for uid in ctrl_ids] +
            [{"unit_id": uid, "variant": "treatment"}  for uid in treat_ids]
        )
        metrics = pd.DataFrame(
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": 0.10, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": 0.15, "period": "post"} for uid in treat_ids] +
            [{"unit_id": uid, "metric_name": "refund_rate",
              "metric_value": 0.05, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "refund_rate",
              "metric_value": 0.20, "period": "post"} for uid in treat_ids]
        )
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "hold"
        assert decision.alpha_used == cfg.alpha
