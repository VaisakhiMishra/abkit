"""
design.py — Experiment spec validation.

Responsibility: accept a parsed ExperimentConfig, run all business-rule
checks, and return a SpecValidation result.

No statistical computation lives here. No I/O. No Streamlit imports.

Guardrail direction validation
-------------------------------
The ``guardrail_directions`` field added in schema v1.1 is validated at the
Pydantic level (unknown metric names raise ValidationError at construction
time).  Design-quality warnings beyond that (e.g. guardrails that have no
declared direction) are intentionally omitted: undeclared directions fall back
gracefully to the legacy "any significant movement blocks" behaviour, so there
is nothing structurally wrong with omitting them.
"""

from __future__ import annotations

from abkit_core.schemas import (
    DiagnosticIssue,
    ExperimentConfig,
    IssueSeverity,
    SpecValidation,
    validate_config,
)

# Additional issue codes owned by design.py
ISSUE_SHORT_HYPOTHESIS = "SHORT_HYPOTHESIS"
ISSUE_NO_GUARDRAILS = "NO_GUARDRAILS"
ISSUE_MDE_TOO_LARGE = "MDE_IMPLAUSIBLY_LARGE"


def validate_experiment_spec(
    config: ExperimentConfig,
    *,
    for_analysis: bool = False,
) -> SpecValidation:
    """
    Validate a parsed ExperimentConfig and return a SpecValidation result.

    Runs the schema-level cross-field checks from ``schemas.validate_config``
    plus a small set of practical design-quality warnings.

    Args:
        config: A successfully parsed ExperimentConfig (Pydantic validation
                has already passed at construction time).
        for_analysis: When True, missing ``metric_type`` is promoted to an
                      error rather than silently accepted.

    Returns:
        A :class:`~abkit_core.schemas.SpecValidation` with ``is_valid=True``
        when no errors are present (warnings are allowed).
    """
    all_issues: list[DiagnosticIssue] = validate_config(config, for_analysis=for_analysis)

    # --- Design-quality warnings (don't block validity, but worth surfacing) ---

    # Policy: hypothesis strings shorter than 20 characters are almost always
    # copy-paste placeholders ("Improve CTR", "Test new flow", etc.).
    # 20 chars is a low bar — enough to filter noise, not enough to be prescriptive.
    if len(config.hypothesis.strip()) < 20:
        all_issues.append(DiagnosticIssue(
            code=ISSUE_SHORT_HYPOTHESIS,
            severity=IssueSeverity.warning,
            message=(
                f"hypothesis is very short ({len(config.hypothesis.strip())} chars). "
                "A useful hypothesis should describe the expected mechanism and direction."
            ),
            field="hypothesis",
        ))

    # No guardrail metrics is a design smell for production experiments
    if not config.guardrail_metrics:
        all_issues.append(DiagnosticIssue(
            code=ISSUE_NO_GUARDRAILS,
            severity=IssueSeverity.warning,
            message=(
                "No guardrail_metrics declared. "
                "Consider adding at least one guardrail to detect harmful side effects."
            ),
            field="guardrail_metrics",
        ))

    # Policy: MDE > 0.5 (50% relative lift) is implausible for most product metrics.
    # This threshold is intentionally generous — real experiments rarely target
    # lifts above 20%.  0.5 is chosen to catch only extreme cases where the
    # value is clearly a placeholder or entry error, not a tight quality gate.
    if config.mde > 0.5:
        all_issues.append(DiagnosticIssue(
            code=ISSUE_MDE_TOO_LARGE,
            severity=IssueSeverity.warning,
            message=(
                f"mde={config.mde} is unusually large. "
                "A realistic MDE for most metrics is under 0.20."
            ),
            field="mde",
        ))

    errors   = [i for i in all_issues if i.severity == IssueSeverity.error]
    warnings = [i for i in all_issues if i.severity == IssueSeverity.warning]

    return SpecValidation(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        normalized_config=config if len(errors) == 0 else None,
    )
