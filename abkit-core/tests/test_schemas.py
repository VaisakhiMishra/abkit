"""
Tests for abkit_core.schemas — Pydantic models and validation helpers.

Coverage targets from 05-acceptance.md:
  - Valid config round-trips cleanly.
  - Missing required field raises a ValidationError.
  - Invalid allocation sum raises a ValidationError.
  - Duplicate variant names raise a ValidationError.
  - allocation keys not matching variants raises a ValidationError.
  - primary_metric in guardrail_metrics returns a business-rule error.
  - metric_type absence is valid for spec-only; returns MISSING_METRIC_TYPE for analysis.
  - Unknown variant label in assignment data returns UNKNOWN_VARIANT issue.
  - Missing required columns in assignment CSV returns MISSING_REQUIRED_CSV_COLUMNS.
  - Missing required columns in metric CSV returns MISSING_REQUIRED_CSV_COLUMNS.
  - Fixture YAML files parse successfully.
  - Fixture CSV files have the correct required columns.
  - DurationPlan ordering invariant is enforced.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from abkit_core.schemas import (
    ISSUE_MISSING_METRIC_TYPE,
    ISSUE_MISSING_CSV_COLUMNS,
    ISSUE_PRIMARY_IN_GUARDRAILS,
    ISSUE_UNKNOWN_VARIANT,
    AssignmentRow,
    CupedRecommendation,
    CupedReadiness,
    DiagnosticIssue,
    DurationPlan,
    DurationPlanAssumptions,
    ExperimentConfig,
    IssueSeverity,
    MetricEstimate,
    MetricRow,
    MetricSpec,
    MetricType,
    SrmCheck,
    SrmSeverity,
    validate_assignment_columns,
    validate_config,
    validate_metric_columns,
    validate_variant_labels,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"


def _minimal_config(**overrides) -> dict:
    """Return a valid minimal config dict, with optional field overrides."""
    base: dict = {
        "schema_version": "1.0",
        "experiment_name": "Test experiment",
        "experiment_id": "exp_test_001",
        "owner": "test-team",
        "hypothesis": "Treatment will improve the metric.",
        "primary_metric": "conversion_rate",
        "metric_type": "proportion",
        "variants": ["control", "treatment"],
        "expected_allocation": {"control": 0.5, "treatment": 0.5},
        "unit_of_randomization": "user_id",
        "alpha": 0.05,
        "power": 0.80,
        "mde": 0.02,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# ExperimentConfig — structural validation (Pydantic)
# ---------------------------------------------------------------------------

class TestExperimentConfigValid:
    def test_minimal_valid_config(self):
        """A complete minimal config parses without error."""
        cfg = ExperimentConfig(**_minimal_config())
        assert cfg.experiment_id == "exp_test_001"
        assert cfg.metric_type == MetricType.proportion
        assert cfg.variants == ["control", "treatment"]

    def test_optional_fields_default_to_empty(self):
        """Optional list fields default to empty lists, not None."""
        cfg = ExperimentConfig(**_minimal_config())
        assert cfg.secondary_metrics == []
        assert cfg.guardrail_metrics == []
        assert cfg.segments == []
        assert cfg.exclusion_rules == []

    def test_secondary_metrics_string_coercion(self):
        """Plain strings in secondary_metrics are coerced to MetricSpec objects."""
        cfg = ExperimentConfig(**_minimal_config(
            secondary_metrics=["revenue_per_user", "session_length"],
        ))
        assert len(cfg.secondary_metrics) == 2
        assert isinstance(cfg.secondary_metrics[0], MetricSpec)
        assert cfg.secondary_metrics[0].name == "revenue_per_user"
        assert cfg.secondary_metrics[0].metric_type == MetricType.continuous

    def test_secondary_metrics_dict_coercion(self):
        """Dict entries in secondary_metrics are coerced to MetricSpec objects."""
        cfg = ExperimentConfig(**_minimal_config(
            secondary_metrics=[{"name": "refund_rate", "metric_type": "proportion"}],
        ))
        assert cfg.secondary_metrics[0].name == "refund_rate"
        assert cfg.secondary_metrics[0].metric_type == MetricType.proportion

    def test_guardrail_metrics_string_coercion(self):
        """Plain strings in guardrail_metrics are coerced to MetricSpec with is_guardrail=False by default."""
        cfg = ExperimentConfig(**_minimal_config(
            guardrail_metrics=["refund_rate"],
        ))
        assert cfg.guardrail_metrics[0].name == "refund_rate"
        assert isinstance(cfg.guardrail_metrics[0], MetricSpec)

    def test_metric_type_continuous_accepted(self):
        cfg = ExperimentConfig(**_minimal_config(metric_type="continuous"))
        assert cfg.metric_type == MetricType.continuous

    def test_metric_type_none_accepted_for_spec_only(self):
        """metric_type may be omitted for spec-validation-only runs."""
        data = _minimal_config()
        del data["metric_type"]
        cfg = ExperimentConfig(**data)
        assert cfg.metric_type is None

    def test_srm_alpha_range_boundaries(self):
        """srm_alpha accepts boundary values 0.001 and 0.1."""
        cfg_low  = ExperimentConfig(**_minimal_config(srm_alpha=0.001))
        cfg_high = ExperimentConfig(**_minimal_config(srm_alpha=0.1))
        assert cfg_low.srm_alpha == 0.001
        assert cfg_high.srm_alpha == 0.1

    def test_three_variant_config(self):
        """Configs with three variants are valid."""
        cfg = ExperimentConfig(**_minimal_config(
            variants=["control", "treatment_a", "treatment_b"],
            expected_allocation={"control": 0.34, "treatment_a": 0.33, "treatment_b": 0.33},
        ))
        assert len(cfg.variants) == 3


# ---------------------------------------------------------------------------
# ExperimentConfig — structural failures (Pydantic ValidationError)
# ---------------------------------------------------------------------------

class TestExperimentConfigStructuralErrors:
    def test_missing_experiment_id(self):
        """experiment_id is required."""
        data = _minimal_config()
        del data["experiment_id"]
        with pytest.raises(ValidationError) as exc_info:
            ExperimentConfig(**data)
        assert "experiment_id" in str(exc_info.value)

    def test_missing_primary_metric(self):
        data = _minimal_config()
        del data["primary_metric"]
        with pytest.raises(ValidationError):
            ExperimentConfig(**data)

    def test_missing_hypothesis(self):
        data = _minimal_config()
        del data["hypothesis"]
        with pytest.raises(ValidationError):
            ExperimentConfig(**data)

    def test_invalid_allocation_sum_too_high(self):
        """Allocation that sums to 1.1 must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ExperimentConfig(**_minimal_config(
                expected_allocation={"control": 0.6, "treatment": 0.5},
            ))
        assert "sum" in str(exc_info.value).lower() or "1.0" in str(exc_info.value)

    def test_invalid_allocation_sum_too_low(self):
        """Allocation that sums to 0.8 must raise ValidationError."""
        with pytest.raises(ValidationError):
            ExperimentConfig(**_minimal_config(
                expected_allocation={"control": 0.4, "treatment": 0.4},
            ))

    def test_duplicate_variant_names(self):
        """Duplicate entries in variants must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ExperimentConfig(**_minimal_config(
                variants=["control", "control"],
                expected_allocation={"control": 1.0},
            ))
        assert "unique" in str(exc_info.value).lower()

    def test_allocation_keys_do_not_match_variants(self):
        """Allocation keys that differ from variants must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ExperimentConfig(**_minimal_config(
                variants=["control", "treatment"],
                expected_allocation={"control": 0.5, "variant_x": 0.5},
            ))
        assert "match" in str(exc_info.value).lower()

    def test_alpha_out_of_range_zero(self):
        """alpha=0 must raise (gt=0 constraint)."""
        with pytest.raises(ValidationError):
            ExperimentConfig(**_minimal_config(alpha=0.0))

    def test_alpha_out_of_range_one(self):
        """alpha=1.0 must raise (lt=1 constraint)."""
        with pytest.raises(ValidationError):
            ExperimentConfig(**_minimal_config(alpha=1.0))

    def test_srm_alpha_out_of_range_too_low(self):
        """srm_alpha below 0.001 must raise ValidationError."""
        with pytest.raises(ValidationError):
            ExperimentConfig(**_minimal_config(srm_alpha=0.0005))

    def test_srm_alpha_out_of_range_too_high(self):
        """srm_alpha above 0.1 must raise ValidationError."""
        with pytest.raises(ValidationError):
            ExperimentConfig(**_minimal_config(srm_alpha=0.11))

    def test_traffic_cap_zero_raises(self):
        """traffic_cap=0 is not a valid fraction (exclusive lower bound)."""
        with pytest.raises(ValidationError):
            ExperimentConfig(**_minimal_config(traffic_cap=0.0))

    def test_invalid_metric_type(self):
        """An unlisted metric_type string must raise ValidationError."""
        with pytest.raises(ValidationError):
            ExperimentConfig(**_minimal_config(metric_type="binary"))

    def test_fewer_than_two_variants_raises(self):
        """A single-variant experiment is structurally invalid."""
        with pytest.raises(ValidationError):
            ExperimentConfig(**_minimal_config(
                variants=["control"],
                expected_allocation={"control": 1.0},
            ))


# ---------------------------------------------------------------------------
# validate_config() — business-rule checks
# ---------------------------------------------------------------------------

class TestValidateConfig:
    def test_clean_config_returns_no_issues(self):
        cfg = ExperimentConfig(**_minimal_config())
        issues = validate_config(cfg)
        assert issues == []

    def test_primary_metric_in_guardrails_raises_at_construction(self):
        """primary_metric in guardrail_metrics is now a Pydantic model_validator error."""
        with pytest.raises(ValidationError) as exc_info:
            ExperimentConfig(**_minimal_config(
                guardrail_metrics=["conversion_rate"],  # same as primary_metric
            ))
        assert "guardrail" in str(exc_info.value).lower()

    def test_missing_metric_type_no_error_for_spec_only(self):
        """metric_type=None is fine at spec-validation time."""
        data = _minimal_config()
        del data["metric_type"]
        cfg = ExperimentConfig(**data)
        issues = validate_config(cfg, for_analysis=False)
        codes = [i.code for i in issues]
        assert ISSUE_MISSING_METRIC_TYPE not in codes

    def test_missing_metric_type_error_for_analysis(self):
        """metric_type=None raises MISSING_METRIC_TYPE when for_analysis=True."""
        data = _minimal_config()
        del data["metric_type"]
        cfg = ExperimentConfig(**data)
        issues = validate_config(cfg, for_analysis=True)
        codes = [i.code for i in issues]
        assert ISSUE_MISSING_METRIC_TYPE in codes
        match = next(i for i in issues if i.code == ISSUE_MISSING_METRIC_TYPE)
        assert match.severity == IssueSeverity.error
        assert match.field == "metric_type"

    def test_high_alpha_warning(self):
        """alpha >= 0.1 generates a warning, not an error."""
        cfg = ExperimentConfig(**_minimal_config(alpha=0.10))
        issues = validate_config(cfg)
        severities = [i.severity for i in issues]
        assert IssueSeverity.warning in severities
        assert IssueSeverity.error not in severities

    def test_low_power_warning(self):
        """power < 0.7 generates a warning."""
        cfg = ExperimentConfig(**_minimal_config(power=0.60))
        issues = validate_config(cfg)
        codes = [i.code for i in issues]
        from abkit_core.schemas import ISSUE_POWER_RANGE
        assert ISSUE_POWER_RANGE in codes


# ---------------------------------------------------------------------------
# Assignment CSV column validation
# ---------------------------------------------------------------------------

class TestValidateAssignmentColumns:
    def test_all_required_columns_present(self):
        cols = ["experiment_id", "unit_id", "variant", "assignment_ts"]
        assert validate_assignment_columns(cols) == []

    def test_extra_optional_columns_allowed(self):
        cols = ["experiment_id", "unit_id", "variant", "assignment_ts",
                "exposed", "country", "device_type"]
        assert validate_assignment_columns(cols) == []

    def test_missing_single_required_column(self):
        cols = ["experiment_id", "unit_id", "variant"]  # missing assignment_ts
        issues = validate_assignment_columns(cols)
        assert len(issues) == 1
        assert issues[0].code == ISSUE_MISSING_CSV_COLUMNS
        assert issues[0].severity == IssueSeverity.error
        assert "assignment_ts" in issues[0].details["missing_columns"]

    def test_missing_multiple_required_columns(self):
        cols = ["unit_id"]
        issues = validate_assignment_columns(cols)
        assert len(issues) == 1  # one issue listing all missing columns
        missing = issues[0].details["missing_columns"]
        assert "experiment_id" in missing
        assert "variant" in missing
        assert "assignment_ts" in missing

    def test_empty_column_list(self):
        issues = validate_assignment_columns([])
        assert len(issues) == 1
        assert len(issues[0].details["missing_columns"]) == 4


# ---------------------------------------------------------------------------
# Metric CSV column validation
# ---------------------------------------------------------------------------

class TestValidateMetricColumns:
    def test_all_required_columns_present(self):
        cols = ["experiment_id", "unit_id", "metric_name", "metric_value", "period"]
        assert validate_metric_columns(cols) == []

    def test_missing_period_column(self):
        cols = ["experiment_id", "unit_id", "metric_name", "metric_value"]
        issues = validate_metric_columns(cols)
        assert len(issues) == 1
        assert issues[0].code == ISSUE_MISSING_CSV_COLUMNS
        assert "period" in issues[0].details["missing_columns"]


# ---------------------------------------------------------------------------
# Variant label validation
# ---------------------------------------------------------------------------

class TestValidateVariantLabels:
    def test_all_known_variants(self):
        cfg = ExperimentConfig(**_minimal_config())
        issues = validate_variant_labels(["control", "treatment"], cfg)
        assert issues == []

    def test_unknown_variant_returns_error(self):
        cfg = ExperimentConfig(**_minimal_config())
        issues = validate_variant_labels(["control", "treatment", "holdout"], cfg)
        assert len(issues) == 1
        assert issues[0].code == ISSUE_UNKNOWN_VARIANT
        assert issues[0].severity == IssueSeverity.error
        assert issues[0].details["unknown_variant"] == "holdout"

    def test_multiple_unknown_variants(self):
        cfg = ExperimentConfig(**_minimal_config())
        issues = validate_variant_labels(["control", "ghost", "phantom"], cfg)
        unknown = {i.details["unknown_variant"] for i in issues}
        assert unknown == {"ghost", "phantom"}


# ---------------------------------------------------------------------------
# DurationPlan ordering invariant
# ---------------------------------------------------------------------------

class TestDurationPlanOrdering:
    def _make_plan(self, opt, planned, cons):
        return DurationPlan(
            required_n_per_variant=3842,
            required_n_total=7684,
            daily_eligible_traffic=5000,
            optimistic_days=opt,
            planned_days=planned,
            conservative_days=cons,
            assumptions=DurationPlanAssumptions(
                traffic_cap=1.0,
                ramp_up_days=0,
                planning_buffer_pct=0.0,
                eligibility_rate=None,
            ),
        )

    def test_valid_ordering(self):
        plan = self._make_plan(2, 4, 5)
        assert plan.optimistic_days == 2
        assert plan.conservative_days == 5

    def test_equal_values_allowed(self):
        """Exact equality is fine: optimistic == planned == conservative."""
        plan = self._make_plan(3, 3, 3)
        assert plan.planned_days == 3

    def test_invalid_ordering_raises(self):
        """conservative_days < planned_days must raise."""
        with pytest.raises(ValidationError) as exc_info:
            self._make_plan(5, 5, 3)
        assert "optimistic_days" in str(exc_info.value) or "conservative" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Fixture file integrity checks
# ---------------------------------------------------------------------------

class TestFixtureFiles:
    @pytest.mark.parametrize("scenario", ["clean", "srm", "weak_cuped"])
    def test_config_yaml_parses(self, scenario):
        """Each fixture config.yaml must parse into a valid ExperimentConfig."""
        path = FIXTURES / scenario / "config.yaml"
        assert path.exists(), f"{path} does not exist"
        with path.open() as f:
            data = yaml.safe_load(f)
        cfg = ExperimentConfig(**data)
        assert cfg.experiment_id is not None

    @pytest.mark.parametrize("scenario", ["clean", "srm", "weak_cuped"])
    def test_assignment_csv_has_required_columns(self, scenario):
        path = FIXTURES / scenario / "assignments.csv"
        assert path.exists(), f"{path} does not exist"
        with path.open() as f:
            reader = csv.DictReader(f)
            cols = list(reader.fieldnames or [])
        issues = validate_assignment_columns(cols)
        assert issues == [], f"Scenario {scenario} assignment CSV missing columns: {issues}"

    @pytest.mark.parametrize("scenario", ["clean", "srm", "weak_cuped"])
    def test_metric_csv_has_required_columns(self, scenario):
        path = FIXTURES / scenario / "metrics.csv"
        assert path.exists(), f"{path} does not exist"
        with path.open() as f:
            reader = csv.DictReader(f)
            cols = list(reader.fieldnames or [])
        issues = validate_metric_columns(cols)
        assert issues == [], f"Scenario {scenario} metrics CSV missing columns: {issues}"

    def test_clean_assignments_balanced(self):
        """Clean fixture must have exactly 100 control and 100 treatment rows."""
        path = FIXTURES / "clean" / "assignments.csv"
        counts: dict[str, int] = {}
        with path.open() as f:
            for row in csv.DictReader(f):
                v = row["variant"]
                counts[v] = counts.get(v, 0) + 1
        assert counts["control"] == 100
        assert counts["treatment"] == 100

    def test_srm_assignments_skewed(self):
        """SRM fixture must have a drift of at least 0.10 from expected 50/50."""
        path = FIXTURES / "srm" / "assignments.csv"
        counts: dict[str, int] = {}
        with path.open() as f:
            for row in csv.DictReader(f):
                v = row["variant"]
                counts[v] = counts.get(v, 0) + 1
        total = sum(counts.values())
        drift = abs(counts["control"] / total - 0.5)
        assert drift >= 0.10, f"Expected drift >= 0.10, got {drift:.3f}"

    def test_weak_cuped_low_pre_period_coverage(self):
        """Weak CUPED fixture must have fewer than 50% units with pre-period data."""
        path = FIXTURES / "weak_cuped" / "metrics.csv"
        post_units: set[str] = set()
        pre_units:  set[str] = set()
        with path.open() as f:
            for row in csv.DictReader(f):
                if row["period"] == "post":
                    post_units.add(row["unit_id"])
                elif row["period"] == "pre":
                    pre_units.add(row["unit_id"])
        coverage = len(pre_units & post_units) / len(post_units)
        assert coverage < 0.50, f"Expected coverage < 0.50, got {coverage:.3f}"


# ---------------------------------------------------------------------------
# Enum serialisation — model_dump() and model_dump_json() must emit string values
# ---------------------------------------------------------------------------

class TestEnumSerialisation:
    """
    Guards against the Pydantic v2 default where model_dump() returns enum
    member objects rather than their wire string values.  use_enum_values=True
    must be set on every model via _MODEL_CFG.
    """

    def test_diagnostic_issue_model_dump_severity_is_string(self):
        issue = DiagnosticIssue(
            code="TEST_CODE",
            severity=IssueSeverity.error,
            message="test",
        )
        dumped = issue.model_dump()
        assert dumped["severity"] == "error", (
            f"model_dump() returned {dumped['severity']!r}, expected 'error' string"
        )

    def test_srm_check_model_dump_severity_is_string(self):
        srm = SrmCheck(
            severity=SrmSeverity.pass_,
            chi2_stat=0.5,
            p_value=0.48,
            srm_alpha_used=0.01,
            observed_counts={"control": 100, "treatment": 100},
            expected_counts={"control": 100.0, "treatment": 100.0},
            observed_allocation={"control": 0.5, "treatment": 0.5},
            expected_allocation={"control": 0.5, "treatment": 0.5},
            max_absolute_drift=0.0,
            explanation="clean",
        )
        dumped = srm.model_dump()
        assert dumped["severity"] == "pass", (
            f"model_dump() returned {dumped['severity']!r}, expected 'pass' string"
        )

    def test_srm_check_json_severity_is_pass_string(self):
        """JSON round-trip: severity='pass' must survive, not become 'pass_'."""
        import json
        srm = SrmCheck(
            severity=SrmSeverity.pass_,
            chi2_stat=0.5, p_value=0.48, srm_alpha_used=0.01,
            observed_counts={"control": 100, "treatment": 100},
            expected_counts={"control": 100.0, "treatment": 100.0},
            observed_allocation={"control": 0.5, "treatment": 0.5},
            expected_allocation={"control": 0.5, "treatment": 0.5},
            max_absolute_drift=0.0, explanation="clean",
        )
        parsed = json.loads(srm.model_dump_json())
        assert parsed["severity"] == "pass"

    def test_experiment_config_metric_type_model_dump_is_string(self):
        cfg = ExperimentConfig(**_minimal_config())
        dumped = cfg.model_dump()
        assert dumped["metric_type"] == "proportion"

    def test_metric_estimate_metric_type_model_dump_is_string(self):
        est = MetricEstimate(
            metric_name="conversion_rate",
            metric_type=MetricType.proportion,
            control_mean=0.10,
            treatment_mean=0.12,
            absolute_lift=0.02,
            relative_lift=0.20,
        )
        dumped = est.model_dump()
        assert dumped["metric_type"] == "proportion"


# ---------------------------------------------------------------------------
# MetricSpec — construction and coercion
# ---------------------------------------------------------------------------

class TestMetricSpec:
    def test_string_coercion_defaults_to_continuous(self):
        spec = MetricSpec.from_str_or_dict("revenue_per_user")
        assert spec.name == "revenue_per_user"
        assert spec.metric_type == MetricType.continuous
        assert spec.is_guardrail is False

    def test_dict_coercion_with_proportion(self):
        spec = MetricSpec.from_str_or_dict({"name": "refund_rate", "metric_type": "proportion"})
        assert spec.name == "refund_rate"
        assert spec.metric_type == MetricType.proportion

    def test_passthrough_of_existing_spec(self):
        original = MetricSpec(name="ctr", metric_type=MetricType.proportion)
        result = MetricSpec.from_str_or_dict(original)
        assert result is original

    def test_metric_spec_serialises_type_as_string(self):
        spec = MetricSpec(name="ctr", metric_type=MetricType.proportion)
        assert spec.model_dump()["metric_type"] == "proportion"


# ---------------------------------------------------------------------------
# Semantic fixture assertions — validate that fixture data will drive the
# expected SRM severity and CUPED recommendation when quality.py is implemented
# ---------------------------------------------------------------------------

class TestFixtureSemanticsReadiness:
    """
    These tests assert numerical properties of the fixture data that will
    determine semantic outcomes (SRM severity, CUPED tier) when quality.py
    and variance.py are implemented.  They act as forward compatibility guards:
    if a fixture is regenerated carelessly, these tests will fail before any
    statistical logic is written.
    """

    def test_srm_fixture_drift_triggers_critical(self):
        """
        SRM fixture drift (0.15) must exceed both classification thresholds:
          - 0.01 (pass/warning boundary)
          - 0.02 (warning/critical boundary)
        A chi-squared test on 105/195 vs 150/150 expected must produce p < 0.01.
        This guarantees severity=critical when quality.py runs the SRM check.
        """
        import math
        path = FIXTURES / "srm" / "assignments.csv"
        counts: dict[str, int] = {}
        with path.open() as f:
            for row in csv.DictReader(f):
                v = row["variant"]
                counts[v] = counts.get(v, 0) + 1
        total = sum(counts.values())
        max_drift = max(abs(n / total - 0.5) for n in counts.values())

        # Drift must exceed the critical threshold (>= 0.02)
        assert max_drift >= 0.02, f"drift={max_drift:.4f} — too small for critical severity"

        # Chi-squared check (manual, no scipy dependency in tests)
        expected_each = total / 2
        chi2 = sum((n - expected_each) ** 2 / expected_each for n in counts.values())
        # chi2 CDF approximation: for df=1, chi2=27 => p << 0.001
        assert chi2 > 10.0, f"chi2={chi2:.2f} — not significant enough for p < 0.01"

    def test_clean_fixture_drift_is_pass(self):
        """
        Clean fixture must have zero drift (exactly 100/100).
        A chi-squared test would produce p=1.0 → severity=pass.
        """
        path = FIXTURES / "clean" / "assignments.csv"
        counts: dict[str, int] = {}
        with path.open() as f:
            for row in csv.DictReader(f):
                v = row["variant"]
                counts[v] = counts.get(v, 0) + 1
        total = sum(counts.values())
        max_drift = max(abs(n / total - 0.5) for n in counts.values())
        assert max_drift < 0.01, f"drift={max_drift:.4f} — should be < 0.01 for pass"

    def test_clean_fixture_cuped_will_be_recommended(self):
        """
        Clean fixture: coverage=1.00 >= 0.80 and Pearson r ~ 0.77 >= 0.30.
        Both thresholds for 'recommended' tier are satisfied.
        """
        import math, statistics
        path = FIXTURES / "clean" / "metrics.csv"
        post: dict[str, float] = {}
        pre:  dict[str, float] = {}
        with path.open() as f:
            for row in csv.DictReader(f):
                if row["metric_name"] != "conversion_rate":
                    continue
                uid, val, period = row["unit_id"], float(row["metric_value"]), row["period"]
                if period == "post":
                    post[uid] = val
                else:
                    pre[uid] = val
        matched = set(post) & set(pre)
        coverage = len(matched) / len(post)
        assert coverage >= 0.80, f"coverage={coverage:.3f} — won't hit 'recommended' tier"

        pre_v  = [pre[u]  for u in sorted(matched)]
        post_v = [post[u] for u in sorted(matched)]
        mp, mq = statistics.mean(pre_v), statistics.mean(post_v)
        num = sum((p - mp) * (q - mq) for p, q in zip(pre_v, post_v))
        den = math.sqrt(
            sum((p - mp) ** 2 for p in pre_v) * sum((q - mq) ** 2 for q in post_v)
        )
        r = num / den if den else 0.0
        assert r >= 0.30, f"r={r:.4f} — won't hit 'recommended' tier (need >= 0.30)"

    def test_weak_cuped_fixture_will_be_not_recommended(self):
        """
        Weak CUPED fixture: coverage=0.40 < 0.50, r ≈ -0.11.
        Either condition alone is sufficient for 'not_recommended'.
        """
        import math, statistics
        path = FIXTURES / "weak_cuped" / "metrics.csv"
        post: dict[str, float] = {}
        pre:  dict[str, float] = {}
        with path.open() as f:
            for row in csv.DictReader(f):
                uid, val, period = row["unit_id"], float(row["metric_value"]), row["period"]
                if period == "post":
                    post[uid] = val
                else:
                    pre[uid] = val
        matched = set(post) & set(pre)
        coverage = len(matched) / len(post)

        pre_v  = [pre[u]  for u in sorted(matched)]
        post_v = [post[u] for u in sorted(matched)]
        mp, mq = statistics.mean(pre_v), statistics.mean(post_v)
        num = sum((p - mp) * (q - mq) for p, q in zip(pre_v, post_v))
        den = math.sqrt(
            sum((p - mp) ** 2 for p in pre_v) * sum((q - mq) ** 2 for q in post_v)
        )
        r = num / den if den else 0.0

        # At least one of the two not_recommended conditions must hold
        fails_coverage = coverage < 0.50
        fails_correlation = r < 0.10
        assert fails_coverage or fails_correlation, (
            f"coverage={coverage:.3f}, r={r:.4f} — fixture will incorrectly hit 'optional' or 'recommended'"
        )
        # Both should hold in this fixture
        assert fails_coverage, f"coverage={coverage:.3f} is too high; fixture should have < 50%"


# ---------------------------------------------------------------------------
# MetricEstimate.relative_lift — None when control_mean is zero
# ---------------------------------------------------------------------------

class TestMetricEstimateRelativeLift:
    """
    relative_lift is float | None.

    It is None when ctrl_mean == 0 (the ratio is undefined, not zero).
    Downstream consumers must display "—" or "undefined" for None — they must
    not silently substitute 0 or any other number.
    """

    def _base_kwargs(self, **overrides):
        base = dict(
            metric_name="conversion_rate",
            control_mean=0.10,
            treatment_mean=0.12,
            absolute_lift=0.02,
        )
        base.update(overrides)
        return base

    def test_relative_lift_float_stored_as_float(self):
        """Normal case: relative_lift is stored and returned as a float."""
        est = MetricEstimate(**self._base_kwargs(relative_lift=0.20))
        assert est.relative_lift == pytest.approx(0.20)

    def test_relative_lift_none_accepted(self):
        """None is a valid value for relative_lift (undefined ratio)."""
        est = MetricEstimate(**self._base_kwargs(relative_lift=None))
        assert est.relative_lift is None

    def test_relative_lift_defaults_to_none(self):
        """When relative_lift is omitted entirely, it defaults to None."""
        est = MetricEstimate(**self._base_kwargs())
        assert est.relative_lift is None

    def test_relative_lift_none_serialises_as_null_in_json(self):
        """model_dump_json() must emit null for relative_lift=None, not 0 or NaN."""
        import json
        est = MetricEstimate(**self._base_kwargs(relative_lift=None))
        payload = json.loads(est.model_dump_json())
        assert payload["relative_lift"] is None, (
            f"Expected null in JSON; got {payload['relative_lift']!r}"
        )

    def test_relative_lift_none_in_model_dump(self):
        """model_dump() must return None for relative_lift=None."""
        est = MetricEstimate(**self._base_kwargs(relative_lift=None))
        dumped = est.model_dump()
        assert dumped["relative_lift"] is None

    def test_relative_lift_zero_is_distinct_from_none(self):
        """
        0.0 is a valid semantic value (exactly no relative change).
        It must not be confused with None (undefined ratio).
        """
        est_zero = MetricEstimate(**self._base_kwargs(relative_lift=0.0))
        est_none = MetricEstimate(**self._base_kwargs(relative_lift=None))
        assert est_zero.relative_lift == 0.0
        assert est_none.relative_lift is None
        assert est_zero.relative_lift != est_none.relative_lift
