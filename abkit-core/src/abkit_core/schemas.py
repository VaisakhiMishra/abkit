"""
Shared Pydantic schemas for the A/B Testing Productivity Kit.

All layers (core, app, skills, templates) must import from this module.
Schema drift should be treated as a bug — do not redefine these structures elsewhere.

Schema version: 1.2
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations — canonical allowed values used across the kit
# ---------------------------------------------------------------------------

class MetricType(str, Enum):
    """Statistical type of the primary metric. Drives test selection."""
    proportion = "proportion"
    continuous = "continuous"


class IssueSeverity(str, Enum):
    """Severity level for a DiagnosticIssue."""
    info = "info"
    warning = "warning"
    error = "error"


class SrmSeverity(str, Enum):
    """Trust classification for the SRM check result."""
    pass_ = "pass"
    warning = "warning"
    critical = "critical"

    # Allow "pass" as a string value even though it is a Python keyword
    @classmethod
    def _missing_(cls, value: object) -> "SrmSeverity | None":
        if value == "pass":
            return cls.pass_
        return None


class CupedRecommendation(str, Enum):
    """Tiered CUPED readiness verdict."""
    recommended = "recommended"
    optional = "optional"
    not_recommended = "not_recommended"


class RunStatus(str, Enum):
    """Overall status of a result payload."""
    success = "success"
    warning = "warning"
    failed = "failed"


class DecisionRecommendation(str, Enum):
    """High-level experiment decision."""
    ship = "ship"
    hold = "hold"
    rerun = "rerun"
    inconclusive = "inconclusive"


class GuardrailDirection(str, Enum):
    """
    Desired direction for a guardrail metric.

    ``increase`` — the metric should go up (block only if lift is negative).
    ``decrease`` — the metric should go down (block only if lift is positive).
    ``flat``     — no movement is desired; any significant change blocks.

    When a guardrail metric is not listed in ``guardrail_directions`` on the
    config, the legacy v1 behaviour applies: any significant movement blocks.
    """
    increase = "increase"
    decrease = "decrease"
    flat = "flat"


class MetricPeriod(str, Enum):
    """Metric observation period."""
    pre = "pre"
    post = "post"


# ---------------------------------------------------------------------------
# Shared model config — applied to every model so that model_dump() and
# model_dump_json() always emit enum string values, never enum member objects.
# ---------------------------------------------------------------------------

_MODEL_CFG = ConfigDict(use_enum_values=True)


# ---------------------------------------------------------------------------
# MetricSpec — per-metric type declaration for secondary and guardrail metrics
# ---------------------------------------------------------------------------

class MetricSpec(BaseModel):
    """
    Typed specification for a single secondary or guardrail metric.

    Accepts either a plain string shorthand (name only, type inferred as
    ``continuous``) or a full object so callers can be explicit.

    Examples::

        # shorthand — converted automatically
        "revenue_per_user"

        # full spec
        {"name": "revenue_per_user", "metric_type": "continuous"}
    """

    model_config = _MODEL_CFG

    name: str = Field(..., description="Canonical metric name.")
    metric_type: MetricType = Field(
        default=MetricType.continuous,
        description=(
            "Statistical type for this metric. Defaults to 'continuous' when not specified. "
            "Set to 'proportion' for binary or rate metrics."
        ),
    )
    is_guardrail: bool = Field(
        default=False,
        description="True when this metric is used as a guardrail.",
    )
    description: str | None = Field(
        default=None,
        description="Optional human-readable description.",
    )

    @classmethod
    def from_str_or_dict(cls, value: "str | dict | MetricSpec") -> "MetricSpec":
        """Coerce a plain string name into a MetricSpec with default type."""
        if isinstance(value, str):
            return cls(name=value)
        if isinstance(value, dict):
            return cls(**value)
        return value


# ---------------------------------------------------------------------------
# DiagnosticIssue — reusable across all validation and QA functions
# ---------------------------------------------------------------------------

class DiagnosticIssue(BaseModel):
    """
    A structured issue produced by any validation or QA function.

    Use stable ``code`` values so downstream consumers can filter reliably.
    ``field`` should name the offending field or CSV column when applicable.
    """

    model_config = _MODEL_CFG

    code: str = Field(..., description="Stable machine-readable issue code.")
    severity: IssueSeverity
    message: str = Field(..., description="Human-readable explanation.")
    field: str | None = Field(
        default=None,
        description="Field or column involved, or None if not field-specific.",
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured details for programmatic use.",
    )


# ---------------------------------------------------------------------------
# ExperimentConfig — primary control object for an analysis run
# ---------------------------------------------------------------------------

class ExperimentConfig(BaseModel):
    """
    Experiment configuration.

    ``metric_type`` is listed in the required-fields table in 03-schemas.md.
    It may be omitted for spec-validation-only runs, but ``validate_config``
    will return a MISSING_METRIC_TYPE error if an analysis run is attempted
    without it.

    ``secondary_metrics`` and ``guardrail_metrics`` accept either plain
    strings (name only) or full ``MetricSpec`` objects.  Plain strings are
    coerced to ``MetricSpec(name=..., metric_type='continuous')`` automatically.
    """

    model_config = _MODEL_CFG

    # --- Required fields ---
    schema_version: str = Field(..., description="Version of the config structure.")
    experiment_name: str = Field(..., description="Human-readable experiment name.")
    experiment_id: str = Field(..., description="Stable identifier for the experiment.")
    owner: str = Field(..., description="Primary owner or team.")
    hypothesis: str = Field(..., description="Short statement of expected impact.")
    primary_metric: str = Field(..., description="Canonical name of the main success metric.")
    metric_type: MetricType | None = Field(
        default=None,
        description=(
            "Statistical type of the primary metric: 'proportion' or 'continuous'. "
            "Required for analysis runs; optional for spec-validation-only runs."
        ),
    )
    variants: list[str] = Field(
        ...,
        min_length=2,
        description="Allowed variant names, e.g. ['control', 'treatment'].",
    )
    expected_allocation: dict[str, float] = Field(
        ...,
        description="Planned traffic split by variant. Values must sum to 1.0.",
    )
    unit_of_randomization: str = Field(
        ...,
        description="Unit assigned to variants, e.g. 'user_id' or 'session_id'.",
    )
    alpha: float = Field(
        ...,
        gt=0,
        lt=1,
        description="Significance threshold used for planning or reporting.",
    )
    power: float = Field(
        ...,
        gt=0,
        lt=1,
        description="Desired statistical power for planning.",
    )
    mde: float = Field(
        ...,
        gt=0,
        description="Minimum detectable effect for planning.",
    )

    # --- Optional fields ---
    secondary_metrics: list[MetricSpec] = Field(
        default_factory=list,
        description=(
            "Additional metrics of interest. "
            "Each entry may be a plain string (name only) or a full MetricSpec object."
        ),
    )
    guardrail_metrics: list[MetricSpec] = Field(
        default_factory=list,
        description=(
            "Metrics used to detect harmful side effects. "
            "Each entry may be a plain string (name only) or a full MetricSpec object."
        ),
    )
    guardrail_directions: dict[str, GuardrailDirection] = Field(
        default_factory=dict,
        description=(
            "Optional map of guardrail metric name → desired direction. "
            "Allowed values: 'increase', 'decrease', 'flat'. "
            "When a guardrail metric is not listed here the legacy v1 behaviour "
            "applies: any statistically significant movement triggers a hold. "
            "Example: {\"revenue\": \"increase\", \"refund_rate\": \"decrease\"}."
        ),
    )

    @field_validator("secondary_metrics", "guardrail_metrics", mode="before")
    @classmethod
    def _coerce_metric_specs(cls, v: list) -> list[MetricSpec]:
        """Accept plain strings and dicts alongside MetricSpec objects."""
        return [MetricSpec.from_str_or_dict(item) for item in v]

    segments: list[str] = Field(
        default_factory=list,
        description="Segment dimensions to analyze, e.g. ['country', 'device_type'].",
    )
    pre_period_window_days: int | None = Field(
        default=None,
        gt=0,
        description="Number of days used for CUPED covariates.",
    )
    analysis_window_days: int | None = Field(
        default=None,
        gt=0,
        description="Planned experiment window in days.",
    )
    srm_alpha: float | None = Field(
        default=None,
        description=(
            "Per-experiment SRM detection threshold. "
            "Overrides the global default of 0.01. Must be between 0.001 and 0.1."
        ),
    )
    daily_eligible_traffic: int | None = Field(
        default=None,
        gt=0,
        description="Expected number of eligible units entering the experiment per day.",
    )
    eligibility_rate: float | None = Field(
        default=None,
        description="Fraction of total traffic that is eligible. Between 0 and 1 exclusive.",
    )
    traffic_cap: float | None = Field(
        default=None,
        description=(
            "Maximum fraction of eligible traffic to route to the experiment. "
            "Between 0 and 1 exclusive. Defaults to 1.0 when not set."
        ),
    )
    ramp_up_days: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Days before full traffic allocation is reached. "
            "Treated as a planning buffer, not a novelty-effect model."
        ),
    )
    planning_buffer_pct: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Additional fractional buffer on the planned duration. "
            "e.g. 0.2 adds 20 percent."
        ),
    )
    apply_bonferroni_correction: bool = Field(
        default=False,
        description=(
            "When True, Bonferroni correction is applied to secondary metrics. "
            "If there are m secondary metrics, significance is tested at alpha / m "
            "rather than alpha. Has no effect when there are 0 or 1 secondary metrics. "
            "The primary metric is always tested at the declared alpha. "
            "Guardrail metrics are always tested at the declared alpha."
        ),
    )
    exclusion_rules: list[str] = Field(
        default_factory=list,
        description="Plain-language exclusions or filter notes.",
    )
    notes: str | None = Field(default=None, description="Free-form context.")

    # ------------------------------------------------------------------
    # Cross-field validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _check_variants_unique(self) -> "ExperimentConfig":
        if len(self.variants) != len(set(self.variants)):
            raise ValueError("Variant names must be unique.")
        return self

    @model_validator(mode="after")
    def _check_primary_not_in_secondary_or_guardrail(self) -> "ExperimentConfig":
        secondary_names = {m.name for m in self.secondary_metrics}
        guardrail_names = {m.name for m in self.guardrail_metrics}
        if self.primary_metric in secondary_names:
            raise ValueError(
                f"primary_metric '{self.primary_metric}' must not also appear in secondary_metrics."
            )
        if self.primary_metric in guardrail_names:
            raise ValueError(
                f"primary_metric '{self.primary_metric}' must not also appear in guardrail_metrics."
            )
        return self

    @model_validator(mode="after")
    def _check_allocation_keys_match_variants(self) -> "ExperimentConfig":
        if set(self.expected_allocation.keys()) != set(self.variants):
            raise ValueError(
                "Keys in expected_allocation must exactly match the declared variants."
            )
        return self

    @model_validator(mode="after")
    def _check_allocation_sum(self) -> "ExperimentConfig":
        total = sum(self.expected_allocation.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"expected_allocation values must sum to 1.0 (got {total:.6f})."
            )
        return self

    @model_validator(mode="after")
    def _check_guardrail_directions_keys(self) -> "ExperimentConfig":
        """
        Every key in guardrail_directions must be a declared guardrail metric name.

        This is enforced as a Pydantic model_validator so that misconfigured
        YAML files (e.g. a typo in a metric name) are caught at load time, not
        silently ignored at analysis time.
        """
        guardrail_names = {m.name for m in self.guardrail_metrics}
        unknown = set(self.guardrail_directions.keys()) - guardrail_names
        if unknown:
            raise ValueError(
                f"guardrail_directions contains metric name(s) not in guardrail_metrics: "
                f"{sorted(unknown)}. "
                "Each key must match a metric name listed in guardrail_metrics."
            )
        return self

    @model_validator(mode="after")
    def _check_srm_alpha_range(self) -> "ExperimentConfig":
        if self.srm_alpha is not None and not (0.001 <= self.srm_alpha <= 0.1):
            raise ValueError("srm_alpha must be between 0.001 and 0.1.")
        return self

    @model_validator(mode="after")
    def _check_rate_fields(self) -> "ExperimentConfig":
        for fname, val in [
            ("eligibility_rate", self.eligibility_rate),
            ("traffic_cap", self.traffic_cap),
        ]:
            if val is not None and not (0.0 < val <= 1.0):
                raise ValueError(f"{fname} must be between 0 (exclusive) and 1 (inclusive).")
        return self


# ---------------------------------------------------------------------------
# Assignment row — one row in the assignment CSV
# ---------------------------------------------------------------------------

class AssignmentRow(BaseModel):
    """Single row from an experiment assignment CSV file."""

    model_config = _MODEL_CFG

    experiment_id: str
    unit_id: str
    variant: str
    assignment_ts: datetime

    # Optional columns
    exposed: bool | None = None
    exposure_ts: datetime | None = None
    country: str | None = None
    device_type: str | None = None
    tenure_bucket: str | None = None
    is_eligible: bool | None = None


# ---------------------------------------------------------------------------
# Metric row — one row in the metric CSV (long format)
# ---------------------------------------------------------------------------

class MetricRow(BaseModel):
    """Single row from a metric CSV file. Long format: one row per (unit, metric, period)."""

    model_config = _MODEL_CFG

    experiment_id: str
    unit_id: str
    metric_name: str
    metric_value: float
    period: MetricPeriod

    # Optional columns
    metric_ts: datetime | None = None
    is_guardrail: bool | None = None
    country: str | None = None
    device_type: str | None = None
    currency: str | None = None


# ---------------------------------------------------------------------------
# Result payload sub-objects
# ---------------------------------------------------------------------------

class SpecValidation(BaseModel):
    """Output from experiment config validation."""

    model_config = _MODEL_CFG

    is_valid: bool
    errors: list[DiagnosticIssue] = Field(default_factory=list)
    warnings: list[DiagnosticIssue] = Field(default_factory=list)
    normalized_config: ExperimentConfig | None = None


class SrmCheck(BaseModel):
    """
    Full SRM diagnostic result.

    Severity is determined by the joint p-value + drift rule from 03-schemas.md.
    Never classified by p-value alone.
    """

    model_config = _MODEL_CFG

    severity: SrmSeverity
    chi2_stat: float
    p_value: float
    srm_alpha_used: float
    observed_counts: dict[str, int]
    expected_counts: dict[str, float]
    observed_allocation: dict[str, float]
    expected_allocation: dict[str, float]
    max_absolute_drift: float
    explanation: str
    issues: list[DiagnosticIssue] = Field(default_factory=list)


class CupedReadiness(BaseModel):
    """CUPED readiness assessment for a single metric."""

    model_config = _MODEL_CFG

    recommendation: CupedRecommendation
    matched_coverage: float = Field(ge=0.0, le=1.0)
    pre_post_correlation: float = Field(ge=-1.0, le=1.0)
    estimated_variance_reduction: float = Field(ge=0.0, le=1.0)
    explanation: str
    caveats: list[str] = Field(default_factory=list)


class DurationPlanAssumptions(BaseModel):
    """Echo of inputs used to compute the duration plan."""

    model_config = _MODEL_CFG

    traffic_cap: float
    ramp_up_days: int
    planning_buffer_pct: float
    eligibility_rate: float | None


class DurationPlan(BaseModel):
    """Three-scenario duration estimate produced by the planner."""

    model_config = _MODEL_CFG

    required_n_per_variant: int
    required_n_total: int
    daily_eligible_traffic: int
    optimistic_days: int
    planned_days: int
    conservative_days: int
    assumptions: DurationPlanAssumptions
    caveats: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_day_ordering(self) -> "DurationPlan":
        if not (self.optimistic_days <= self.planned_days <= self.conservative_days):
            raise ValueError(
                "Duration estimates must satisfy: "
                "optimistic_days <= planned_days <= conservative_days."
            )
        return self


class QualityChecks(BaseModel):
    """Container for all assignment and metric QA results."""

    model_config = _MODEL_CFG

    srm_check: SrmCheck | None = None
    balance_checks: list[DiagnosticIssue] = Field(default_factory=list)
    missingness_checks: list[DiagnosticIssue] = Field(default_factory=list)
    join_integrity_checks: list[DiagnosticIssue] = Field(default_factory=list)
    guardrail_status: list[DiagnosticIssue] = Field(default_factory=list)


class MetricEstimate(BaseModel):
    """Statistical estimate for one metric in one variant comparison."""

    model_config = _MODEL_CFG

    metric_name: str
    metric_type: MetricType = Field(
        default=MetricType.continuous,
        description="Statistical type used for inference on this metric.",
    )
    control_mean: float
    treatment_mean: float
    absolute_lift: float
    relative_lift: float | None = Field(
        default=None,
        description=(
            "Fractional change: absolute_lift / control_mean. "
            "None when control_mean is zero (ratio is undefined, not zero)."
        ),
    )
    p_value: float | None = None
    ci_lower: float | None = None
    ci_upper: float | None = None
    is_significant: bool | None = None


class AnalysisResult(BaseModel):
    """Statistical analysis outputs for the experiment."""

    model_config = _MODEL_CFG

    primary_metric_result: MetricEstimate | None = None
    effective_primary_estimate: MetricEstimate | None = Field(
        default=None,
        description=(
            "The estimate actually used for the decision: CUPED-adjusted when "
            "CUPED was applied, raw otherwise.  Identical to primary_metric_result "
            "when no CUPED adjustment was made.  The UI must use this field rather "
            "than recomputing the raw-vs-CUPED selection itself."
        ),
    )
    secondary_metric_results: list[MetricEstimate] = Field(default_factory=list)
    guardrail_metric_results: list[MetricEstimate] = Field(default_factory=list)
    cuped_estimate: MetricEstimate | None = None
    cuped_readiness: CupedReadiness | None = None
    segment_summaries: list[dict[str, Any]] = Field(default_factory=list)
    secondary_alpha_used: float | None = Field(
        default=None,
        description=(
            "The significance threshold actually applied to secondary metrics. "
            "Equals config.alpha when Bonferroni correction is off or when there is "
            "only one secondary metric. Equals config.alpha / m when Bonferroni "
            "correction is on and m > 1 secondary metrics were declared. "
            "None when no secondary metrics were declared."
        ),
    )
    bonferroni_correction_applied: bool = Field(
        default=False,
        description=(
            "True when Bonferroni correction was enabled in the config AND at least "
            "two secondary metrics were declared (i.e. the adjusted alpha differs "
            "from the base alpha). False otherwise, including when correction is "
            "enabled but only one secondary metric is present."
        ),
    )


class DecisionMemo(BaseModel):
    """High-level experiment decision and supporting narrative."""

    model_config = _MODEL_CFG

    recommendation: DecisionRecommendation
    reasoning_summary: str
    key_caveats: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    alpha_used: float | None = Field(
        default=None,
        description=(
            "The significance threshold (alpha) from the experiment config that "
            "was applied when reaching this decision.  Stored here so the UI "
            "can render 'p=0.02 < alpha=0.05' without accessing ExperimentConfig."
        ),
    )


class ResultPayload(BaseModel):
    """
    Canonical output of an analysis run.

    This is the single contract between abkit-core and all downstream consumers
    (Streamlit app, skills, templates). Any consumer must not re-implement the
    logic that produced these values.
    """

    model_config = _MODEL_CFG

    schema_version: str = "1.2"
    experiment_id: str
    run_id: str
    status: RunStatus
    generated_at: datetime
    spec_validation: SpecValidation
    duration_plan: DurationPlan | None = None
    quality_checks: QualityChecks | None = None
    analysis: AnalysisResult | None = None
    decision: DecisionMemo | None = None
    artifacts: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Validation helpers — business-rule checks that return DiagnosticIssue lists
# These are pure functions: no I/O, no statistics. Called by quality.py and
# analysis.py to enforce schema-level rules.
# ---------------------------------------------------------------------------

# Stable issue codes referenced in tests and downstream consumers
ISSUE_DUPLICATE_VARIANT = "DUPLICATE_VARIANT"
ISSUE_ALLOCATION_SUM = "ALLOCATION_SUM"
ISSUE_ALLOCATION_KEYS = "ALLOCATION_KEYS"
ISSUE_PRIMARY_IN_GUARDRAILS = "PRIMARY_METRIC_IN_GUARDRAILS"
ISSUE_MISSING_METRIC_TYPE = "MISSING_METRIC_TYPE"
ISSUE_ALPHA_RANGE = "ALPHA_OUT_OF_RANGE"
ISSUE_POWER_RANGE = "POWER_OUT_OF_RANGE"
ISSUE_MDE_RANGE = "MDE_OUT_OF_RANGE"
ISSUE_UNKNOWN_VARIANT = "UNKNOWN_VARIANT_IN_ASSIGNMENT"
ISSUE_MISSING_CSV_COLUMNS = "MISSING_REQUIRED_CSV_COLUMNS"
ISSUE_GUARDRAIL_DIRECTION_MISMATCH = "GUARDRAIL_DIRECTION_MISMATCH"


def validate_config(config: ExperimentConfig, *, for_analysis: bool = False) -> list[DiagnosticIssue]:
    """
    Run business-rule checks on a parsed ExperimentConfig.

    Pydantic handles structural validation (types, ranges declared in Field).
    This function checks cross-field business rules that Pydantic cannot express
    as single-field constraints.

    Args:
        config: A successfully parsed ExperimentConfig.
        for_analysis: When True, the absence of ``metric_type`` is an error.

    Returns:
        A list of DiagnosticIssue objects. Empty list means the config is clean.
    """
    issues: list[DiagnosticIssue] = []

    # metric_type required for analysis runs
    if for_analysis and config.metric_type is None:
        issues.append(DiagnosticIssue(
            code=ISSUE_MISSING_METRIC_TYPE,
            severity=IssueSeverity.error,
            message=(
                "metric_type is required to run analysis but was not provided. "
                "Set metric_type to 'proportion' or 'continuous'."
            ),
            field="metric_type",
        ))

    # alpha, power, mde reasonable-range warnings (Pydantic handles gt/lt,
    # but we add warnings for practically suspect values).
    #
    # Policy thresholds — rationale:
    #   alpha >= 0.1 : Published experiment standards rarely accept alpha above 0.05.
    #     0.1 is the looser end; anything at or above it warrants explicit acknowledgment.
    #   power < 0.7  : Industry convention treats 0.80 as the minimum acceptable power.
    #     0.7 is a lenient lower bound; below it the experiment is severely under-powered.
    if config.alpha >= 0.1:
        issues.append(DiagnosticIssue(
            code=ISSUE_ALPHA_RANGE,
            severity=IssueSeverity.warning,
            message=f"alpha={config.alpha} is unusually high. Common values are 0.05 or 0.01.",
            field="alpha",
        ))
    if config.power < 0.7:
        issues.append(DiagnosticIssue(
            code=ISSUE_POWER_RANGE,
            severity=IssueSeverity.warning,
            message=f"power={config.power} is unusually low. Common values are 0.80 or higher.",
            field="power",
        ))

    return issues


def validate_assignment_columns(columns: list[str]) -> list[DiagnosticIssue]:
    """
    Check that an assignment CSV has the required column headers.

    Args:
        columns: List of column names from the CSV (case-sensitive).

    Returns:
        A list of DiagnosticIssue objects. Empty means all required columns present.
    """
    required = {"experiment_id", "unit_id", "variant", "assignment_ts"}
    missing = required - set(columns)
    if missing:
        return [DiagnosticIssue(
            code=ISSUE_MISSING_CSV_COLUMNS,
            severity=IssueSeverity.error,
            message=f"Assignment CSV is missing required columns: {sorted(missing)}.",
            field=None,
            details={"missing_columns": sorted(missing)},
        )]
    return []


def validate_metric_columns(columns: list[str]) -> list[DiagnosticIssue]:
    """
    Check that a metric CSV has the required column headers.

    Args:
        columns: List of column names from the CSV (case-sensitive).

    Returns:
        A list of DiagnosticIssue objects. Empty means all required columns present.
    """
    required = {"experiment_id", "unit_id", "metric_name", "metric_value", "period"}
    missing = required - set(columns)
    if missing:
        return [DiagnosticIssue(
            code=ISSUE_MISSING_CSV_COLUMNS,
            severity=IssueSeverity.error,
            message=f"Metric CSV is missing required columns: {sorted(missing)}.",
            field=None,
            details={"missing_columns": sorted(missing)},
        )]
    return []


def validate_variant_labels(
    observed_variants: list[str],
    config: ExperimentConfig,
) -> list[DiagnosticIssue]:
    """
    Check that all variant labels in the assignment data are declared in the config.

    Args:
        observed_variants: Unique variant strings found in the assignment data.
        config: The validated experiment config.

    Returns:
        One DiagnosticIssue per unknown variant label found.
    """
    allowed = set(config.variants)
    issues: list[DiagnosticIssue] = []
    for v in sorted(set(observed_variants)):
        if v not in allowed:
            issues.append(DiagnosticIssue(
                code=ISSUE_UNKNOWN_VARIANT,
                severity=IssueSeverity.error,
                message=(
                    f"Variant '{v}' found in assignment data is not declared "
                    f"in the experiment config. Allowed variants: {sorted(allowed)}."
                ),
                field="variant",
                details={"unknown_variant": v, "allowed_variants": sorted(allowed)},
            ))
    return issues
