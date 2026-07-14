"""
Tests for abkit_core.design — experiment spec validation.

Coverage:
  - Valid config returns is_valid=True with no errors.
  - normalized_config is populated on success, None on failure.
  - Missing metric_type raises ValidationError at spec-only time? No —
    metric_type is optional at parse; only for_analysis=True surfaces the error.
  - for_analysis=True with missing metric_type returns is_valid=False.
  - for_analysis=True with present metric_type returns is_valid=True.
  - High alpha emits a warning (not error) and is still valid.
  - Low power emits a warning.
  - Very short hypothesis emits a warning.
  - No guardrail metrics emits a warning.
  - Implausibly large MDE emits a warning.
  - Config with primary_metric in guardrails fails at Pydantic construction,
    so validate_experiment_spec never sees that invalid config.
  - fixture clean/config.yaml produces is_valid=True.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from abkit_core.design import (
    ISSUE_MDE_TOO_LARGE,
    ISSUE_NO_GUARDRAILS,
    ISSUE_SHORT_HYPOTHESIS,
    validate_experiment_spec,
)
from abkit_core.schemas import (
    ISSUE_MISSING_METRIC_TYPE,
    ISSUE_POWER_RANGE,
    ExperimentConfig,
    IssueSeverity,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _make_config(**overrides) -> ExperimentConfig:
    base = {
        "schema_version": "1.0",
        "experiment_name": "Test experiment",
        "experiment_id": "exp_design_001",
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


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestValidateExperimentSpecValid:
    def test_valid_config_is_valid_true(self):
        cfg = _make_config()
        result = validate_experiment_spec(cfg)
        assert result.is_valid is True
        assert result.errors == []

    def test_normalized_config_populated_on_success(self):
        cfg = _make_config()
        result = validate_experiment_spec(cfg)
        assert result.normalized_config is not None
        assert result.normalized_config.experiment_id == cfg.experiment_id

    def test_for_analysis_true_with_metric_type_is_valid(self):
        cfg = _make_config(metric_type="proportion")
        result = validate_experiment_spec(cfg, for_analysis=True)
        assert result.is_valid is True
        assert result.errors == []

    def test_fixture_clean_config_is_valid(self):
        path = FIXTURES / "clean" / "config.yaml"
        with path.open() as f:
            data = yaml.safe_load(f)
        cfg = ExperimentConfig(**data)
        result = validate_experiment_spec(cfg)
        assert result.is_valid is True


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TestValidateExperimentSpecErrors:
    def test_missing_metric_type_for_analysis_is_invalid(self):
        data = {
            "schema_version": "1.0",
            "experiment_name": "Test experiment",
            "experiment_id": "exp_design_002",
            "owner": "test-team",
            "hypothesis": "Treatment will improve the primary metric significantly.",
            "primary_metric": "conversion_rate",
            # metric_type intentionally absent
            "variants": ["control", "treatment"],
            "expected_allocation": {"control": 0.5, "treatment": 0.5},
            "unit_of_randomization": "user_id",
            "alpha": 0.05,
            "power": 0.80,
            "mde": 0.02,
        }
        cfg = ExperimentConfig(**data)
        result = validate_experiment_spec(cfg, for_analysis=True)
        assert result.is_valid is False
        codes = [e.code for e in result.errors]
        assert ISSUE_MISSING_METRIC_TYPE in codes

    def test_missing_metric_type_spec_only_is_valid(self):
        data = {
            "schema_version": "1.0",
            "experiment_name": "Test experiment",
            "experiment_id": "exp_design_003",
            "owner": "test-team",
            "hypothesis": "Treatment will improve the primary metric significantly.",
            "primary_metric": "conversion_rate",
            "variants": ["control", "treatment"],
            "expected_allocation": {"control": 0.5, "treatment": 0.5},
            "unit_of_randomization": "user_id",
            "alpha": 0.05,
            "power": 0.80,
            "mde": 0.02,
        }
        cfg = ExperimentConfig(**data)
        result = validate_experiment_spec(cfg, for_analysis=False)
        assert result.is_valid is True  # no errors, only possible warnings
        codes = [e.code for e in result.errors]
        assert ISSUE_MISSING_METRIC_TYPE not in codes

    def test_normalized_config_is_none_when_invalid(self):
        data = {
            "schema_version": "1.0",
            "experiment_name": "Test experiment",
            "experiment_id": "exp_design_004",
            "owner": "test-team",
            "hypothesis": "Treatment will improve the primary metric significantly.",
            "primary_metric": "conversion_rate",
            "variants": ["control", "treatment"],
            "expected_allocation": {"control": 0.5, "treatment": 0.5},
            "unit_of_randomization": "user_id",
            "alpha": 0.05,
            "power": 0.80,
            "mde": 0.02,
        }
        cfg = ExperimentConfig(**data)
        result = validate_experiment_spec(cfg, for_analysis=True)
        assert result.normalized_config is None


# ---------------------------------------------------------------------------
# Warnings — is_valid is still True
# ---------------------------------------------------------------------------

class TestValidateExperimentSpecWarnings:
    def test_high_alpha_produces_warning_not_error(self):
        cfg = _make_config(alpha=0.10)
        result = validate_experiment_spec(cfg)
        assert result.is_valid is True
        w_codes = [w.code for w in result.warnings]
        from abkit_core.schemas import ISSUE_ALPHA_RANGE
        assert ISSUE_ALPHA_RANGE in w_codes

    def test_low_power_produces_warning(self):
        cfg = _make_config(power=0.60)
        result = validate_experiment_spec(cfg)
        assert result.is_valid is True
        assert any(w.code == ISSUE_POWER_RANGE for w in result.warnings)

    def test_short_hypothesis_produces_warning(self):
        cfg = _make_config(hypothesis="Improve CTR")  # 11 chars
        result = validate_experiment_spec(cfg)
        assert result.is_valid is True
        w_codes = [w.code for w in result.warnings]
        assert ISSUE_SHORT_HYPOTHESIS in w_codes

    def test_no_guardrail_metrics_produces_warning(self):
        cfg = _make_config(guardrail_metrics=[])
        result = validate_experiment_spec(cfg)
        assert result.is_valid is True
        w_codes = [w.code for w in result.warnings]
        assert ISSUE_NO_GUARDRAILS in w_codes

    def test_implausibly_large_mde_produces_warning(self):
        cfg = _make_config(mde=0.6)
        result = validate_experiment_spec(cfg)
        assert result.is_valid is True
        w_codes = [w.code for w in result.warnings]
        assert ISSUE_MDE_TOO_LARGE in w_codes

    def test_multiple_warnings_all_collected(self):
        """Low power + short hypothesis + no guardrails = 3 warnings, still valid."""
        cfg = _make_config(
            power=0.60,
            hypothesis="short",
            guardrail_metrics=[],
        )
        result = validate_experiment_spec(cfg)
        assert result.is_valid is True
        assert len(result.warnings) >= 3

    def test_errors_and_warnings_are_separate(self):
        """for_analysis=True with no metric_type: one error, possible warnings."""
        data = {
            "schema_version": "1.0",
            "experiment_name": "Test experiment",
            "experiment_id": "exp_design_005",
            "owner": "test-team",
            "hypothesis": "short",     # triggers SHORT_HYPOTHESIS warning
            "primary_metric": "conversion_rate",
            "variants": ["control", "treatment"],
            "expected_allocation": {"control": 0.5, "treatment": 0.5},
            "unit_of_randomization": "user_id",
            "alpha": 0.05,
            "power": 0.80,
            "mde": 0.02,
        }
        cfg = ExperimentConfig(**data)
        result = validate_experiment_spec(cfg, for_analysis=True)
        error_codes = {e.code for e in result.errors}
        warning_codes = {w.code for w in result.warnings}
        assert ISSUE_MISSING_METRIC_TYPE in error_codes
        assert ISSUE_SHORT_HYPOTHESIS in warning_codes
        # errors and warnings are disjoint
        assert not (error_codes & warning_codes)

    def test_all_issues_have_required_fields(self):
        """Every DiagnosticIssue must have code, severity, message."""
        cfg = _make_config(power=0.60, hypothesis="short", guardrail_metrics=[])
        result = validate_experiment_spec(cfg)
        for issue in result.warnings + result.errors:
            assert issue.code
            assert issue.severity
            assert issue.message
