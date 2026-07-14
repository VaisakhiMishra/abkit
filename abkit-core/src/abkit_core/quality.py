"""
quality.py — Assignment and metric QA functions.

Responsibilities:
  - Validate assignment DataFrame shape and content.
  - Detect duplicate and conflicting unit assignments.
  - Check variant labels against the config allow-list.
  - Run the SRM chi-squared test and classify severity.
  - Validate metric DataFrame shape and content.
  - Detect join gaps between assignment and metric data.
  - Return structured QualityChecks results using shared schemas.

No statistical inference beyond SRM chi-squared lives here.
No Streamlit imports. No I/O beyond accepting DataFrames.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd
from scipy.stats import chisquare

from abkit_core.schemas import (
    DiagnosticIssue,
    ExperimentConfig,
    IssueSeverity,
    QualityChecks,
    SrmCheck,
    SrmSeverity,
    validate_assignment_columns,
    validate_metric_columns,
    validate_variant_labels,
)

if TYPE_CHECKING:
    pass  # keep import block clean

# ---------------------------------------------------------------------------
# Global defaults and named policy thresholds
# ---------------------------------------------------------------------------

SRM_ALPHA_DEFAULT: float = 0.01
"""Default significance threshold for the SRM chi-squared test.

Can be overridden per-experiment via ``ExperimentConfig.srm_alpha``
(allowed range: 0.001-0.1).  The global default of 0.01 is deliberately
stricter than the analysis alpha (typically 0.05) because SRM is a
data-integrity gate, not a directional hypothesis test.
"""

# Drift thresholds that drive the pass / warning / critical classification.
# These are *policy* choices, not statistical derivations -- they represent
# the minimum meaningful allocation imbalance worth surfacing to analysts.
#
# Classification matrix (evaluated top-to-bottom in _srm_severity):
#
#   p < alpha  AND  drift >= SRM_DRIFT_CRITICAL  ->  critical
#   p < alpha  AND  drift <  SRM_DRIFT_CRITICAL  ->  warning  (significant but small)
#   p >= alpha AND  drift >= SRM_DRIFT_WARNING   ->  warning  (visible but not significant)
#   p >= alpha AND  drift <  SRM_DRIFT_WARNING   ->  pass
#
# SRM_DRIFT_WARNING  (0.01): a 1 pp per-arm shift is detectable by eye and
#   worth monitoring even without statistical significance, especially in
#   high-traffic experiments where chi-squared power is very high.
#
# SRM_DRIFT_CRITICAL (0.02): a 2 pp per-arm shift combined with a significant
#   p-value indicates the assignment mechanism is unreliable.  Results from
#   such an experiment should not be used to make a ship decision without
#   root-cause investigation.
SRM_DRIFT_WARNING:  float = 0.01
SRM_DRIFT_CRITICAL: float = 0.02

# ---------------------------------------------------------------------------
# Stable issue codes for assignment QA
# ---------------------------------------------------------------------------

ISSUE_DUPLICATE_UNIT      = "DUPLICATE_UNIT_ASSIGNMENT"
ISSUE_CONFLICTING_VARIANT = "CONFLICTING_VARIANT_ASSIGNMENT"
ISSUE_UNPARSEABLE_TS      = "UNPARSEABLE_ASSIGNMENT_TIMESTAMP"
ISSUE_EXPERIMENT_ID_MISMATCH = "EXPERIMENT_ID_MISMATCH"

# Stable issue codes for metric QA
ISSUE_INVALID_PERIOD      = "INVALID_METRIC_PERIOD"
ISSUE_NONNUMERIC_VALUE    = "NONNUMERIC_METRIC_VALUE"
ISSUE_UNKNOWN_METRIC_NAME = "UNKNOWN_METRIC_NAME"
ISSUE_METRIC_JOIN_GAP     = "METRIC_JOIN_GAP"
ISSUE_ASSIGNMENT_JOIN_GAP = "ASSIGNMENT_JOIN_GAP"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _srm_severity(p_value: float, max_drift: float, alpha: float) -> SrmSeverity:
    """
    Classify SRM severity using the joint p-value + drift rule from 03-schemas.md.

    Rules (evaluated in priority order, using named policy constants):
      critical : p < alpha  AND drift >= SRM_DRIFT_CRITICAL (0.02)
      warning  : p < alpha  AND drift <  SRM_DRIFT_CRITICAL (0.02)  -- significant but small
      warning  : p >= alpha AND drift >= SRM_DRIFT_WARNING  (0.01)  -- visible, not significant
      pass     : p >= alpha AND drift <  SRM_DRIFT_WARNING  (0.01)
    """
    sig = p_value < alpha
    if sig and max_drift >= SRM_DRIFT_CRITICAL:
        return SrmSeverity.critical
    if sig and max_drift < SRM_DRIFT_CRITICAL:
        return SrmSeverity.warning
    if not sig and max_drift >= SRM_DRIFT_WARNING:
        return SrmSeverity.warning
    return SrmSeverity.pass_


def _srm_explanation(
    severity: SrmSeverity,
    observed_alloc: dict[str, float],
    expected_alloc: dict[str, float],
    max_drift: float,
    p_value: float,
    alpha: float,
) -> str:
    """Build a human-readable SRM explanation string."""
    obs_parts = ", ".join(
        f"{v}={f:.4f}" for v, f in sorted(observed_alloc.items())
    )
    exp_parts = ", ".join(
        f"{v}={f:.4f}" for v, f in sorted(expected_alloc.items())
    )
    base = (
        f"Observed allocation: {obs_parts}. "
        f"Expected allocation: {exp_parts}. "
        f"Max absolute drift: {max_drift:.4f}. "
        f"Chi-squared p-value: {p_value:.4f} (threshold: {alpha})."
    )
    if severity == SrmSeverity.pass_:
        return (
            f"SRM PASS: No evidence of sample ratio mismatch. {base} "
            "Assignment allocation is consistent with the planned split."
        )
    if severity == SrmSeverity.warning:
        if p_value < alpha:
            return (
                f"SRM WARNING: Statistically significant imbalance detected, "
                f"but drift is small (max_drift={max_drift:.4f} < 0.02). {base} "
                "Results can be used with caution. Investigate assignment mechanism."
            )
        return (
            f"SRM WARNING: Visible allocation drift detected (max_drift={max_drift:.4f} >= 0.01) "
            f"without reaching statistical significance. {base} "
            "Monitor closely if the experiment continues."
        )
    # critical
    return (
        f"SRM CRITICAL: Both statistical significance (p={p_value:.4f} < {alpha}) "
        f"and meaningful drift (max_drift={max_drift:.4f} >= 0.02) are present. {base} "
        "Results cannot be trusted without investigating and remediating the "
        "assignment mechanism. Do not ship based on this data."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_assignment_quality(
    assignments: pd.DataFrame,
    config: ExperimentConfig,
) -> QualityChecks:
    """
    Run all assignment-level QA checks and return a QualityChecks result.

    Checks performed (in order):
      1. Required column presence.
      2. experiment_id consistency with config.
      3. Timestamp parseability.
      4. Duplicate unit_id rows (same unit assigned more than once).
      5. Conflicting variant assignments (same unit, different variants).
      6. Unknown variant labels (not declared in config).
      7. SRM chi-squared test with joint drift classification.

    Args:
        assignments: DataFrame loaded from the assignment CSV. Must contain
                     at minimum the four required columns.
        config: The validated ExperimentConfig for this experiment.

    Returns:
        A :class:`~abkit_core.schemas.QualityChecks` object.  Only
        ``srm_check`` and ``balance_checks`` are populated here;
        ``join_integrity_checks`` requires the metric DataFrame and is
        filled by :func:`check_join_integrity`.
    """
    balance_issues: list[DiagnosticIssue] = []

    # 1. Required columns
    col_issues = validate_assignment_columns(list(assignments.columns))
    if col_issues:
        # Cannot proceed further without required columns
        return QualityChecks(balance_checks=col_issues)

    # 2. experiment_id consistency
    unique_ids = assignments["experiment_id"].dropna().unique()
    if len(unique_ids) > 1:
        balance_issues.append(DiagnosticIssue(
            code=ISSUE_EXPERIMENT_ID_MISMATCH,
            severity=IssueSeverity.error,
            message=(
                f"Assignment file contains multiple experiment_id values: "
                f"{sorted(str(x) for x in unique_ids)}. "
                "Expected only one experiment per file."
            ),
            field="experiment_id",
            details={"found_ids": sorted(str(x) for x in unique_ids)},
        ))

    # 3. Timestamp parseability (try coercing; flag rows that fail)
    ts_col = pd.to_datetime(assignments["assignment_ts"], errors="coerce")
    bad_ts_count = int(ts_col.isna().sum() - assignments["assignment_ts"].isna().sum())
    if bad_ts_count > 0:
        balance_issues.append(DiagnosticIssue(
            code=ISSUE_UNPARSEABLE_TS,
            severity=IssueSeverity.error,
            message=(
                f"{bad_ts_count} assignment row(s) have unparseable assignment_ts values."
            ),
            field="assignment_ts",
            details={"unparseable_count": bad_ts_count},
        ))

    # 4 & 5. Duplicate and conflicting assignments
    dup_issues = _check_duplicate_assignments(assignments)
    balance_issues.extend(dup_issues)

    # 6. Unknown variant labels
    observed_variants = assignments["variant"].dropna().unique().tolist()
    variant_issues = validate_variant_labels(observed_variants, config)
    balance_issues.extend(variant_issues)

    # 7. SRM check — only run on variants declared in config (skip unknowns)
    known_variants = set(config.variants)
    filtered = assignments[assignments["variant"].isin(known_variants)]
    srm = _run_srm_check(filtered, config)

    return QualityChecks(
        srm_check=srm,
        balance_checks=balance_issues,
    )


def check_metric_quality(
    metrics: pd.DataFrame,
    config: ExperimentConfig,
) -> list[DiagnosticIssue]:
    """
    Validate the metric DataFrame against schema and config expectations.

    Checks:
      1. Required column presence.
      2. ``period`` values are ``pre`` or ``post``.
      3. ``metric_value`` is numeric (coercible to float).
      4. ``metric_name`` values are known to the config (warning, not error,
         for extras -- the config may not enumerate all metrics).

    Return type — design note (v1):
        This function intentionally returns a flat ``list[DiagnosticIssue]``
        rather than a structured ``QualityChecks`` object.  The caller is
        responsible for slotting these issues into the appropriate field of
        ``QualityChecks`` (``missingness_checks`` for data-shape issues).
        Promoting the return type to a structured object before an orchestrating
        caller exists would be speculative.  Revisit when a ``run_quality_checks``
        function is written for prompt 4.

    Args:
        metrics: DataFrame loaded from the metric CSV.
        config: The validated ExperimentConfig.

    Returns:
        A list of DiagnosticIssue objects. Empty list means no problems found.
    """
    issues: list[DiagnosticIssue] = []

    # 1. Required columns
    col_issues = validate_metric_columns(list(metrics.columns))
    if col_issues:
        return col_issues

    # 2. Period values
    valid_periods = {"pre", "post"}
    invalid_periods = set(metrics["period"].dropna().unique()) - valid_periods
    if invalid_periods:
        issues.append(DiagnosticIssue(
            code=ISSUE_INVALID_PERIOD,
            severity=IssueSeverity.error,
            message=(
                f"metric period column contains unsupported values: "
                f"{sorted(invalid_periods)}. Allowed values: 'pre', 'post'."
            ),
            field="period",
            details={"invalid_periods": sorted(invalid_periods)},
        ))

    # 3. Numeric metric_value
    coerced = pd.to_numeric(metrics["metric_value"], errors="coerce")
    bad_count = int(coerced.isna().sum() - metrics["metric_value"].isna().sum())
    if bad_count > 0:
        issues.append(DiagnosticIssue(
            code=ISSUE_NONNUMERIC_VALUE,
            severity=IssueSeverity.error,
            message=(
                f"{bad_count} metric row(s) have non-numeric metric_value entries."
            ),
            field="metric_value",
            details={"nonnumeric_count": bad_count},
        ))

    # 4. Unknown metric names (warning only)
    declared_names: set[str] = {config.primary_metric}
    declared_names.update(m.name for m in config.secondary_metrics)
    declared_names.update(m.name for m in config.guardrail_metrics)

    observed_names = set(metrics["metric_name"].dropna().unique())
    unknown_names = observed_names - declared_names
    if unknown_names:
        issues.append(DiagnosticIssue(
            code=ISSUE_UNKNOWN_METRIC_NAME,
            severity=IssueSeverity.warning,
            message=(
                f"Metric file contains metric names not declared in the config: "
                f"{sorted(unknown_names)}. These will be carried through but not "
                "analysed as primary, secondary, or guardrail metrics."
            ),
            field="metric_name",
            details={"unknown_names": sorted(unknown_names)},
        ))

    return issues


def check_join_integrity(
    assignments: pd.DataFrame,
    metrics: pd.DataFrame,
    config: ExperimentConfig,
) -> list[DiagnosticIssue]:
    """
    Check join coverage between the assignment and metric DataFrames.

    Two gap directions are checked:
      - Assignment units with no post-period metric rows (metric join gap).
      - Metric units with no corresponding assignment row (assignment join gap).

    Both gaps are reported as warnings (not errors) because partial coverage
    is sometimes intentional (e.g. not all assigned users triggered the event
    being measured).

    Return type — design note (v1):
        Like ``check_metric_quality``, this returns a flat list.  The caller
        should place these issues into ``QualityChecks.join_integrity_checks``
        when assembling the full ``QualityChecks`` result for ``ResultPayload``.

    Args:
        assignments: Assignment DataFrame with at least ``unit_id`` column.
        metrics: Metric DataFrame with at least ``unit_id`` and ``period`` columns.
        config: The validated ExperimentConfig (used for context in messages).

    Returns:
        A list of DiagnosticIssue objects.
    """
    issues: list[DiagnosticIssue] = []

    assigned_units: set[str] = set(assignments["unit_id"].dropna().astype(str))

    post_metrics = metrics[metrics["period"] == "post"] if "period" in metrics.columns else metrics
    metric_units: set[str] = set(post_metrics["unit_id"].dropna().astype(str))

    # Assigned units missing from metrics
    unmatched_assigned = assigned_units - metric_units
    if unmatched_assigned:
        pct = len(unmatched_assigned) / len(assigned_units) * 100 if assigned_units else 0
        issues.append(DiagnosticIssue(
            code=ISSUE_METRIC_JOIN_GAP,
            severity=IssueSeverity.warning,
            message=(
                f"{len(unmatched_assigned)} assigned unit(s) ({pct:.1f}%) have no "
                f"post-period metric rows. This may indicate data pipeline issues or "
                f"intentional eligibility filtering."
            ),
            field="unit_id",
            details={
                "unmatched_count": len(unmatched_assigned),
                "total_assigned": len(assigned_units),
                "gap_pct": round(pct, 2),
            },
        ))

    # Metric units missing from assignments
    unmatched_metric = metric_units - assigned_units
    if unmatched_metric:
        pct = len(unmatched_metric) / len(metric_units) * 100 if metric_units else 0
        issues.append(DiagnosticIssue(
            code=ISSUE_ASSIGNMENT_JOIN_GAP,
            severity=IssueSeverity.warning,
            message=(
                f"{len(unmatched_metric)} metric unit(s) ({pct:.1f}%) have no "
                f"corresponding assignment row. Check for contamination or logging "
                f"issues in the metric pipeline."
            ),
            field="unit_id",
            details={
                "unmatched_count": len(unmatched_metric),
                "total_metric_units": len(metric_units),
                "gap_pct": round(pct, 2),
            },
        ))

    return issues


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _check_duplicate_assignments(assignments: pd.DataFrame) -> list[DiagnosticIssue]:
    """
    Detect units assigned more than once, and units with conflicting variants.

    A duplicate is when the same ``unit_id`` appears in more than one row.
    A conflict is when the same ``unit_id`` maps to different variant values.
    """
    issues: list[DiagnosticIssue] = []

    counts = assignments.groupby("unit_id").size()
    duplicated_units = counts[counts > 1].index.tolist()

    if not duplicated_units:
        return []

    # Separate pure duplicates (same unit, same variant) from conflicts
    variant_counts = assignments.groupby("unit_id")["variant"].nunique()
    conflict_units = variant_counts[variant_counts > 1].index.tolist()
    pure_dup_units = [u for u in duplicated_units if u not in conflict_units]

    if pure_dup_units:
        issues.append(DiagnosticIssue(
            code=ISSUE_DUPLICATE_UNIT,
            severity=IssueSeverity.warning,
            message=(
                f"{len(pure_dup_units)} unit(s) appear in multiple assignment rows "
                f"with the same variant. Deduplicate to avoid inflated sample counts. "
                f"First 5: {sorted(pure_dup_units)[:5]}."
            ),
            field="unit_id",
            details={
                "duplicate_count": len(pure_dup_units),
                "examples": sorted(pure_dup_units)[:5],
            },
        ))

    if conflict_units:
        issues.append(DiagnosticIssue(
            code=ISSUE_CONFLICTING_VARIANT,
            severity=IssueSeverity.error,
            message=(
                f"{len(conflict_units)} unit(s) are assigned to more than one variant. "
                f"This indicates a randomization or logging error. "
                f"First 5: {sorted(conflict_units)[:5]}."
            ),
            field="unit_id",
            details={
                "conflict_count": len(conflict_units),
                "examples": sorted(conflict_units)[:5],
            },
        ))

    return issues


def _run_srm_check(
    assignments: pd.DataFrame,
    config: ExperimentConfig,
) -> SrmCheck:
    """
    Run the SRM chi-squared test and return a fully populated SrmCheck.

    Uses the per-experiment ``srm_alpha`` if set; otherwise the global default.
    Severity is classified by the joint p-value + drift rule.
    """
    alpha_used: float = config.srm_alpha if config.srm_alpha is not None else SRM_ALPHA_DEFAULT

    total = len(assignments)
    observed_counts: dict[str, int] = {}
    expected_counts: dict[str, float] = {}
    observed_alloc:  dict[str, float] = {}
    expected_alloc:  dict[str, float] = {}

    variant_counts = assignments["variant"].value_counts()

    for variant in config.variants:
        obs = int(variant_counts.get(variant, 0))
        exp_frac = config.expected_allocation[variant]
        exp_n = total * exp_frac

        observed_counts[variant] = obs
        expected_counts[variant] = round(exp_n, 4)
        observed_alloc[variant]  = round(obs / total, 6) if total > 0 else 0.0
        expected_alloc[variant]  = exp_frac

    max_drift = max(
        abs(observed_alloc[v] - expected_alloc[v])
        for v in config.variants
    )

    # scipy chisquare expects arrays in the same order
    obs_arr = [observed_counts[v] for v in config.variants]
    exp_arr = [expected_counts[v] for v in config.variants]

    if total == 0 or sum(obs_arr) == 0:
        # No data: cannot compute chi-squared, return a neutral result
        chi2_stat, p_value = 0.0, 1.0
    else:
        result = chisquare(obs_arr, f_exp=exp_arr)
        chi2_stat = float(result.statistic)
        p_value = float(result.pvalue)
        # Guard against NaN from degenerate inputs
        if not math.isfinite(chi2_stat):
            chi2_stat, p_value = 0.0, 1.0

    severity = _srm_severity(p_value, max_drift, alpha_used)
    explanation = _srm_explanation(
        severity, observed_alloc, expected_alloc, max_drift, p_value, alpha_used
    )

    # Build structured DiagnosticIssue for the srm_check.issues list
    srm_issues: list[DiagnosticIssue] = []
    if severity == SrmSeverity.critical:
        srm_issues.append(DiagnosticIssue(
            code="SRM_CRITICAL",
            severity=IssueSeverity.error,
            message=explanation,
            field="variant",
            details={
                "chi2_stat": round(chi2_stat, 4),
                "p_value": round(p_value, 6),
                "max_absolute_drift": round(max_drift, 6),
                "srm_alpha_used": alpha_used,
            },
        ))
    elif severity == SrmSeverity.warning:
        srm_issues.append(DiagnosticIssue(
            code="SRM_WARNING",
            severity=IssueSeverity.warning,
            message=explanation,
            field="variant",
            details={
                "chi2_stat": round(chi2_stat, 4),
                "p_value": round(p_value, 6),
                "max_absolute_drift": round(max_drift, 6),
                "srm_alpha_used": alpha_used,
            },
        ))

    return SrmCheck(
        severity=severity,
        chi2_stat=round(chi2_stat, 4),
        p_value=round(p_value, 6),
        srm_alpha_used=alpha_used,
        observed_counts=observed_counts,
        expected_counts=expected_counts,
        observed_allocation=observed_alloc,
        expected_allocation=expected_alloc,
        max_absolute_drift=round(max_drift, 6),
        explanation=explanation,
        issues=srm_issues,
    )
