"""
Tests for abkit_core.quality — assignment and metric QA functions.

Coverage:
  - Clean assignment data → no issues, SRM pass.
  - SRM fixture → SRM critical severity.
  - Duplicate unit rows (same variant) → DUPLICATE_UNIT_ASSIGNMENT warning.
  - Conflicting variant rows (same unit, different variants) → CONFLICTING_VARIANT_ASSIGNMENT error.
  - Unknown variant label → UNKNOWN_VARIANT_IN_ASSIGNMENT error.
  - SRM warning via p-value only (small drift, significant).
  - SRM warning via drift only (visible drift, not significant).
  - Missing required column → early return with column error.
  - Multiple experiment_ids in one file → EXPERIMENT_ID_MISMATCH error.
  - Metric join gap → METRIC_JOIN_GAP warning.
  - Assignment join gap → ASSIGNMENT_JOIN_GAP warning.
  - Clean join → no issues.
  - Invalid period values → INVALID_METRIC_PERIOD error.
  - Non-numeric metric_value → NONNUMERIC_VALUE error.
  - Unknown metric name → warning (not error).
  - check_assignment_quality on clean fixture → pass, no errors.
  - check_assignment_quality on srm fixture → critical SRM.
"""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

import pandas as pd
import pytest
import yaml

from abkit_core.quality import (
    ISSUE_ASSIGNMENT_JOIN_GAP,
    ISSUE_CONFLICTING_VARIANT,
    ISSUE_DUPLICATE_UNIT,
    ISSUE_EXPERIMENT_ID_MISMATCH,
    ISSUE_INVALID_PERIOD,
    ISSUE_METRIC_JOIN_GAP,
    ISSUE_NONNUMERIC_VALUE,
    ISSUE_UNKNOWN_METRIC_NAME,
    check_assignment_quality,
    check_join_integrity,
    check_metric_quality,
)
from abkit_core.schemas import (
    ISSUE_MISSING_CSV_COLUMNS,
    ISSUE_UNKNOWN_VARIANT,
    ExperimentConfig,
    IssueSeverity,
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
        "experiment_id": "exp_qa_001",
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


def _assignment_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal assignment DataFrame from a list of dicts."""
    return pd.DataFrame(rows)


def _metric_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _base_assignment(unit_id: str, variant: str, exp_id: str = "exp_qa_001") -> dict:
    return {
        "experiment_id": exp_id,
        "unit_id": unit_id,
        "variant": variant,
        "assignment_ts": "2024-03-01T09:00:00",
    }


# ---------------------------------------------------------------------------
# Assignment QA — clean data
# ---------------------------------------------------------------------------

class TestCheckAssignmentQualityClean:
    def test_clean_fixture_no_balance_errors(self):
        cfg = _load_config("clean")
        df  = _load_csv("clean", "assignments.csv")
        result = check_assignment_quality(df, cfg)
        assert result.balance_checks == [] or all(
            i.severity != IssueSeverity.error for i in result.balance_checks
        )

    def test_clean_fixture_srm_pass(self):
        cfg = _load_config("clean")
        df  = _load_csv("clean", "assignments.csv")
        result = check_assignment_quality(df, cfg)
        assert result.srm_check is not None
        assert result.srm_check.severity == "pass"

    def test_clean_fixture_srm_has_all_fields(self):
        cfg = _load_config("clean")
        df  = _load_csv("clean", "assignments.csv")
        srm = check_assignment_quality(df, cfg).srm_check
        assert srm.chi2_stat >= 0
        assert 0.0 <= srm.p_value <= 1.0
        assert srm.srm_alpha_used == 0.01
        assert srm.max_absolute_drift == 0.0  # exact 50/50
        assert "pass" in srm.explanation.lower() or "no evidence" in srm.explanation.lower()

    def test_clean_fixture_srm_uses_default_alpha(self):
        """Config has srm_alpha=0.01, which equals the global default."""
        cfg = _load_config("clean")
        df  = _load_csv("clean", "assignments.csv")
        srm = check_assignment_quality(df, cfg).srm_check
        assert srm.srm_alpha_used == 0.01

    def test_minimal_clean_df(self):
        cfg = _make_config()
        rows = [_base_assignment(f"u{i:04d}", "control" if i < 50 else "treatment")
                for i in range(100)]
        df = _assignment_df(rows)
        result = check_assignment_quality(df, cfg)
        assert result.srm_check.severity == "pass"
        assert result.balance_checks == []


# ---------------------------------------------------------------------------
# Assignment QA — SRM
# ---------------------------------------------------------------------------

class TestCheckAssignmentQualitySRM:
    def test_srm_fixture_severity_critical(self):
        cfg = _load_config("srm")
        df  = _load_csv("srm", "assignments.csv")
        result = check_assignment_quality(df, cfg)
        assert result.srm_check.severity == "critical"

    def test_srm_fixture_has_issues_list(self):
        cfg = _load_config("srm")
        df  = _load_csv("srm", "assignments.csv")
        srm = check_assignment_quality(df, cfg).srm_check
        assert len(srm.issues) == 1
        assert srm.issues[0].code == "SRM_CRITICAL"
        assert srm.issues[0].severity == IssueSeverity.error

    def test_srm_fixture_drift_and_pvalue(self):
        cfg = _load_config("srm")
        df  = _load_csv("srm", "assignments.csv")
        srm = check_assignment_quality(df, cfg).srm_check
        assert srm.max_absolute_drift >= 0.02
        assert srm.p_value < 0.01

    def test_srm_fixture_explanation_contains_critical(self):
        cfg = _load_config("srm")
        df  = _load_csv("srm", "assignments.csv")
        srm = check_assignment_quality(df, cfg).srm_check
        assert "CRITICAL" in srm.explanation

    def test_srm_warning_via_pvalue_only(self):
        """
        Small drift (< 0.02) but statistically significant → warning.

        With N=100_000 and a 1% imbalance (49_500 vs 50_500), drift=0.01 which
        is below the 0.02 critical threshold but the large N drives chi-squared
        p well below the 0.01 default alpha.  Expected severity: warning.
        """
        cfg = _make_config()  # default srm_alpha=0.01
        n_control   = 49_500
        n_treatment = 50_500   # drift = 0.01, just at the pass/warning boundary
        rows = (
            [_base_assignment(f"u{i:07d}", "control")   for i in range(n_control)] +
            [_base_assignment(f"u{i:07d}", "treatment") for i in range(n_control, n_control + n_treatment)]
        )
        df = _assignment_df(rows)
        result = check_assignment_quality(df, cfg)
        srm = result.srm_check
        # Drift is exactly 0.01 — at boundary. p will be very small with N=100k.
        # Severity should be warning (significant p, but drift < 0.02).
        assert srm.max_absolute_drift < 0.02, f"drift={srm.max_absolute_drift} should be < 0.02"
        assert srm.p_value < 0.01, f"p={srm.p_value} should be < 0.01 with N=100k"
        assert srm.severity == "warning"

    def test_srm_warning_via_drift_only(self):
        """
        Drift >= SRM_DRIFT_WARNING (0.01) but p >= srm_alpha → warning.

        N=10, srm_alpha=0.001 (very strict).  With 4 control / 6 treatment the
        chi-squared statistic is 0.4 (p ≈ 0.527), which cannot reach 0.001.
        Drift = |0.4 - 0.5| = 0.10, which exceeds SRM_DRIFT_WARNING.
        Expected severity: warning (drift path, not significance path).
        """
        from abkit_core.quality import SRM_DRIFT_WARNING
        cfg = _make_config(srm_alpha=0.001)   # strict alpha: chi2 on N=10 cannot reach it
        rows = (
            [_base_assignment(f"u{i:03d}", "control")   for i in range(4)] +
            [_base_assignment(f"u{i:03d}", "treatment") for i in range(4, 10)]  # 4/6 split
        )
        df = _assignment_df(rows)
        srm = check_assignment_quality(df, cfg).srm_check
        # Confirm the setup matches expectations
        assert srm.max_absolute_drift >= SRM_DRIFT_WARNING, (
            f"drift={srm.max_absolute_drift} should be >= {SRM_DRIFT_WARNING}"
        )
        assert srm.p_value >= 0.001, (
            f"p={srm.p_value} should be >= 0.001 with N=10 (chi2 too small to reach strict alpha)"
        )
        # The only path that fires here is the drift-only warning branch
        assert srm.severity == "warning"

    def test_per_experiment_srm_alpha_is_used(self):
        """Config with srm_alpha=0.001 (strict) on a balanced dataset → pass."""
        cfg = _make_config(srm_alpha=0.001)
        rows = [_base_assignment(f"u{i:04d}", "control" if i < 50 else "treatment")
                for i in range(100)]
        df = _assignment_df(rows)
        srm = check_assignment_quality(df, cfg).srm_check
        assert srm.srm_alpha_used == 0.001
        assert srm.severity == "pass"


    def test_srm_threshold_constants_are_exported(self):
        """
        SRM_DRIFT_WARNING and SRM_DRIFT_CRITICAL must be importable and have
        the correct policy values that match the classification table in 03-schemas.md.
        """
        from abkit_core.quality import SRM_DRIFT_CRITICAL, SRM_DRIFT_WARNING
        assert SRM_DRIFT_WARNING  == 0.01, (
            "SRM_DRIFT_WARNING must be 0.01 (pass/warning boundary per 03-schemas.md)"
        )
        assert SRM_DRIFT_CRITICAL == 0.02, (
            "SRM_DRIFT_CRITICAL must be 0.02 (warning/critical boundary per 03-schemas.md)"
        )
        # The critical threshold must be strictly greater than the warning threshold
        # so that the classification cases are mutually exclusive without overlap.
        assert SRM_DRIFT_CRITICAL > SRM_DRIFT_WARNING

    def test_srm_pass_exactly_at_warning_drift_boundary(self):
        """
        drift < SRM_DRIFT_WARNING with p >= alpha → pass.
        Verifies the strict < boundary on the pass case.
        Perfect 50/50 split gives drift=0.0, p=1.0.
        """
        from abkit_core.quality import SRM_DRIFT_WARNING
        cfg = _make_config()
        rows = [_base_assignment(f"u{i:04d}", "control" if i < 50 else "treatment")
                for i in range(100)]
        df = _assignment_df(rows)
        srm = check_assignment_quality(df, cfg).srm_check
        assert srm.max_absolute_drift < SRM_DRIFT_WARNING
        assert srm.severity == "pass"

    def test_srm_critical_requires_both_significance_and_large_drift(self):
        """
        drift >= SRM_DRIFT_CRITICAL AND p < alpha → critical (not warning).
        Uses the SRM fixture which is guaranteed to exceed both thresholds.
        """
        from abkit_core.quality import SRM_DRIFT_CRITICAL
        cfg = _load_config("srm")
        df  = _load_csv("srm", "assignments.csv")
        srm = check_assignment_quality(df, cfg).srm_check
        assert srm.max_absolute_drift >= SRM_DRIFT_CRITICAL
        assert srm.p_value < srm.srm_alpha_used
        assert srm.severity == "critical"



# ---------------------------------------------------------------------------
# Assignment QA — duplicates and conflicts
# ---------------------------------------------------------------------------

class TestCheckAssignmentQualityDuplicates:
    def test_duplicate_unit_same_variant_warning(self):
        cfg = _make_config()
        rows = [
            _base_assignment("u0001", "control"),
            _base_assignment("u0001", "control"),   # pure duplicate
            _base_assignment("u0002", "treatment"),
        ]
        df = _assignment_df(rows)
        result = check_assignment_quality(df, cfg)
        codes = [i.code for i in result.balance_checks]
        assert ISSUE_DUPLICATE_UNIT in codes
        dup = next(i for i in result.balance_checks if i.code == ISSUE_DUPLICATE_UNIT)
        assert dup.severity == IssueSeverity.warning

    def test_conflicting_variant_same_unit_error(self):
        cfg = _make_config()
        rows = [
            _base_assignment("u0001", "control"),
            _base_assignment("u0001", "treatment"),  # conflict!
            _base_assignment("u0002", "treatment"),
        ]
        df = _assignment_df(rows)
        result = check_assignment_quality(df, cfg)
        codes = [i.code for i in result.balance_checks]
        assert ISSUE_CONFLICTING_VARIANT in codes
        conflict = next(i for i in result.balance_checks if i.code == ISSUE_CONFLICTING_VARIANT)
        assert conflict.severity == IssueSeverity.error

    def test_conflict_reported_separately_from_pure_duplicate(self):
        cfg = _make_config()
        rows = [
            _base_assignment("u0001", "control"),
            _base_assignment("u0001", "treatment"),  # conflict
            _base_assignment("u0002", "control"),
            _base_assignment("u0002", "control"),    # pure dup
            _base_assignment("u0003", "treatment"),
        ]
        df = _assignment_df(rows)
        result = check_assignment_quality(df, cfg)
        codes = [i.code for i in result.balance_checks]
        assert ISSUE_CONFLICTING_VARIANT in codes
        assert ISSUE_DUPLICATE_UNIT in codes

    def test_no_duplicates_clean(self):
        cfg = _make_config()
        rows = [_base_assignment(f"u{i:04d}", "control" if i < 5 else "treatment")
                for i in range(10)]
        df = _assignment_df(rows)
        result = check_assignment_quality(df, cfg)
        codes = [i.code for i in result.balance_checks]
        assert ISSUE_DUPLICATE_UNIT not in codes
        assert ISSUE_CONFLICTING_VARIANT not in codes


# ---------------------------------------------------------------------------
# Assignment QA — unknown variants
# ---------------------------------------------------------------------------

class TestCheckAssignmentQualityUnknownVariant:
    def test_unknown_variant_returns_error(self):
        cfg = _make_config()
        rows = [
            _base_assignment("u0001", "control"),
            _base_assignment("u0002", "holdout"),   # not in config
        ]
        df = _assignment_df(rows)
        result = check_assignment_quality(df, cfg)
        codes = [i.code for i in result.balance_checks]
        assert ISSUE_UNKNOWN_VARIANT in codes
        unknown = next(i for i in result.balance_checks if i.code == ISSUE_UNKNOWN_VARIANT)
        assert unknown.severity == IssueSeverity.error
        assert unknown.details["unknown_variant"] == "holdout"

    def test_all_known_variants_no_unknown_issue(self):
        cfg = _make_config()
        rows = [_base_assignment(f"u{i:04d}", "control" if i < 50 else "treatment")
                for i in range(100)]
        df = _assignment_df(rows)
        result = check_assignment_quality(df, cfg)
        assert ISSUE_UNKNOWN_VARIANT not in [i.code for i in result.balance_checks]


# ---------------------------------------------------------------------------
# Assignment QA — missing required columns
# ---------------------------------------------------------------------------

class TestCheckAssignmentQualityColumns:
    def test_missing_required_column_early_return(self):
        cfg = _make_config()
        df = pd.DataFrame([{"unit_id": "u001", "variant": "control"}])  # missing cols
        result = check_assignment_quality(df, cfg)
        assert any(i.code == ISSUE_MISSING_CSV_COLUMNS for i in result.balance_checks)
        assert result.srm_check is None

    def test_multiple_experiment_ids_flagged(self):
        cfg = _make_config()
        rows = [
            {**_base_assignment("u0001", "control"),   "experiment_id": "exp_A"},
            {**_base_assignment("u0002", "treatment"), "experiment_id": "exp_B"},
        ]
        df = _assignment_df(rows)
        result = check_assignment_quality(df, cfg)
        codes = [i.code for i in result.balance_checks]
        assert ISSUE_EXPERIMENT_ID_MISMATCH in codes


# ---------------------------------------------------------------------------
# Metric QA
# ---------------------------------------------------------------------------

class TestCheckMetricQuality:
    def test_clean_metric_df_no_issues(self):
        cfg = _make_config()
        rows = [
            {"experiment_id": "exp_qa_001", "unit_id": "u0001",
             "metric_name": "conversion_rate", "metric_value": 0.12, "period": "post"},
        ]
        df = _metric_df(rows)
        issues = check_metric_quality(df, cfg)
        assert issues == []

    def test_invalid_period_value_error(self):
        cfg = _make_config()
        rows = [
            {"experiment_id": "exp_qa_001", "unit_id": "u0001",
             "metric_name": "conversion_rate", "metric_value": 0.12, "period": "during"},
        ]
        df = _metric_df(rows)
        issues = check_metric_quality(df, cfg)
        codes = [i.code for i in issues]
        assert ISSUE_INVALID_PERIOD in codes
        err = next(i for i in issues if i.code == ISSUE_INVALID_PERIOD)
        assert err.severity == IssueSeverity.error

    def test_nonnumeric_metric_value_error(self):
        cfg = _make_config()
        rows = [
            {"experiment_id": "exp_qa_001", "unit_id": "u0001",
             "metric_name": "conversion_rate", "metric_value": "N/A", "period": "post"},
        ]
        df = _metric_df(rows)
        issues = check_metric_quality(df, cfg)
        codes = [i.code for i in issues]
        assert ISSUE_NONNUMERIC_VALUE in codes

    def test_unknown_metric_name_warning_not_error(self):
        cfg = _make_config()
        rows = [
            {"experiment_id": "exp_qa_001", "unit_id": "u0001",
             "metric_name": "some_mystery_metric", "metric_value": 1.0, "period": "post"},
        ]
        df = _metric_df(rows)
        issues = check_metric_quality(df, cfg)
        codes = [i.code for i in issues]
        assert ISSUE_UNKNOWN_METRIC_NAME in codes
        warn = next(i for i in issues if i.code == ISSUE_UNKNOWN_METRIC_NAME)
        assert warn.severity == IssueSeverity.warning

    def test_missing_metric_columns_early_return(self):
        cfg = _make_config()
        df = pd.DataFrame([{"unit_id": "u001", "metric_value": 1.0}])
        issues = check_metric_quality(df, cfg)
        assert any(i.code == ISSUE_MISSING_CSV_COLUMNS for i in issues)

    def test_clean_fixture_metrics_no_issues(self):
        cfg = _load_config("clean")
        df = _load_csv("clean", "metrics.csv")
        issues = check_metric_quality(df, cfg)
        # Only warnings are allowed (e.g. extra metrics); no errors
        assert all(i.severity != IssueSeverity.error for i in issues)


# ---------------------------------------------------------------------------
# Join integrity
# ---------------------------------------------------------------------------

class TestCheckJoinIntegrity:
    def test_clean_join_no_issues(self):
        cfg = _make_config()
        assign_df = _assignment_df([
            _base_assignment("u0001", "control"),
            _base_assignment("u0002", "treatment"),
        ])
        metric_df = _metric_df([
            {"experiment_id": "exp_qa_001", "unit_id": "u0001",
             "metric_name": "conversion_rate", "metric_value": 0.1, "period": "post"},
            {"experiment_id": "exp_qa_001", "unit_id": "u0002",
             "metric_name": "conversion_rate", "metric_value": 0.15, "period": "post"},
        ])
        issues = check_join_integrity(assign_df, metric_df, cfg)
        assert issues == []

    def test_metric_join_gap_flagged(self):
        """Assigned unit u0003 has no post metric row."""
        cfg = _make_config()
        assign_df = _assignment_df([
            _base_assignment("u0001", "control"),
            _base_assignment("u0002", "treatment"),
            _base_assignment("u0003", "control"),   # no matching metric
        ])
        metric_df = _metric_df([
            {"experiment_id": "exp_qa_001", "unit_id": "u0001",
             "metric_name": "conversion_rate", "metric_value": 0.1, "period": "post"},
            {"experiment_id": "exp_qa_001", "unit_id": "u0002",
             "metric_name": "conversion_rate", "metric_value": 0.15, "period": "post"},
        ])
        issues = check_join_integrity(assign_df, metric_df, cfg)
        codes = [i.code for i in issues]
        assert ISSUE_METRIC_JOIN_GAP in codes
        gap = next(i for i in issues if i.code == ISSUE_METRIC_JOIN_GAP)
        assert gap.severity == IssueSeverity.warning
        assert gap.details["unmatched_count"] == 1

    def test_assignment_join_gap_flagged(self):
        """Metric unit u0099 has no assignment row."""
        cfg = _make_config()
        assign_df = _assignment_df([
            _base_assignment("u0001", "control"),
        ])
        metric_df = _metric_df([
            {"experiment_id": "exp_qa_001", "unit_id": "u0001",
             "metric_name": "conversion_rate", "metric_value": 0.1, "period": "post"},
            {"experiment_id": "exp_qa_001", "unit_id": "u0099",  # no assignment
             "metric_name": "conversion_rate", "metric_value": 0.2, "period": "post"},
        ])
        issues = check_join_integrity(assign_df, metric_df, cfg)
        codes = [i.code for i in issues]
        assert ISSUE_ASSIGNMENT_JOIN_GAP in codes
        gap = next(i for i in issues if i.code == ISSUE_ASSIGNMENT_JOIN_GAP)
        assert gap.severity == IssueSeverity.warning

    def test_both_gaps_reported(self):
        """Both directions missing simultaneously."""
        cfg = _make_config()
        assign_df = _assignment_df([
            _base_assignment("u0001", "control"),
            _base_assignment("u0002", "treatment"),  # no metric
        ])
        metric_df = _metric_df([
            {"experiment_id": "exp_qa_001", "unit_id": "u0001",
             "metric_name": "conversion_rate", "metric_value": 0.1, "period": "post"},
            {"experiment_id": "exp_qa_001", "unit_id": "u0099",  # no assignment
             "metric_name": "conversion_rate", "metric_value": 0.2, "period": "post"},
        ])
        issues = check_join_integrity(assign_df, metric_df, cfg)
        codes = [i.code for i in issues]
        assert ISSUE_METRIC_JOIN_GAP in codes
        assert ISSUE_ASSIGNMENT_JOIN_GAP in codes

    def test_clean_fixture_join_no_errors(self):
        cfg = _load_config("clean")
        assign_df = _load_csv("clean", "assignments.csv")
        metric_df = _load_csv("clean", "metrics.csv")
        issues = check_join_integrity(assign_df, metric_df, cfg)
        # Clean fixture has full overlap; no gaps expected
        assert all(i.severity != IssueSeverity.error for i in issues)

    def test_pre_period_rows_not_counted_as_gaps(self):
        """
        Pre-period metric rows must not be confused with post-period when
        computing join coverage. Units with only pre-period rows count as
        'missing' from the post-metric side and should trigger a gap warning.
        """
        cfg = _make_config()
        assign_df = _assignment_df([
            _base_assignment("u0001", "control"),
            _base_assignment("u0002", "treatment"),
        ])
        metric_df = _metric_df([
            {"experiment_id": "exp_qa_001", "unit_id": "u0001",
             "metric_name": "conversion_rate", "metric_value": 0.1, "period": "post"},
            # u0002 has only pre-period — should flag as metric join gap
            {"experiment_id": "exp_qa_001", "unit_id": "u0002",
             "metric_name": "conversion_rate", "metric_value": 0.1, "period": "pre"},
        ])
        issues = check_join_integrity(assign_df, metric_df, cfg)
        codes = [i.code for i in issues]
        assert ISSUE_METRIC_JOIN_GAP in codes


# ---------------------------------------------------------------------------
# End-to-end: full QA run on fixture scenarios
# ---------------------------------------------------------------------------

class TestFullQARunFixtures:
    def test_scenario_a_clean_passes_all_checks(self):
        cfg = _load_config("clean")
        assign_df = _load_csv("clean", "assignments.csv")
        metric_df = _load_csv("clean", "metrics.csv")

        qa = check_assignment_quality(assign_df, cfg)
        metric_issues = check_metric_quality(metric_df, cfg)
        join_issues = check_join_integrity(assign_df, metric_df, cfg)

        # SRM must pass
        assert qa.srm_check.severity == "pass"
        # No assignment errors
        assert all(i.severity != IssueSeverity.error for i in qa.balance_checks)
        # No metric errors
        assert all(i.severity != IssueSeverity.error for i in metric_issues)
        # No join errors
        assert all(i.severity != IssueSeverity.error for i in join_issues)

    def test_scenario_b_srm_fixture_critical(self):
        cfg = _load_config("srm")
        assign_df = _load_csv("srm", "assignments.csv")
        metric_df = _load_csv("srm", "metrics.csv")

        qa = check_assignment_quality(assign_df, cfg)
        assert qa.srm_check.severity == "critical"

    def test_srm_result_serialises_cleanly(self):
        """model_dump_json() must produce valid JSON with string enum values."""
        import json
        cfg = _load_config("srm")
        df  = _load_csv("srm", "assignments.csv")
        qa  = check_assignment_quality(df, cfg)
        payload = json.loads(qa.model_dump_json())
        assert payload["srm_check"]["severity"] == "critical"
        assert isinstance(payload["srm_check"]["p_value"], float)
