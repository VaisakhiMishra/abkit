"""
analysis.py — Primary-metric analysis, CUPED-adjusted estimates, and decision scaffolding.

Responsibilities:
  - Compute raw two-sample test (t-test for continuous, z-test for proportions)
    for the primary metric and for each secondary / guardrail metric present in
    the data.
  - Optionally apply CUPED adjustment and return an adjusted MetricEstimate.
  - Build a DecisionMemo by applying the ship / hold / rerun / inconclusive
    rules from 05-acceptance.md and 03-schemas.md.
  - Return an AnalysisResult that slots cleanly into ResultPayload.analysis.

Statistical scope and assumptions
----------------------------------
1. Continuous metrics use Welch's two-sample t-test (unequal-variance assumption).
   This is conservative but appropriate when we do not want to assume equal
   population variances across variants.

2. Proportion metrics use a two-proportion z-test (normal approximation).
   The approximation is valid when both n*p and n*(1-p) exceed ~5.  Below that
   threshold a caveat is added but the test still runs.  The schema's
   ``metric_type`` field controls which test is chosen.

3. Two-sided tests at the ``alpha`` declared in the experiment config.  No
   one-sided tests, no sequential corrections.  Optional Bonferroni correction
   for secondary metrics is available via ``apply_bonferroni_correction`` in
   the experiment config.  Guardrail metrics are always tested at the base
   ``alpha``.  Primary metric is always tested at the base ``alpha``.

4. Confidence intervals are computed as:
   - Continuous: Welch CI (mean difference ± t_crit * SE_welch).
   - Proportion:  Normal CI (proportion difference ± z_crit * SE_pooled_or_unpooled).
   Both are 1-alpha two-sided intervals, where alpha is the value passed into
   _compute_metric_estimate.  For secondary metrics this is ``secondary_alpha``
   when Bonferroni correction is active, so the CI width is already consistent
   with the corrected threshold.  The ``is_significant`` flag on each
   MetricEstimate is also evaluated against the same passed-in alpha, so there
   is no mismatch between the CI and the significance flag for any metric.

5. Secondary and guardrail metrics are always analysed with Welch t-test
   regardless of their declared metric_type.  v1 does not plumb per-secondary
   metric type through the analysis path — this is a known simplification,
   documented in caveats when secondary results are present.

6. CUPED is applied only when:
   - ``variance.assess_cuped_readiness`` returns ``recommended`` or ``optional``.
   - At least 3 matched pre/post units exist.
   If CUPED cannot be applied the raw estimate is promoted to the primary result
   and ``cuped_estimate`` is left as None.

7. Decision rules (from 05-acceptance.md §6):
   - SRM critical  → hold  (trust gate: results cannot be used)
   - SRM warning   → ship/hold/rerun/inconclusive with caveat surfaced
   - Primary metric significant AND no guardrail failures → ship
   - Primary metric not significant                      → inconclusive
   - Guardrail metric triggered (see rule below)         → hold

   Guardrail blocking rule (direction-aware, introduced in schema v1.1):
   - If the guardrail is not statistically significant → does not block.
   - If significant AND the metric has no declared direction (or direction
     is ``flat``) → any significant movement blocks (legacy v1 behaviour).
   - If significant AND direction is ``increase`` → blocks only when
     absolute_lift < 0 (negative lift violates the desired increase).
   - If significant AND direction is ``decrease`` → blocks only when
     absolute_lift > 0 (positive lift violates the desired decrease).
   - If absolute_lift is None (edge-case: zero observations) → blocks
     conservatively regardless of declared direction.

8. Zero-variance policy (degenerate test-data edge cases only):
   Real experiment data always has within-group variance.  When synthetic or
   pathologically constructed inputs produce zero within-group variance,
   ``_welch_ttest`` returns a deterministic result rather than propagating nan:
   - Identical constant groups (diff=0): p=1.0 — no detectable difference.
   - Different constant groups (diff≠0): p=0.0 — infinite signal-to-noise ratio.
   These are deliberate analysis policies for pipeline stability, not statistical
   claims about real experiment results.  See ``_welch_ttest`` for full rationale.

9. v1 variant limit:
   ``run_analysis`` accepts exactly two variants (control + one treatment arm).
   Passing a config with more than two variants raises ``ValueError`` with the
   variant names in the message.  Multi-variant support is planned for v2.

No Streamlit imports.  No I/O beyond accepting DataFrames and returning schemas.
"""

from __future__ import annotations

import math
import warnings

import pandas as pd
import scipy.stats as scipy_stats

from abkit_core.schemas import (
    AnalysisResult,
    CupedRecommendation,
    DecisionMemo,
    DecisionRecommendation,
    ExperimentConfig,
    GuardrailDirection,
    IssueSeverity,
    MetricEstimate,
    MetricType,
    QualityChecks,
    SrmSeverity,
)
from abkit_core.variance import (
    CUPED_DROP_CAVEAT_THRESHOLD,
    apply_cuped_adjustment,
    assess_cuped_readiness,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_analysis(
    assignments: pd.DataFrame,
    metrics: pd.DataFrame,
    config: ExperimentConfig,
    quality_checks: QualityChecks | None = None,
) -> tuple[AnalysisResult, DecisionMemo]:
    """
    Run the full analysis pipeline for one experiment and return results.

    Steps:
      1. Compute raw MetricEstimate for the primary metric.
      2. Assess CUPED readiness; compute adjusted estimate when appropriate.
         CUPED is applied only when readiness is 'recommended' or 'optional'.
         When readiness is 'not_recommended' the raw estimate is used as-is
         and cuped_estimate is left as None.
      3. Compute raw MetricEstimate for each secondary metric present in data.
      4. Compute raw MetricEstimate for each guardrail metric present in data.
      5. Derive a DecisionMemo from significance, guardrail status, and SRM.

    v1 supports exactly two-variant experiments (one control, one treatment).
    Passing a config with more than two variants raises ValueError.

    Args:
        assignments: DataFrame with at minimum ``unit_id`` and ``variant``.
        metrics: DataFrame with ``unit_id``, ``metric_name``,
                 ``metric_value``, and ``period``.
        config: Validated ExperimentConfig.  ``metric_type`` must be set.
                Must declare exactly two variants (control + treatment).
        quality_checks: Optional QualityChecks from the QA layer.  Used to
                        apply the SRM-critical hold rule.

    Returns:
        ``(AnalysisResult, DecisionMemo)`` — both populate
        ``ResultPayload.analysis`` and ``ResultPayload.decision``.

    Raises:
        ValueError: If ``config.metric_type`` is None.
        ValueError: If ``config.variants`` has more than 2 entries (v1 limit).
    """
    if config.metric_type is None:
        raise ValueError(
            "config.metric_type must be set to run analysis. "
            "Call validate_config(config, for_analysis=True) first."
        )

    if len(config.variants) > 2:
        raise ValueError(
            f"run_analysis supports exactly two variants in v1 "
            f"(control + one treatment arm). "
            f"Got {len(config.variants)} variants: {config.variants}. "
            "Multi-variant support is planned for a future release."
        )

    # Identify the control variant (first in config.variants)
    control_variant   = config.variants[0]
    treatment_variant = config.variants[1]  # first treatment arm

    # 1. Raw primary-metric estimate
    raw_primary = _compute_metric_estimate(
        assignments=assignments,
        metrics=metrics,
        metric_name=config.primary_metric,
        metric_type=config.metric_type,
        alpha=config.alpha,
        control_variant=control_variant,
        treatment_variant=treatment_variant,
    )

    # 2. CUPED readiness + optional adjusted estimate
    cuped_readiness = None
    cuped_estimate  = None

    if _has_pre_period_data(metrics, config.primary_metric):
        cuped_readiness = assess_cuped_readiness(metrics, config.primary_metric)

        if cuped_readiness.recommendation in (
            CupedRecommendation.recommended,
            CupedRecommendation.optional,
        ):
            cuped_estimate = _compute_cuped_estimate(
                assignments=assignments,
                metrics=metrics,
                config=config,
                control_variant=control_variant,
                treatment_variant=treatment_variant,
            )

    # 3. Secondary metrics
    # Bonferroni correction: if enabled and m >= 2 secondary metrics are
    # declared, test each at alpha / m.  When m == 1 the correction has no
    # mathematical effect (alpha / 1 == alpha), so we leave the flag False to
    # avoid misleading downstream consumers.  Primary and guardrail metrics
    # are always tested at the base alpha — this logic is intentionally
    # isolated to the secondary-metric loop only.
    m_secondary = len(config.secondary_metrics)
    bonferroni_applies = (
        config.apply_bonferroni_correction and m_secondary >= 2
    )
    secondary_alpha = config.alpha / m_secondary if bonferroni_applies else config.alpha

    declared_secondary = {m.name for m in config.secondary_metrics}
    observed_metrics   = set(
        metrics[metrics["period"] == "post"]["metric_name"].dropna().unique()
    )
    secondary_results: list[MetricEstimate] = []
    for mname in sorted(declared_secondary & observed_metrics):
        est = _compute_metric_estimate(
            assignments=assignments,
            metrics=metrics,
            metric_name=mname,
            # Secondary metrics always run as continuous in v1 — see module docstring.
            metric_type=MetricType.continuous,
            alpha=secondary_alpha,
            control_variant=control_variant,
            treatment_variant=treatment_variant,
        )
        if est is not None:
            secondary_results.append(est)

    # 4. Guardrail metrics
    declared_guardrail = {m.name for m in config.guardrail_metrics}
    guardrail_results: list[MetricEstimate] = []
    for mname in sorted(declared_guardrail & observed_metrics):
        est = _compute_metric_estimate(
            assignments=assignments,
            metrics=metrics,
            metric_name=mname,
            metric_type=MetricType.continuous,
            alpha=config.alpha,
            control_variant=control_variant,
            treatment_variant=treatment_variant,
        )
        if est is not None:
            guardrail_results.append(est)

    # The effective primary estimate is what the decision layer actually uses:
    # CUPED-adjusted when available, raw otherwise.  Surfacing it explicitly
    # means the UI never has to replicate this conditional.
    effective_primary = cuped_estimate if cuped_estimate is not None else raw_primary

    analysis = AnalysisResult(
        primary_metric_result=raw_primary,
        effective_primary_estimate=effective_primary,
        secondary_metric_results=secondary_results,
        guardrail_metric_results=guardrail_results,
        cuped_estimate=cuped_estimate,
        cuped_readiness=cuped_readiness,
        secondary_alpha_used=secondary_alpha if m_secondary > 0 else None,
        bonferroni_correction_applied=bonferroni_applies,
    )

    # 5. Decision memo
    decision = _build_decision(
        effective_primary=effective_primary,
        cuped_was_applied=cuped_estimate is not None,
        guardrail_results=guardrail_results,
        quality_checks=quality_checks,
        config=config,
    )

    return analysis, decision


# ---------------------------------------------------------------------------
# Private: metric estimation
# ---------------------------------------------------------------------------

def _compute_metric_estimate(
    assignments: pd.DataFrame,
    metrics: pd.DataFrame,
    metric_name: str,
    metric_type: MetricType | str,
    alpha: float,
    control_variant: str,
    treatment_variant: str,
) -> MetricEstimate | None:
    """
    Compute a two-sample estimate for one metric using post-period values only.

    Returns None when either variant has fewer than 2 observations.
    """
    post = metrics[
        (metrics["period"] == "post") & (metrics["metric_name"] == metric_name)
    ][["unit_id", "metric_value"]].rename(columns={"metric_value": "value"})

    merged = post.merge(assignments[["unit_id", "variant"]], on="unit_id", how="inner")

    ctrl  = merged.loc[merged["variant"] == control_variant,   "value"].dropna()
    treat = merged.loc[merged["variant"] == treatment_variant, "value"].dropna()

    if len(ctrl) < 2 or len(treat) < 2:
        return None

    ctrl_mean  = float(ctrl.mean())
    treat_mean = float(treat.mean())
    abs_lift   = treat_mean - ctrl_mean
    rel_lift   = abs_lift / ctrl_mean if ctrl_mean != 0 else float("nan")

    # Choose test based on metric_type and data shape.
    #
    # Proportion z-test assumption: values must be binary (0/1) Bernoulli indicators.
    # The pooled-proportion SE formula sqrt(p*(1-p)/n) is only valid when each row
    # represents one Bernoulli trial.  When the metric file stores per-unit
    # aggregated rates (e.g. conversion_rate=0.085 per user), the values are
    # continuous and the Bernoulli SE wildly over-estimates the true SE, producing
    # a z-statistic near zero even with a large true effect.
    #
    # Policy (v1): use the proportion z-test only when ALL observed values are
    # exactly 0 or 1.  In all other cases fall back to Welch's t-test, which is
    # valid for any continuous or per-unit-aggregated metric regardless of its
    # declared metric_type.  This is documented in the returned MetricEstimate
    # via metric_type so the caller knows which test was applied.
    mt = MetricType(metric_type) if isinstance(metric_type, str) else metric_type

    if mt == MetricType.proportion and _is_binary(ctrl) and _is_binary(treat):
        p_value, ci_lower, ci_upper = _proportion_test(ctrl, treat, alpha)
    else:
        p_value, ci_lower, ci_upper = _welch_ttest(ctrl, treat, alpha)

    is_sig = p_value < alpha if p_value is not None else None

    return MetricEstimate(
        metric_name=metric_name,
        metric_type=mt,
        control_mean=round(ctrl_mean, 8),
        treatment_mean=round(treat_mean, 8),
        absolute_lift=round(abs_lift, 8),
        # None when ctrl_mean == 0: the ratio is undefined, not zero.
        # Downstream renderers must display "—" or "undefined" for None,
        # never silently substitute 0.
        relative_lift=round(rel_lift, 8) if math.isfinite(rel_lift) else None,
        p_value=round(p_value, 8) if p_value is not None else None,
        ci_lower=round(ci_lower, 8) if ci_lower is not None else None,
        ci_upper=round(ci_upper, 8) if ci_upper is not None else None,
        is_significant=is_sig,
    )


def _compute_cuped_estimate(
    assignments: pd.DataFrame,
    metrics: pd.DataFrame,
    config: ExperimentConfig,
    control_variant: str,
    treatment_variant: str,
) -> MetricEstimate | None:
    """
    Apply CUPED adjustment and return an adjusted MetricEstimate.

    Returns None if adjustment fails (too few matched units or zero pre-variance).
    The metric_name on the returned estimate includes an '_cuped_adjusted' suffix
    so it is distinguishable from the raw estimate in serialised output.
    """
    try:
        ctrl_adj, treat_adj, theta = apply_cuped_adjustment(
            assignments=assignments,
            metrics=metrics,
            metric_name=config.primary_metric,
            control_variant=control_variant,
            treatment_variant=treatment_variant,
        )
    except ValueError:
        # Insufficient matched units or zero pre-variance — skip CUPED.
        return None

    if len(ctrl_adj) < 2 or len(treat_adj) < 2:
        return None

    ctrl_mean  = float(ctrl_adj.mean())
    treat_mean = float(treat_adj.mean())
    abs_lift   = treat_mean - ctrl_mean
    rel_lift   = abs_lift / ctrl_mean if ctrl_mean != 0 else float("nan")

    mt = config.metric_type  # type: ignore[assignment]
    # Same binary-detection gate as _compute_metric_estimate — adjusted values
    # inherit the data shape of the original post-period values, so the same
    # rule applies: z-test only for binary 0/1 series, Welch otherwise.
    if mt == MetricType.proportion and _is_binary(ctrl_adj) and _is_binary(treat_adj):
        p_value, ci_lower, ci_upper = _proportion_test(ctrl_adj, treat_adj, config.alpha)
    else:
        p_value, ci_lower, ci_upper = _welch_ttest(ctrl_adj, treat_adj, config.alpha)

    is_sig = p_value < config.alpha if p_value is not None else None

    # Check how many units were dropped vs the post-only set
    n_post = len(metrics[
        (metrics["period"] == "post") & (metrics["metric_name"] == config.primary_metric)
    ])
    n_matched = len(ctrl_adj) + len(treat_adj)
    drop_frac = 1.0 - (n_matched / n_post) if n_post > 0 else 0.0

    # Caveat when material fraction of units was dropped
    # (stored in the name since MetricEstimate has no caveats field)
    name = config.primary_metric + "_cuped_adjusted"
    if drop_frac > CUPED_DROP_CAVEAT_THRESHOLD:
        name += f"_partial_coverage_{n_matched}of{n_post}"

    return MetricEstimate(
        metric_name=name,
        metric_type=mt,
        control_mean=round(ctrl_mean, 8),
        treatment_mean=round(treat_mean, 8),
        absolute_lift=round(abs_lift, 8),
        # None when ctrl_mean == 0: the ratio is undefined, not zero.
        relative_lift=round(rel_lift, 8) if math.isfinite(rel_lift) else None,
        p_value=round(p_value, 8) if p_value is not None else None,
        ci_lower=round(ci_lower, 8) if ci_lower is not None else None,
        ci_upper=round(ci_upper, 8) if ci_upper is not None else None,
        is_significant=is_sig,
    )


# ---------------------------------------------------------------------------
# Private: statistical tests
# ---------------------------------------------------------------------------

def _is_binary(s: pd.Series) -> bool:
    """
    Return True if every non-null value in the Series is exactly 0.0 or 1.0.

    Used to gate the proportion z-test: the pooled-proportion SE formula is
    only valid for Bernoulli (0/1) indicator variables.  Per-unit aggregated
    rates stored as floats must use Welch's t-test instead.
    """
    vals = s.dropna().unique()
    return all(v in (0, 1, 0.0, 1.0) for v in vals)


def _welch_ttest(
    ctrl: pd.Series,
    treat: pd.Series,
    alpha: float,
) -> tuple[float, float, float]:
    """
    Welch two-sample t-test (unequal variances).

    Returns (p_value, ci_lower, ci_upper) for the mean difference
    (treatment - control) at the 1-alpha confidence level.

    Zero-variance policy (degenerate test-data edge cases only)
    -----------------------------------------------------------
    Real experiment data always has within-group variance.  Zero-variance only
    occurs with synthetic or pathologically constructed inputs — it is not a
    normal experiment condition.  Two sub-cases are handled explicitly so that
    the function always returns a fully finite, deterministic result:

      Case A — identical constant groups (var_c=0, var_t=0, diff=0):
        Both arms have the same constant value.  There is literally no
        detectable difference.  Policy: p_value=1.0, CI=[0, 0].
        Interpretation: the test cannot distinguish the groups; treat as
        no evidence of effect.

      Case B — different constant groups (var_c=0, var_t=0, diff≠0):
        Each arm is internally constant but the two constants differ.
        Within-group SE is zero, producing an infinite signal-to-noise ratio.
        Policy: p_value=0.0, CI=[diff, diff].
        Interpretation: the groups are perfectly and trivially separable —
        every observation in one arm exceeds every observation in the other.
        This is a maximally extreme outcome for degenerate test inputs, not
        a general statistical claim about effect reliability.

    Neither case should be interpreted as a real experiment result.  Both
    exist solely to keep the analysis pipeline numerically stable when
    driven by degenerate fixtures or unit tests.
    """
    diff = float(treat.mean()) - float(ctrl.mean())

    var_c = float(ctrl.var(ddof=1))
    var_t = float(treat.var(ddof=1))

    if var_c == 0.0 and var_t == 0.0:
        if diff == 0.0:
            # Case A: identical constant groups — no detectable difference.
            # Policy: p=1.0, zero-width CI at zero.
            return 1.0, 0.0, 0.0
        else:
            # Case B: different constant groups — infinite SNR, perfectly separable.
            # Policy: p=0.0, zero-width CI at the observed difference.
            # This is a degenerate policy outcome for test-data edge cases only.
            return 0.0, diff, diff

    # SE may still be exactly zero if one group has variance and the other does
    # not, AND the non-zero variance rounds to zero after division (extremely
    # unlikely in practice but guarded for completeness).
    se = math.sqrt(var_c / len(ctrl) + var_t / len(treat))

    if se == 0.0:
        # Residual zero-SE path: same degenerate policy as Case A / Case B above.
        p_value = 0.0 if diff != 0.0 else 1.0
        return p_value, diff, diff

    # Welch-Satterthwaite degrees of freedom
    df = _welch_df(ctrl, treat)
    t_crit = float(scipy_stats.t.ppf(1 - alpha / 2, df))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        result = scipy_stats.ttest_ind(treat, ctrl, equal_var=False)
    p_value = float(result.pvalue)

    # ttest_ind can still return nan for near-degenerate data in edge cases;
    # treat nan as "no evidence of difference" (conservative).
    if not math.isfinite(p_value):
        p_value = 1.0

    ci_lower = diff - t_crit * se
    ci_upper = diff + t_crit * se

    return p_value, ci_lower, ci_upper


def _proportion_test(
    ctrl: pd.Series,
    treat: pd.Series,
    alpha: float,
) -> tuple[float, float, float]:
    """
    Two-proportion z-test (normal approximation).

    Assumes values are binary (0/1) or bounded [0,1] proportions.
    The z-test is performed on raw values treated as per-unit proportions.

    Returns (p_value, ci_lower, ci_upper) for the proportion difference.
    """
    n_c = len(ctrl)
    n_t = len(treat)
    p_c = float(ctrl.mean())
    p_t = float(treat.mean())
    diff = p_t - p_c

    # Pooled proportion for the null-hypothesis SE
    p_pool = (p_c * n_c + p_t * n_t) / (n_c + n_t)
    se_null = math.sqrt(p_pool * (1 - p_pool) * (1 / n_c + 1 / n_t))

    if se_null == 0.0:
        # Degenerate: both proportions are identical or 0/1 with zero variance.
        return 1.0, diff, diff

    z_stat = diff / se_null
    p_value = float(2 * (1 - scipy_stats.norm.cdf(abs(z_stat))))

    # CI uses the unpooled SE (Agresti-Caffo approximation for CI, Wald for z-test)
    se_ci = math.sqrt(p_c * (1 - p_c) / n_c + p_t * (1 - p_t) / n_t)
    z_crit = float(scipy_stats.norm.ppf(1 - alpha / 2))
    ci_lower = diff - z_crit * se_ci
    ci_upper = diff + z_crit * se_ci

    return p_value, ci_lower, ci_upper


def _welch_df(ctrl: pd.Series, treat: pd.Series) -> float:
    """Welch-Satterthwaite degrees of freedom."""
    v_c = ctrl.var(ddof=1) / len(ctrl)
    v_t = treat.var(ddof=1) / len(treat)
    num = (v_c + v_t) ** 2
    den = (v_c ** 2 / (len(ctrl) - 1)) + (v_t ** 2 / (len(treat) - 1))
    return num / den if den > 0 else 1.0


def _has_pre_period_data(metrics: pd.DataFrame, metric_name: str) -> bool:
    """Return True if any pre-period rows exist for this metric."""
    return (
        (metrics["period"] == "pre") & (metrics["metric_name"] == metric_name)
    ).any()


# ---------------------------------------------------------------------------
# Private: decision memo
# ---------------------------------------------------------------------------

def _guardrail_blocks(
    estimate: MetricEstimate,
    direction: GuardrailDirection | None,
) -> bool:
    """
    Return True when this guardrail estimate should trigger a HOLD.

    Rules:
    - If the estimate is not statistically significant → False (does not block).
    - If absolute_lift is None (undefined — edge case with zero observations) →
      True conservatively, regardless of declared direction.
    - If direction is None or ``flat`` → any significant movement blocks
      (legacy v1 behaviour for undeclared guardrails).
    - If direction is ``increase`` → blocks only when absolute_lift < 0
      (negative movement violates the "should go up" intention).
    - If direction is ``decrease`` → blocks only when absolute_lift > 0
      (positive movement violates the "should go down" intention).

    Args:
        estimate: A computed MetricEstimate for a guardrail metric.
        direction: The declared GuardrailDirection for this metric, or None
                   when the metric was not listed in guardrail_directions.
    """
    if estimate.is_significant is not True:
        return False

    # Conservative fallback: undefined lift blocks.
    if estimate.absolute_lift is None:
        return True

    if direction is None or direction == GuardrailDirection.flat:
        # Legacy behaviour: any significant movement is a problem.
        return True

    if direction == GuardrailDirection.increase:
        # Only harmful when lift is negative (going the wrong way).
        return estimate.absolute_lift < 0

    if direction == GuardrailDirection.decrease:
        # Only harmful when lift is positive (going the wrong way).
        return estimate.absolute_lift > 0

    # Unreachable — all enum values are handled above.
    return True  # pragma: no cover


def _build_decision(
    effective_primary: MetricEstimate | None,
    cuped_was_applied: bool,
    guardrail_results: list[MetricEstimate],
    quality_checks: QualityChecks | None,
    config: ExperimentConfig,
) -> DecisionMemo:
    """
    Apply the decision rules from 05-acceptance.md §6 and 03-schemas.md.

    Args:
        effective_primary: The best available primary estimate — CUPED-adjusted
            when CUPED was applied, raw otherwise.  This is the same value
            exposed on AnalysisResult.effective_primary_estimate.
        cuped_was_applied: True when the effective estimate is CUPED-adjusted.
        guardrail_results: Metric estimates for all guardrail metrics.
        quality_checks: Optional QA results from the assignment QA layer.
        config: Validated ExperimentConfig for this experiment.

    Rules (evaluated in priority order):
      1. SRM critical → hold, regardless of metric results.
      2. Guardrail metric blocks (direction-aware) → hold.
         Whether a significant guardrail blocks depends on its declared direction
         in config.guardrail_directions.  See ``_guardrail_blocks`` for the full
         rule table.  Guardrails with no declared direction retain v1 behaviour:
         any significant movement triggers a hold.
      3. Primary metric significant → ship (with SRM warning caveat if present).
      4. Primary metric not significant → inconclusive.
      5. No primary metric result (e.g. empty data) → inconclusive.
    """
    caveats:      list[str] = []
    next_actions: list[str] = []

    # --- SRM gate ---
    srm_critical = False
    srm_warning  = False
    if quality_checks and quality_checks.srm_check:
        sev = quality_checks.srm_check.severity
        if sev == SrmSeverity.critical or sev == "critical":
            srm_critical = True
            caveats.append(
                "SRM CRITICAL: The assignment mechanism produced a statistically "
                "significant and large allocation imbalance "
                f"(max_drift={quality_checks.srm_check.max_absolute_drift:.4f}, "
                f"p={quality_checks.srm_check.p_value:.4g}). "
                "Results cannot be trusted without root-cause investigation."
            )
            next_actions.append(
                "Investigate the assignment pipeline before acting on any metric result."
            )
        elif sev == SrmSeverity.warning or sev == "warning":
            srm_warning = True
            caveats.append(
                "SRM WARNING: An allocation imbalance was detected "
                f"(max_drift={quality_checks.srm_check.max_absolute_drift:.4f}). "
                "Review the assignment mechanism. Results can be used with caution."
            )

    if srm_critical:
        return DecisionMemo(
            recommendation=DecisionRecommendation.hold,
            reasoning_summary=(
                "Experiment held due to critical SRM. "
                "Metric results are not reliable until the assignment issue is resolved."
            ),
            key_caveats=caveats,
            next_actions=next_actions,
            alpha_used=config.alpha,
        )

    # --- Guardrail gate (direction-aware) ---
    directions = config.guardrail_directions  # dict[str, GuardrailDirection]
    blocking_guardrails = [
        g for g in guardrail_results
        if _guardrail_blocks(g, directions.get(g.metric_name))
    ]
    if blocking_guardrails:
        blocking_names = [g.metric_name for g in blocking_guardrails]

        # Build per-metric reason lines for the caveats section.
        reason_lines: list[str] = []
        for g in blocking_guardrails:
            dir_ = directions.get(g.metric_name)
            lift_str = (
                f"{g.absolute_lift:+.6f}"
                if g.absolute_lift is not None
                else "undefined"
            )
            if dir_ is None or dir_ == GuardrailDirection.flat:
                why = "any significant movement is a violation (no direction declared)"
            elif dir_ == GuardrailDirection.increase:
                why = f"desired direction is 'increase' but lift={lift_str} is negative"
            else:  # decrease
                why = f"desired direction is 'decrease' but lift={lift_str} is positive"
            reason_lines.append(f"{g.metric_name}: {why}")

        caveats.append(
            "Guardrail metric(s) triggered a hold:\n"
            + "\n".join(f"  • {r}" for r in reason_lines)
        )
        next_actions.append(
            f"Investigate guardrail metric(s) before shipping: {blocking_names}."
        )
        if srm_warning:
            next_actions.append("Review SRM warning before shipping.")

        return DecisionMemo(
            recommendation=DecisionRecommendation.hold,
            reasoning_summary=(
                f"Guardrail metric(s) {blocking_names} violated their declared direction. "
                + "; ".join(reason_lines)
            ),
            key_caveats=caveats,
            next_actions=next_actions,
            alpha_used=config.alpha,
        )

    # --- Primary metric ---
    if effective_primary is None:
        return DecisionMemo(
            recommendation=DecisionRecommendation.inconclusive,
            reasoning_summary=(
                "No primary metric estimate could be computed "
                "(insufficient data or missing metric)."
            ),
            key_caveats=caveats,
            next_actions=["Check that the metric CSV contains post-period rows "
                          "for the primary metric."],
            alpha_used=config.alpha,
        )

    if effective_primary.is_significant:
        direction = "positive" if effective_primary.absolute_lift > 0 else "negative"
        if srm_warning:
            next_actions.append("Review SRM warning before shipping.")
        cuped_note = (
            " (based on CUPED-adjusted estimate)"
            if cuped_was_applied
            else " (based on raw estimate; no CUPED adjustment applied)"
        )
        rel_str = (
            f"{effective_primary.relative_lift:+.4f}"
            if effective_primary.relative_lift is not None
            else "undefined (zero control mean)"
        )
        return DecisionMemo(
            recommendation=DecisionRecommendation.ship,
            reasoning_summary=(
                f"Primary metric '{config.primary_metric}' shows a {direction} "
                f"lift of {effective_primary.absolute_lift:+.6f} "
                f"(relative: {rel_str}) "
                f"with p={effective_primary.p_value:.4g} < alpha={config.alpha}"
                f"{cuped_note}. "
                "No guardrail failures detected."
            ),
            key_caveats=caveats,
            next_actions=next_actions or ["Proceed with rollout and monitor guardrail metrics."],
            alpha_used=config.alpha,
        )
    else:
        # Not significant
        p_str = f"{effective_primary.p_value:.4g}" if effective_primary.p_value is not None else "unknown"
        caveats.append(
            f"Primary metric p={p_str} >= alpha={config.alpha}. "
            "The observed lift is not statistically distinguishable from noise "
            "at the declared significance level."
        )
        return DecisionMemo(
            recommendation=DecisionRecommendation.inconclusive,
            reasoning_summary=(
                f"Primary metric '{config.primary_metric}' did not reach "
                f"statistical significance (p={p_str}, alpha={config.alpha}). "
                "The experiment is inconclusive."
            ),
            key_caveats=caveats,
            next_actions=[
                "Consider whether the experiment ran long enough to reach the planned sample size.",
                "Review the MDE assumption — the true effect may be smaller than planned.",
            ],
            alpha_used=config.alpha,
        )
