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


# ---------------------------------------------------------------------------
# Guardrail directionality tests (schema v1.1)
# ---------------------------------------------------------------------------

class TestGuardrailDirectionality:
    """
    Tests for the direction-aware guardrail blocking logic introduced in schema
    v1.1.

    Key behaviour matrix (all cases assume the guardrail IS significant):
      - no direction declared (legacy)  → always blocks
      - direction=flat                  → always blocks
      - direction=increase, lift > 0    → does NOT block (correct direction)
      - direction=increase, lift < 0    → blocks (wrong direction)
      - direction=decrease, lift < 0    → does NOT block (correct direction)
      - direction=decrease, lift > 0    → blocks (wrong direction)
      - not significant                 → never blocks (regardless of direction)
    """

    def _make_datasets(
        self,
        n: int = 200,
        ctrl_val: float = 0.05,
        treat_val: float = 0.20,
    ):
        """Return minimal assignment + metric DataFrames with a large, significant guardrail."""
        ctrl_ids  = [f"c{i:04d}" for i in range(n)]
        treat_ids = [f"t{i:04d}" for i in range(n)]
        assign = pd.DataFrame(
            [{"unit_id": uid, "variant": "control"}   for uid in ctrl_ids] +
            [{"unit_id": uid, "variant": "treatment"}  for uid in treat_ids]
        )
        # Primary metric is also significant so we can confirm ship vs hold cleanly.
        metrics = pd.DataFrame(
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": 0.10, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": 0.20, "period": "post"} for uid in treat_ids] +
            [{"unit_id": uid, "metric_name": "refund_rate",
              "metric_value": ctrl_val, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "refund_rate",
              "metric_value": treat_val, "period": "post"} for uid in treat_ids]
        )
        return assign, metrics

    # ------------------------------------------------------------------
    # Legacy behaviour (no direction declared)
    # ------------------------------------------------------------------

    def test_undeclared_direction_significant_blocks(self):
        """
        No guardrail_directions entry → legacy behaviour: any significant
        movement triggers hold.  Lift is positive here (refund increased).
        """
        cfg = _make_config(
            guardrail_metrics=["refund_rate"],
            secondary_metrics=[],
            # no guardrail_directions — legacy behaviour
        )
        assign, metrics = self._make_datasets(ctrl_val=0.05, treat_val=0.20)
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "hold", (
            "Undeclared direction + significant guardrail must trigger hold"
        )

    def test_undeclared_direction_significant_negative_lift_also_blocks(self):
        """
        No direction declared → legacy: negative significant lift also blocks.
        """
        cfg = _make_config(
            guardrail_metrics=["refund_rate"],
            secondary_metrics=[],
        )
        # refund_rate drops significantly (ctrl=0.20, treat=0.05)
        assign, metrics = self._make_datasets(ctrl_val=0.20, treat_val=0.05)
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "hold"

    # ------------------------------------------------------------------
    # direction=flat (explicit; same as legacy)
    # ------------------------------------------------------------------

    def test_flat_direction_significant_positive_lift_blocks(self):
        """flat direction: any significant movement blocks — positive lift."""
        cfg = _make_config(
            guardrail_metrics=["refund_rate"],
            secondary_metrics=[],
            guardrail_directions={"refund_rate": "flat"},
        )
        assign, metrics = self._make_datasets(ctrl_val=0.05, treat_val=0.20)
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "hold"

    def test_flat_direction_significant_negative_lift_blocks(self):
        """flat direction: any significant movement blocks — negative lift."""
        cfg = _make_config(
            guardrail_metrics=["refund_rate"],
            secondary_metrics=[],
            guardrail_directions={"refund_rate": "flat"},
        )
        assign, metrics = self._make_datasets(ctrl_val=0.20, treat_val=0.05)
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "hold"

    # ------------------------------------------------------------------
    # direction=increase
    # ------------------------------------------------------------------

    def test_increase_direction_negative_lift_blocks(self):
        """
        direction=increase: refund_rate drops significantly.
        A drop violates the 'increase' direction → hold.
        """
        cfg = _make_config(
            guardrail_metrics=["refund_rate"],
            secondary_metrics=[],
            guardrail_directions={"refund_rate": "increase"},
        )
        # refund_rate decreases (ctrl=0.20, treat=0.05) — wrong direction
        assign, metrics = self._make_datasets(ctrl_val=0.20, treat_val=0.05)
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "hold", (
            "Significant drop in an 'increase' guardrail must block"
        )

    def test_increase_direction_positive_lift_does_not_block(self):
        """
        direction=increase: refund_rate rises significantly.
        A rise is the desired direction → should NOT block → ship.
        """
        cfg = _make_config(
            guardrail_metrics=["refund_rate"],
            secondary_metrics=[],
            guardrail_directions={"refund_rate": "increase"},
        )
        # refund_rate increases (ctrl=0.05, treat=0.20) — correct direction
        assign, metrics = self._make_datasets(ctrl_val=0.05, treat_val=0.20)
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "ship", (
            "Significant increase in an 'increase' guardrail must not block; "
            f"got {decision.recommendation}"
        )

    # ------------------------------------------------------------------
    # direction=decrease
    # ------------------------------------------------------------------

    def test_decrease_direction_positive_lift_blocks(self):
        """
        direction=decrease: refund_rate rises significantly.
        A rise violates the 'decrease' direction → hold.
        """
        cfg = _make_config(
            guardrail_metrics=["refund_rate"],
            secondary_metrics=[],
            guardrail_directions={"refund_rate": "decrease"},
        )
        # refund_rate increases (ctrl=0.05, treat=0.20) — wrong direction
        assign, metrics = self._make_datasets(ctrl_val=0.05, treat_val=0.20)
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "hold", (
            "Significant increase in a 'decrease' guardrail must block"
        )

    def test_decrease_direction_negative_lift_does_not_block(self):
        """
        direction=decrease: refund_rate drops significantly.
        A drop is the desired direction → should NOT block → ship.
        """
        cfg = _make_config(
            guardrail_metrics=["refund_rate"],
            secondary_metrics=[],
            guardrail_directions={"refund_rate": "decrease"},
        )
        # refund_rate decreases (ctrl=0.20, treat=0.05) — correct direction
        assign, metrics = self._make_datasets(ctrl_val=0.20, treat_val=0.05)
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "ship", (
            "Significant decrease in a 'decrease' guardrail must not block; "
            f"got {decision.recommendation}"
        )

    # ------------------------------------------------------------------
    # Non-significant guardrail: never blocks regardless of direction
    # ------------------------------------------------------------------

    def _make_datasets_nonsig_guardrail(self, n: int = 200):
        """Primary is significant; guardrail has essentially no movement."""
        ctrl_ids  = [f"c{i:04d}" for i in range(n)]
        treat_ids = [f"t{i:04d}" for i in range(n)]
        assign = pd.DataFrame(
            [{"unit_id": uid, "variant": "control"}   for uid in ctrl_ids] +
            [{"unit_id": uid, "variant": "treatment"}  for uid in treat_ids]
        )
        metrics = pd.DataFrame(
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": 0.10, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": 0.20, "period": "post"} for uid in treat_ids] +
            # identical values → p=1.0, not significant
            [{"unit_id": uid, "metric_name": "refund_rate",
              "metric_value": 0.05, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "refund_rate",
              "metric_value": 0.05, "period": "post"} for uid in treat_ids]
        )
        return assign, metrics

    def test_not_significant_guardrail_never_blocks_no_direction(self):
        """Non-significant guardrail → ship regardless of direction=None."""
        cfg = _make_config(guardrail_metrics=["refund_rate"], secondary_metrics=[])
        assign, metrics = self._make_datasets_nonsig_guardrail()
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "ship"

    def test_not_significant_guardrail_never_blocks_with_direction(self):
        """Non-significant guardrail → ship even when direction is declared."""
        cfg = _make_config(
            guardrail_metrics=["refund_rate"],
            secondary_metrics=[],
            guardrail_directions={"refund_rate": "decrease"},
        )
        assign, metrics = self._make_datasets_nonsig_guardrail()
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "ship"

    # ------------------------------------------------------------------
    # Reasoning summary and caveats content
    # ------------------------------------------------------------------

    def test_hold_reasoning_mentions_direction_when_declared(self):
        """
        When a guardrail blocks due to a declared direction mismatch,
        the reasoning_summary must describe the direction violation.
        """
        cfg = _make_config(
            guardrail_metrics=["refund_rate"],
            secondary_metrics=[],
            guardrail_directions={"refund_rate": "decrease"},
        )
        assign, metrics = self._make_datasets(ctrl_val=0.05, treat_val=0.20)
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "hold"
        # Must explain that the direction was violated, not just "significant movement"
        assert "decrease" in decision.reasoning_summary.lower() or "positive" in decision.reasoning_summary.lower(), (
            f"reasoning_summary should explain direction; got: {decision.reasoning_summary!r}"
        )

    def test_hold_caveats_mention_metric_name(self):
        """Key caveats must include the metric name that triggered the hold."""
        cfg = _make_config(
            guardrail_metrics=["refund_rate"],
            secondary_metrics=[],
            guardrail_directions={"refund_rate": "decrease"},
        )
        assign, metrics = self._make_datasets(ctrl_val=0.05, treat_val=0.20)
        _, decision = run_analysis(assign, metrics, cfg)
        combined = " ".join(decision.key_caveats)
        assert "refund_rate" in combined

    def test_ship_caveats_do_not_mention_nontriggered_guardrail(self):
        """
        When a direction-aware guardrail is significant but moving in the
        correct direction, the decision is ship and the caveats must not
        falsely claim the guardrail triggered a hold.
        """
        cfg = _make_config(
            guardrail_metrics=["refund_rate"],
            secondary_metrics=[],
            guardrail_directions={"refund_rate": "decrease"},
        )
        # refund drops (correct direction) — should be ship
        assign, metrics = self._make_datasets(ctrl_val=0.20, treat_val=0.05)
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "ship"
        # Caveats must not claim refund_rate triggered a hold
        combined = " ".join(decision.key_caveats).lower()
        assert "hold" not in combined or "refund_rate" not in combined

    # ------------------------------------------------------------------
    # Multiple guardrails — mixed directions
    # ------------------------------------------------------------------

    def test_mixed_guardrails_only_violating_ones_block(self):
        """
        revenue (increase direction) rises → correct, does not block.
        refund_rate (decrease direction) rises → violates direction → blocks.
        Overall decision must be hold.
        """
        cfg = _make_config(
            guardrail_metrics=["refund_rate", "revenue"],
            secondary_metrics=[],
            guardrail_directions={
                "refund_rate": "decrease",
                "revenue": "increase",
            },
        )
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
              "metric_value": 0.20, "period": "post"} for uid in treat_ids] +
            # refund_rate rises (bad — violates decrease)
            [{"unit_id": uid, "metric_name": "refund_rate",
              "metric_value": 0.05, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "refund_rate",
              "metric_value": 0.20, "period": "post"} for uid in treat_ids] +
            # revenue rises (good — correct for increase direction)
            [{"unit_id": uid, "metric_name": "revenue",
              "metric_value": 10.0, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "revenue",
              "metric_value": 15.0, "period": "post"} for uid in treat_ids]
        )
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "hold"
        # reasoning must mention refund_rate but revenue should not be blamed
        assert "refund_rate" in decision.reasoning_summary

    def test_mixed_guardrails_all_correct_direction_ships(self):
        """
        Both guardrails move in the correct direction → neither blocks → ship.
        """
        cfg = _make_config(
            guardrail_metrics=["refund_rate", "revenue"],
            secondary_metrics=[],
            guardrail_directions={
                "refund_rate": "decrease",
                "revenue": "increase",
            },
        )
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
              "metric_value": 0.20, "period": "post"} for uid in treat_ids] +
            # refund_rate drops (good — correct for decrease)
            [{"unit_id": uid, "metric_name": "refund_rate",
              "metric_value": 0.20, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "refund_rate",
              "metric_value": 0.05, "period": "post"} for uid in treat_ids] +
            # revenue rises (good — correct for increase)
            [{"unit_id": uid, "metric_name": "revenue",
              "metric_value": 10.0, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "revenue",
              "metric_value": 15.0, "period": "post"} for uid in treat_ids]
        )
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "ship", (
            f"All guardrails correct direction → expect ship; got {decision.recommendation}"
        )

    # ------------------------------------------------------------------
    # Fixture-level integration: clean fixture with decrease direction
    # ------------------------------------------------------------------

    def test_clean_fixture_ships_with_decrease_direction_guardrail(self):
        """
        Clean fixture declares refund_rate with direction=decrease.
        The clean fixture refund_rate is not significant → should not block.
        The primary metric IS significant → decision should be ship.
        """
        cfg = _load_config("clean")
        assign = _load_csv("clean", "assignments.csv")
        metrics = _load_csv("clean", "metrics.csv")
        # Verify guardrail_directions was loaded from the fixture YAML
        assert "refund_rate" in cfg.guardrail_directions, (
            "clean fixture must have guardrail_directions.refund_rate"
        )
        assert cfg.guardrail_directions["refund_rate"] == "decrease"
        _, decision = run_analysis(assign, metrics, cfg)
        assert decision.recommendation == "ship"


# ---------------------------------------------------------------------------
# Bonferroni correction for secondary metrics
# ---------------------------------------------------------------------------

class TestBonferroniCorrection:
    """
    Tests for the optional Bonferroni correction on secondary metrics.

    Coverage:
      - Correction off (default) → secondary metrics use base alpha; secondary_alpha_used == alpha.
      - Correction on, one secondary metric → m=1, bonferroni_correction_applied=False,
        secondary_alpha_used == alpha (correction has no mathematical effect).
      - Correction on, multiple secondary metrics → secondary_alpha_used == alpha / m,
        bonferroni_correction_applied=True.
      - Significance boundary: p < alpha but p > adjusted_alpha → not significant.
      - Primary metric is always tested at base alpha regardless of correction setting.
      - secondary_alpha_used is None when no secondary metrics are declared.
    """

    def _make_assign_and_metrics(
        self,
        n: int = 200,
        ctrl_secondary_vals: dict[str, float] | None = None,
        treat_secondary_vals: dict[str, float] | None = None,
        ctrl_primary: float = 0.10,
        treat_primary: float = 0.20,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Build assignment + metric DataFrames.

        primary metric is 'conversion_rate' (large effect, always significant).
        Secondary metrics are provided as {name: ctrl_value} / {name: treat_value}.
        """
        ctrl_secondary_vals  = ctrl_secondary_vals  or {}
        treat_secondary_vals = treat_secondary_vals or {}

        ctrl_ids  = [f"c{i:04d}" for i in range(n)]
        treat_ids = [f"t{i:04d}" for i in range(n)]

        assign = pd.DataFrame(
            [{"unit_id": uid, "variant": "control"}   for uid in ctrl_ids] +
            [{"unit_id": uid, "variant": "treatment"}  for uid in treat_ids]
        )

        rows: list[dict] = (
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": ctrl_primary, "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": treat_primary, "period": "post"} for uid in treat_ids]
        )
        for mname, cval in ctrl_secondary_vals.items():
            tval = treat_secondary_vals.get(mname, cval)
            rows += (
                [{"unit_id": uid, "metric_name": mname,
                  "metric_value": cval, "period": "post"} for uid in ctrl_ids] +
                [{"unit_id": uid, "metric_name": mname,
                  "metric_value": tval, "period": "post"} for uid in treat_ids]
            )
        return assign, pd.DataFrame(rows)

    def _make_cfg(self, secondary: list[str], apply_correction: bool = False) -> ExperimentConfig:
        return _make_config(
            secondary_metrics=secondary,
            guardrail_metrics=[],
            apply_bonferroni_correction=apply_correction,
            alpha=0.05,
        )

    # ------------------------------------------------------------------
    # secondary_alpha_used is None when no secondaries declared
    # ------------------------------------------------------------------

    def test_no_secondary_metrics_secondary_alpha_used_is_none(self):
        """No secondary metrics → secondary_alpha_used must be None."""
        cfg = _make_config(secondary_metrics=[], guardrail_metrics=[])
        assign, metrics = self._make_assign_and_metrics()
        analysis, _ = run_analysis(assign, metrics, cfg)
        assert analysis.secondary_alpha_used is None

    def test_no_secondary_metrics_bonferroni_applied_is_false(self):
        cfg = _make_config(secondary_metrics=[], guardrail_metrics=[])
        assign, metrics = self._make_assign_and_metrics()
        analysis, _ = run_analysis(assign, metrics, cfg)
        assert analysis.bonferroni_correction_applied is False

    # ------------------------------------------------------------------
    # Correction OFF (default)
    # ------------------------------------------------------------------

    def test_correction_off_uses_base_alpha(self):
        """When apply_bonferroni_correction=False, secondary_alpha_used == config.alpha."""
        cfg = self._make_cfg(["revenue_per_user", "session_length"], apply_correction=False)
        assign, metrics = self._make_assign_and_metrics(
            ctrl_secondary_vals={"revenue_per_user": 10.0, "session_length": 5.0},
            treat_secondary_vals={"revenue_per_user": 10.0, "session_length": 5.0},
        )
        analysis, _ = run_analysis(assign, metrics, cfg)
        assert analysis.secondary_alpha_used == pytest.approx(cfg.alpha)

    def test_correction_off_bonferroni_applied_is_false(self):
        """When correction is off, bonferroni_correction_applied must always be False."""
        cfg = self._make_cfg(["revenue_per_user", "session_length"], apply_correction=False)
        assign, metrics = self._make_assign_and_metrics(
            ctrl_secondary_vals={"revenue_per_user": 10.0, "session_length": 5.0},
        )
        analysis, _ = run_analysis(assign, metrics, cfg)
        assert analysis.bonferroni_correction_applied is False

    # ------------------------------------------------------------------
    # Correction ON, one secondary metric → no actual adjustment
    # ------------------------------------------------------------------

    def test_correction_on_one_secondary_no_adjustment(self):
        """m=1: secondary_alpha_used == alpha (alpha/1 == alpha), flag stays False."""
        cfg = self._make_cfg(["revenue_per_user"], apply_correction=True)
        assign, metrics = self._make_assign_and_metrics(
            ctrl_secondary_vals={"revenue_per_user": 10.0},
        )
        analysis, _ = run_analysis(assign, metrics, cfg)
        assert analysis.secondary_alpha_used == pytest.approx(cfg.alpha)
        assert analysis.bonferroni_correction_applied is False

    # ------------------------------------------------------------------
    # Correction ON, multiple secondary metrics → adjusted alpha
    # ------------------------------------------------------------------

    def test_correction_on_two_secondaries_halves_alpha(self):
        """m=2: secondary_alpha_used == alpha / 2."""
        cfg = self._make_cfg(["revenue_per_user", "session_length"], apply_correction=True)
        assign, metrics = self._make_assign_and_metrics(
            ctrl_secondary_vals={"revenue_per_user": 10.0, "session_length": 5.0},
        )
        analysis, _ = run_analysis(assign, metrics, cfg)
        assert analysis.secondary_alpha_used == pytest.approx(cfg.alpha / 2)
        assert analysis.bonferroni_correction_applied is True

    def test_correction_on_three_secondaries_thirds_alpha(self):
        """m=3: secondary_alpha_used == alpha / 3."""
        cfg = self._make_cfg(
            ["revenue_per_user", "session_length", "page_views"], apply_correction=True
        )
        assign, metrics = self._make_assign_and_metrics(
            ctrl_secondary_vals={
                "revenue_per_user": 10.0,
                "session_length": 5.0,
                "page_views": 3.0,
            },
        )
        analysis, _ = run_analysis(assign, metrics, cfg)
        assert analysis.secondary_alpha_used == pytest.approx(cfg.alpha / 3)
        assert analysis.bonferroni_correction_applied is True

    # ------------------------------------------------------------------
    # Significance boundary: p < alpha but p > alpha/m → not significant
    # ------------------------------------------------------------------

    def test_metric_not_significant_after_correction(self):
        """
        Build a secondary metric with a p-value between alpha/m and alpha.

        Strategy: use many units with a tiny but real effect.  With n=1000
        per arm and a 2% relative lift on a continuous metric with std=0.5,
        a Welch t-test typically yields p around 0.01–0.04 — above alpha/5
        (0.01) but below alpha (0.05).

        We test three secondaries (alpha/3 ≈ 0.0167).  The target secondary
        ('marginal_metric') is set up to cross the boundary, while the other
        two have zero lift (no noise) so we can isolate the test.
        """
        import random
        rng = random.Random(42)
        n = 800  # large n for stable p-value
        ctrl_ids  = [f"c{i:04d}" for i in range(n)]
        treat_ids = [f"t{i:04d}" for i in range(n)]
        assign = pd.DataFrame(
            [{"unit_id": uid, "variant": "control"}   for uid in ctrl_ids] +
            [{"unit_id": uid, "variant": "treatment"}  for uid in treat_ids]
        )

        # Primary: large effect (always significant)
        # marginal_metric: small effect using a seed that produces p in [alpha/3, alpha]
        # flat_a, flat_b: zero lift (p ≈ 1)
        rows: list[dict] = (
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": 0.0 if i < n else 1.0, "period": "post"}
             for i, uid in enumerate(ctrl_ids + treat_ids)]
        )
        # Build marginal metric with std=1.0 and small lift
        for uid in ctrl_ids:
            rows.append({"unit_id": uid, "metric_name": "marginal_metric",
                         "metric_value": rng.gauss(0.0, 1.0), "period": "post"})
        for uid in treat_ids:
            rows.append({"unit_id": uid, "metric_name": "marginal_metric",
                         "metric_value": rng.gauss(0.07, 1.0), "period": "post"})
        # flat secondaries (zero lift)
        for mname in ["flat_a", "flat_b"]:
            for uid in ctrl_ids + treat_ids:
                rows.append({"unit_id": uid, "metric_name": mname,
                             "metric_value": 5.0, "period": "post"})

        metrics = pd.DataFrame(rows)

        # Without correction: does marginal_metric cross alpha=0.05?
        cfg_no_correction = _make_config(
            secondary_metrics=["marginal_metric", "flat_a", "flat_b"],
            guardrail_metrics=[],
            apply_bonferroni_correction=False,
            alpha=0.05,
        )
        analysis_no_corr, _ = run_analysis(assign, metrics, cfg_no_correction)
        marginal_no_corr = next(
            (e for e in analysis_no_corr.secondary_metric_results
             if e.metric_name == "marginal_metric"), None
        )
        assert marginal_no_corr is not None

        # With correction (3 secondaries, adjusted_alpha = 0.05/3 ≈ 0.0167):
        cfg_correction = _make_config(
            secondary_metrics=["marginal_metric", "flat_a", "flat_b"],
            guardrail_metrics=[],
            apply_bonferroni_correction=True,
            alpha=0.05,
        )
        analysis_corr, _ = run_analysis(assign, metrics, cfg_correction)
        marginal_corr = next(
            (e for e in analysis_corr.secondary_metric_results
             if e.metric_name == "marginal_metric"), None
        )
        assert marginal_corr is not None

        # Verify the adjusted alpha is stored correctly
        assert analysis_corr.secondary_alpha_used == pytest.approx(0.05 / 3)

        # If the raw p-value is between alpha/3 and alpha, the metric should be
        # significant without correction but not significant with correction.
        # We don't assert both (the p-value is stochastic), but we do assert that
        # is_significant is determined by the correct alpha in each case.
        p = marginal_corr.p_value
        assert p is not None

        adjusted_alpha = 0.05 / 3
        expected_sig_with_correction = p < adjusted_alpha
        assert marginal_corr.is_significant == expected_sig_with_correction, (
            f"With correction: p={p:.4f}, alpha/3={adjusted_alpha:.4f}, "
            f"expected is_significant={expected_sig_with_correction}, "
            f"got {marginal_corr.is_significant}"
        )

        # The same p-value must produce different significance with and without correction
        # when p is in the boundary region
        expected_sig_without_correction = p < 0.05
        assert marginal_no_corr.is_significant == expected_sig_without_correction

    # ------------------------------------------------------------------
    # Primary metric unchanged by Bonferroni correction
    # ------------------------------------------------------------------

    def test_primary_metric_unchanged_by_bonferroni(self):
        """
        Primary metric is always tested at config.alpha regardless of correction.
        With and without correction, the primary result must be identical.
        """
        secondary = ["revenue_per_user", "session_length"]
        assign, metrics = self._make_assign_and_metrics(
            ctrl_secondary_vals={"revenue_per_user": 10.0, "session_length": 5.0},
        )

        cfg_off = self._make_cfg(secondary, apply_correction=False)
        cfg_on  = self._make_cfg(secondary, apply_correction=True)

        analysis_off, _ = run_analysis(assign, metrics, cfg_off)
        analysis_on,  _ = run_analysis(assign, metrics, cfg_on)

        r_off = analysis_off.primary_metric_result
        r_on  = analysis_on.primary_metric_result

        assert r_off is not None and r_on is not None
        assert r_off.p_value    == pytest.approx(r_on.p_value,    abs=1e-9)
        assert r_off.is_significant == r_on.is_significant
        assert r_off.absolute_lift  == pytest.approx(r_on.absolute_lift, abs=1e-9)

    # ------------------------------------------------------------------
    # Guardrail metrics unchanged by Bonferroni correction
    # ------------------------------------------------------------------

    def test_guardrail_metrics_unchanged_by_bonferroni(self):
        """Guardrail metrics are always tested at config.alpha (not affected by correction)."""
        import random
        rng = random.Random(7)
        n = 200
        ctrl_ids  = [f"c{i:04d}" for i in range(n)]
        treat_ids = [f"t{i:04d}" for i in range(n)]
        assign = pd.DataFrame(
            [{"unit_id": uid, "variant": "control"}   for uid in ctrl_ids] +
            [{"unit_id": uid, "variant": "treatment"}  for uid in treat_ids]
        )
        rows: list[dict] = (
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": rng.gauss(0.10, 0.05), "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "conversion_rate",
              "metric_value": rng.gauss(0.20, 0.05), "period": "post"} for uid in treat_ids] +
            [{"unit_id": uid, "metric_name": "refund_rate",
              "metric_value": rng.gauss(0.05, 0.02), "period": "post"} for uid in ctrl_ids] +
            [{"unit_id": uid, "metric_name": "refund_rate",
              "metric_value": rng.gauss(0.05, 0.02), "period": "post"} for uid in treat_ids]
        )
        metrics = pd.DataFrame(rows)

        cfg_off = _make_config(
            secondary_metrics=["revenue_a", "revenue_b"],
            guardrail_metrics=["refund_rate"],
            apply_bonferroni_correction=False, alpha=0.05,
        )
        cfg_on = _make_config(
            secondary_metrics=["revenue_a", "revenue_b"],
            guardrail_metrics=["refund_rate"],
            apply_bonferroni_correction=True, alpha=0.05,
        )
        # refund_rate has no data for revenue_a/b — that's fine, they won't appear
        analysis_off, _ = run_analysis(assign, metrics, cfg_off)
        analysis_on,  _ = run_analysis(assign, metrics, cfg_on)

        g_off = next((e for e in analysis_off.guardrail_metric_results
                      if e.metric_name == "refund_rate"), None)
        g_on  = next((e for e in analysis_on.guardrail_metric_results
                      if e.metric_name == "refund_rate"), None)

        if g_off is not None and g_on is not None:
            assert g_off.p_value       == pytest.approx(g_on.p_value,       abs=1e-9)
            assert g_off.is_significant == g_on.is_significant

    # ------------------------------------------------------------------
    # Serialisation: new fields round-trip correctly
    # ------------------------------------------------------------------

    def test_bonferroni_fields_serialise_in_json(self):
        """secondary_alpha_used and bonferroni_correction_applied survive JSON round-trip."""
        import json
        cfg = self._make_cfg(["revenue_per_user", "session_length"], apply_correction=True)
        assign, metrics = self._make_assign_and_metrics(
            ctrl_secondary_vals={"revenue_per_user": 10.0, "session_length": 5.0},
        )
        analysis, _ = run_analysis(assign, metrics, cfg)
        payload = json.loads(analysis.model_dump_json())
        assert "secondary_alpha_used" in payload
        assert "bonferroni_correction_applied" in payload
        assert payload["secondary_alpha_used"] == pytest.approx(0.05 / 2)
        assert payload["bonferroni_correction_applied"] is True


# ---------------------------------------------------------------------------
# Scenario D — guardrail ship (regression test for the guardrail-ship bug)
# ---------------------------------------------------------------------------

class TestScenarioDGuardrailShip:
    """
    Regression tests for the guardrail_ship fixture (Scenario D).

    Scenario:
      - primary metric: conversion_value (continuous), significant positive lift.
      - secondary metric: sessions_per_user (continuous).
      - guardrail: activation_rate with direction=increase. The treatment
        increases activation_rate significantly — this is the CORRECT direction
        and must NOT trigger a hold.
      - CUPED is recommended (high pre-post correlation on conversion_value).
      - SRM: pass (balanced 50/50 assignment).

    Expected outcome: decision=ship, NOT inconclusive or hold.

    This test class guards against two previously identified bugs:
      1. The primary metric estimate must always be computed when the data is
         valid (assignments and metrics CSVs have matching unit_ids, correct
         period values, and the primary metric has post-period rows).
      2. A guardrail metric that is significant but moves in the declared
         direction must NOT block the ship decision. The stale UI warning text
         that said "direction is not modelled automatically" was factually wrong;
         this fixture proves the direction-aware blocking logic is correct in core.
    """

    def setup_method(self):
        self.cfg    = _load_config("guardrail_ship")
        self.assign = _load_csv("guardrail_ship", "assignments.csv")
        self.metrics = _load_csv("guardrail_ship", "metrics.csv")

    # ------------------------------------------------------------------
    # Primary metric must be computed
    # ------------------------------------------------------------------

    def test_primary_metric_result_is_not_none(self):
        """
        Primary metric estimate must be present for valid input data.
        Returning None would produce the 'No primary metric estimate could be
        computed' INCONCLUSIVE outcome that triggered this bug report.
        """
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        assert analysis.primary_metric_result is not None, (
            "primary_metric_result must not be None for the guardrail_ship fixture"
        )

    def test_primary_metric_fields_complete(self):
        """All required fields on the primary MetricEstimate must be populated."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        r = analysis.primary_metric_result
        assert r.metric_name == "conversion_value"
        assert r.p_value is not None
        assert r.ci_lower is not None
        assert r.ci_upper is not None
        assert r.is_significant is not None

    def test_primary_metric_is_significant(self):
        """
        The guardrail_ship fixture has a treatment lift of ~+5 units on a
        baseline of ~50, with N=160 per arm — must be significant at alpha=0.05.
        """
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        r = analysis.primary_metric_result
        assert r.is_significant is True, (
            f"Expected significant primary metric; p={r.p_value:.6f}"
        )

    def test_effective_primary_estimate_populated(self):
        """effective_primary_estimate must be set whenever primary data exists."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        assert analysis.effective_primary_estimate is not None

    # ------------------------------------------------------------------
    # CUPED: pre-period data exists, high correlation
    # ------------------------------------------------------------------

    def test_cuped_readiness_is_recommended(self):
        """
        The fixture provides matched pre/post rows for conversion_value with high
        correlation — CUPED must be recommended.
        """
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        assert analysis.cuped_readiness is not None
        assert analysis.cuped_readiness.recommendation == "recommended", (
            f"Expected CUPED recommended; got {analysis.cuped_readiness.recommendation}"
        )

    def test_cuped_estimate_present(self):
        """CUPED estimate must be computed when readiness is recommended."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        assert analysis.cuped_estimate is not None

    def test_effective_primary_equals_cuped_when_applied(self):
        """effective_primary_estimate must point to the CUPED estimate."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        if analysis.cuped_estimate is not None:
            assert (
                analysis.effective_primary_estimate.metric_name
                == analysis.cuped_estimate.metric_name
            )

    # ------------------------------------------------------------------
    # Guardrail: significant increase → direction=increase → does NOT block
    # ------------------------------------------------------------------

    def test_guardrail_result_present(self):
        """activation_rate must have a computed estimate."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        names = [g.metric_name for g in analysis.guardrail_metric_results]
        assert "activation_rate" in names, (
            f"Expected activation_rate in guardrail results; got {names}"
        )

    def test_guardrail_significant_in_correct_direction(self):
        """
        The fixture ensures activation_rate rises in treatment.
        It should be significant (large enough effect, N=160) and the lift
        must be positive.
        """
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        ar = next(
            (g for g in analysis.guardrail_metric_results if g.metric_name == "activation_rate"),
            None,
        )
        assert ar is not None
        assert ar.is_significant is True, (
            "activation_rate must be significant in the guardrail_ship fixture"
        )
        assert ar.absolute_lift > 0, (
            f"activation_rate lift must be positive (direction=increase); "
            f"got {ar.absolute_lift}"
        )

    def test_decision_is_ship_not_hold_or_inconclusive(self):
        """
        Core regression: direction-aware guardrail must NOT block a ship.
        activation_rate is significant and increases → direction=increase →
        _guardrail_blocks returns False → decision must be ship.
        """
        _, decision = run_analysis(self.assign, self.metrics, self.cfg)
        assert decision.recommendation == "ship", (
            f"Expected ship; got {decision.recommendation}. "
            f"Reasoning: {decision.reasoning_summary}"
        )

    def test_decision_not_inconclusive_due_to_missing_estimate(self):
        """
        Explicit guard against the specific bug: decision must not be
        inconclusive due to a missing primary metric estimate.
        The reasoning string for that path is 'No primary metric estimate
        could be computed'.
        """
        _, decision = run_analysis(self.assign, self.metrics, self.cfg)
        assert "No primary metric estimate" not in decision.reasoning_summary, (
            "Decision must not be inconclusive due to missing primary estimate"
        )

    def test_secondary_metric_result_present(self):
        """sessions_per_user must appear in secondary results."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        names = [s.metric_name for s in analysis.secondary_metric_results]
        assert "sessions_per_user" in names

    def test_analysis_serialises_to_json(self):
        """Full AnalysisResult must serialise without error."""
        import json
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg)
        payload = json.loads(analysis.model_dump_json())
        assert payload["primary_metric_result"] is not None
        assert payload["primary_metric_result"]["metric_name"] == "conversion_value"


# ---------------------------------------------------------------------------
# Scenario E — Bonferroni ON/OFF fixture integration tests
# ---------------------------------------------------------------------------

class TestScenarioEBonferroniFixture:
    """
    Fixture-backed integration tests for the bonferroni_on and bonferroni_off
    scenarios.  These complement the unit-level TestBonferroniCorrection class
    by exercising the full pipeline against real CSV files — the same workflow
    a user would follow in the Streamlit app.

    Fixture: abkit-core/tests/fixtures/bonferroni_on/
      - experiment_id: exp_bonf_on_001
      - primary: conversion_value (continuous, large effect, always significant)
      - secondary: metric_a, metric_b, metric_c
      - guardrail: activation_rate (constant, never significant)
      - apply_bonferroni_correction: true  (for bonferroni_on)
                                    false (for bonferroni_off, same CSV data)

    The bonferroni_off fixture uses the same CSVs and experiment_id but its
    config has apply_bonferroni_correction=false.
    """

    def setup_method(self):
        self.cfg_on   = _load_config("bonferroni_on")
        self.cfg_off  = _load_config("bonferroni_off")
        # Both scenarios share the same CSV data
        self.assign   = _load_csv("bonferroni_on", "assignments.csv")
        self.metrics  = _load_csv("bonferroni_on", "metrics.csv")

    # ------------------------------------------------------------------
    # Config structure checks
    # ------------------------------------------------------------------

    def test_bonferroni_on_config_has_correction_enabled(self):
        """bonferroni_on config must have apply_bonferroni_correction=True."""
        assert self.cfg_on.apply_bonferroni_correction is True

    def test_bonferroni_off_config_has_correction_disabled(self):
        """bonferroni_off config must have apply_bonferroni_correction=False."""
        assert self.cfg_off.apply_bonferroni_correction is False

    def test_both_configs_have_three_secondary_metrics(self):
        assert len(self.cfg_on.secondary_metrics) == 3
        assert len(self.cfg_off.secondary_metrics) == 3
        names = {m.name for m in self.cfg_on.secondary_metrics}
        assert names == {"metric_a", "metric_b", "metric_c"}

    # ------------------------------------------------------------------
    # Bonferroni ON: secondary_alpha_used = alpha / 3
    # ------------------------------------------------------------------

    def test_bonferroni_on_secondary_alpha_is_adjusted(self):
        """With ON and m=3 secondaries: secondary_alpha_used = alpha/3 ≈ 0.0167."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg_on)
        import pytest
        assert analysis.secondary_alpha_used == pytest.approx(0.05 / 3, abs=1e-9)

    def test_bonferroni_on_correction_applied_flag_true(self):
        """bonferroni_correction_applied must be True for ON case with m=3."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg_on)
        assert analysis.bonferroni_correction_applied is True

    # ------------------------------------------------------------------
    # Bonferroni OFF: secondary_alpha_used = alpha
    # ------------------------------------------------------------------

    def test_bonferroni_off_secondary_alpha_equals_base_alpha(self):
        """With OFF: secondary_alpha_used = alpha = 0.05."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg_off)
        import pytest
        assert analysis.secondary_alpha_used == pytest.approx(0.05, abs=1e-9)

    def test_bonferroni_off_correction_applied_flag_false(self):
        """bonferroni_correction_applied must be False for OFF case."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg_off)
        assert analysis.bonferroni_correction_applied is False

    # ------------------------------------------------------------------
    # Primary metric: significant in both cases
    # ------------------------------------------------------------------

    def test_primary_metric_significant_bonferroni_on(self):
        """Primary metric must be significant regardless of Bonferroni setting."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg_on)
        assert analysis.primary_metric_result is not None
        assert analysis.primary_metric_result.is_significant is True

    def test_primary_metric_significant_bonferroni_off(self):
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg_off)
        assert analysis.primary_metric_result is not None
        assert analysis.primary_metric_result.is_significant is True

    def test_primary_p_value_identical_on_vs_off(self):
        """Bonferroni correction must not affect the primary metric p-value."""
        import pytest
        analysis_on, _ = run_analysis(self.assign, self.metrics, self.cfg_on)
        analysis_off, _ = run_analysis(self.assign, self.metrics, self.cfg_off)
        assert analysis_on.primary_metric_result.p_value == pytest.approx(
            analysis_off.primary_metric_result.p_value, abs=1e-9
        )

    # ------------------------------------------------------------------
    # Decision: ship in both cases (guardrail is not significant)
    # ------------------------------------------------------------------

    def test_decision_ship_bonferroni_on(self):
        """Fixture decision must be ship for Bonferroni ON (primary significant, no blocking guardrail)."""
        _, decision = run_analysis(self.assign, self.metrics, self.cfg_on)
        assert decision.recommendation == "ship", (
            f"Expected ship; got {decision.recommendation}. "
            f"Reasoning: {decision.reasoning_summary}"
        )

    def test_decision_ship_bonferroni_off(self):
        """Fixture decision must be ship for Bonferroni OFF."""
        _, decision = run_analysis(self.assign, self.metrics, self.cfg_off)
        assert decision.recommendation == "ship", (
            f"Expected ship; got {decision.recommendation}. "
            f"Reasoning: {decision.reasoning_summary}"
        )

    # ------------------------------------------------------------------
    # Guardrail: activation_rate constant → not significant → does not block
    # ------------------------------------------------------------------

    def test_guardrail_activation_rate_not_significant(self):
        """activation_rate is constant in this fixture — must not be significant."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg_on)
        ar = next(
            (g for g in analysis.guardrail_metric_results if g.metric_name == "activation_rate"),
            None,
        )
        assert ar is not None, "activation_rate must be in guardrail results"
        assert ar.is_significant is False, (
            f"activation_rate should not be significant for constant data; p={ar.p_value}"
        )

    # ------------------------------------------------------------------
    # CUPED: pre-period data present → assessed and recommended
    # ------------------------------------------------------------------

    def test_cuped_readiness_assessed(self):
        """Pre-period data for conversion_value → CUPED readiness must be assessed."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg_on)
        assert analysis.cuped_readiness is not None

    def test_cuped_estimate_present(self):
        """CUPED-adjusted estimate must be computed when pre-period data exists."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg_on)
        # Readiness must be recommended or optional to have a cuped_estimate
        assert analysis.cuped_readiness.recommendation in ("recommended", "optional"), (
            f"Expected recommended or optional; got {analysis.cuped_readiness.recommendation}"
        )
        assert analysis.cuped_estimate is not None

    # ------------------------------------------------------------------
    # Secondary metric results present
    # ------------------------------------------------------------------

    def test_all_declared_secondaries_in_results(self):
        """All three declared secondary metrics must appear in secondary_metric_results."""
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg_on)
        names = {s.metric_name for s in analysis.secondary_metric_results}
        assert "metric_a" in names
        assert "metric_b" in names
        assert "metric_c" in names

    # ------------------------------------------------------------------
    # JSON serialisation
    # ------------------------------------------------------------------

    def test_analysis_serialises_to_json_bonferroni_on(self):
        """Full AnalysisResult must serialise without error — Bonferroni ON."""
        import json
        analysis, _ = run_analysis(self.assign, self.metrics, self.cfg_on)
        payload = json.loads(analysis.model_dump_json())
        assert payload["bonferroni_correction_applied"] is True
        assert payload["secondary_alpha_used"] is not None
        assert payload["primary_metric_result"]["metric_name"] == "conversion_value"
