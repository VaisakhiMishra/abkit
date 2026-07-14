"""
variance.py — CUPED readiness assessment and variance-reduction helpers.

Responsibilities:
  - Compute matched pre/post coverage for a single metric.
  - Compute the Pearson pre-post correlation on matched units.
  - Classify CUPED readiness as recommended / optional / not_recommended.
  - Compute the OLS CUPED theta coefficient and return adjusted values.
  - Return a CupedReadiness schema object with full numeric metadata.

Statistical scope and assumptions
----------------------------------
1. CUPED uses ordinary-least-squares adjustment:
       Y_adj = Y_post - theta * (X_pre - mean(X_pre))
   where theta = Cov(X_pre, Y_post) / Var(X_pre), computed on ALL matched
   units (both variants pooled).  Pooling is correct under the standard CUPED
   assumption that the pre-period relationship is the same in both arms.

2. The adjustment is applied independently per unit, so variant means shift
   but the within-variant sample size is unchanged.

3. Variance-reduction estimate: r^2  (derived from the identity
   Var(Y_adj) = Var(Y_post) * (1 - r^2) under the linear model).
   This is an approximation; actual reduction depends on the realized
   theta and may differ slightly, especially in small samples.

4. Only post-period units that also have a matched pre-period row are included
   in the CUPED-adjusted analysis.  Units without a pre-period match are
   dropped from the CUPED estimate (but NOT from the raw estimate).
   The fraction dropped is reported as a caveat when material (> 20%).

5. No Bayesian inference, no sequential testing, no heterogeneous-treatment-
   effect adjustment, no covariate balancing beyond the pre-period value.

No Streamlit imports.  No I/O beyond accepting DataFrames.
"""

from __future__ import annotations

import math

import pandas as pd

from abkit_core.schemas import (
    CupedRecommendation,
    CupedReadiness,
)

# ---------------------------------------------------------------------------
# Named policy thresholds for CUPED readiness tiers (from 03-schemas.md).
# These are policy choices — change them only with a documented reason.
#
#   recommended    : coverage >= CUPED_COVERAGE_RECOMMENDED  AND r >= CUPED_R_RECOMMENDED
#   optional       : coverage >= CUPED_COVERAGE_OPTIONAL     AND r >= CUPED_R_OPTIONAL
#   not_recommended: any other combination
#
# Coverage thresholds express the minimum fraction of post-period units that
# must have a matched pre-period row for the adjustment to be stable.
# Correlation thresholds express the minimum linear relationship needed for
# CUPED to meaningfully reduce variance (below these values the adjustment
# may add noise rather than remove it).
# ---------------------------------------------------------------------------
CUPED_COVERAGE_RECOMMENDED: float = 0.80
CUPED_R_RECOMMENDED:        float = 0.30
CUPED_COVERAGE_OPTIONAL:    float = 0.50
CUPED_R_OPTIONAL:           float = 0.10

# Fraction of dropped units above which a coverage-loss caveat is appended
# to the CUPED-adjusted estimate.  This is not a hard stop; it surfaces a
# warning to the analyst.
CUPED_DROP_CAVEAT_THRESHOLD: float = 0.20


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def assess_cuped_readiness(
    metrics: pd.DataFrame,
    metric_name: str,
) -> CupedReadiness:
    """
    Assess whether pre-period data is sufficient for CUPED adjustment.

    Computes matched coverage and Pearson pre-post correlation for a single
    metric across all units (both variants pooled — pooling is valid because
    the pre-period relationship should be the same in both arms).

    Args:
        metrics: DataFrame with at minimum columns
                 ``unit_id``, ``metric_name``, ``metric_value``, ``period``.
                 The ``period`` column must contain ``'pre'`` and ``'post'``
                 values.  Rows for other metrics are silently ignored.
        metric_name: Canonical metric name to assess.

    Returns:
        A :class:`~abkit_core.schemas.CupedReadiness` with recommendation,
        numeric metadata, and a human-readable explanation.
    """
    post_rows = metrics[
        (metrics["metric_name"] == metric_name) & (metrics["period"] == "post")
    ][["unit_id", "metric_value"]].rename(columns={"metric_value": "post"})

    pre_rows = metrics[
        (metrics["metric_name"] == metric_name) & (metrics["period"] == "pre")
    ][["unit_id", "metric_value"]].rename(columns={"metric_value": "pre"})

    n_post = len(post_rows)

    if n_post == 0:
        # No post-period data at all — cannot assess.
        return _no_data_readiness(metric_name)

    matched = post_rows.merge(pre_rows, on="unit_id", how="inner").dropna()
    n_matched = len(matched)
    coverage = n_matched / n_post

    if n_matched < 3:
        # Fewer than 3 matched pairs cannot produce a stable correlation.
        return _insufficient_matched_readiness(metric_name, coverage, n_matched)

    r = _pearson_r(matched["pre"].tolist(), matched["post"].tolist())

    # Clamp to [-1, 1] to guard against floating-point overshoot in tiny samples.
    r = max(-1.0, min(1.0, r))

    # Variance reduction estimate: r^2  (see module docstring for derivation)
    variance_reduction = r ** 2

    recommendation, caveats = _classify_readiness(coverage, r, variance_reduction)

    explanation = (
        f"CUPED readiness for '{metric_name}': "
        f"matched_coverage={coverage:.3f} ({n_matched}/{n_post} post-period units "
        f"have a pre-period match), "
        f"pre_post_correlation={r:.4f}, "
        f"estimated_variance_reduction={variance_reduction:.4f} "
        f"(approximation: r\u00b2 under OLS adjustment). "
        f"Recommendation: {recommendation}."
    )

    return CupedReadiness(
        recommendation=recommendation,
        matched_coverage=round(coverage, 6),
        pre_post_correlation=round(r, 6),
        estimated_variance_reduction=round(variance_reduction, 6),
        explanation=explanation,
        caveats=caveats,
    )


def apply_cuped_adjustment(
    assignments: pd.DataFrame,
    metrics: pd.DataFrame,
    metric_name: str,
    control_variant: str = "control",
    treatment_variant: str = "treatment",
) -> tuple[pd.Series, pd.Series, float]:
    """
    Apply the OLS CUPED adjustment to post-period values for two variants.

    Theta is estimated on ALL matched units (pooled across variants).
    Only units that have both a pre-period and post-period row for the metric
    are included.  The adjusted values are returned as two separate Series
    (control, treatment) that can be passed directly to a two-sample t-test.

    Assumption: the pre-post relationship (theta) is the same in both arms.
    This holds under valid randomisation.  If you suspect treatment affects
    the pre-period covariate (e.g. carry-over effects), do not apply CUPED.

    Args:
        assignments: DataFrame with ``unit_id`` and ``variant`` columns.
        metrics: DataFrame with ``unit_id``, ``metric_name``,
                 ``metric_value``, and ``period`` columns.
        metric_name: Which metric to adjust.
        control_variant: Label of the control variant.
        treatment_variant: Label of the treatment variant.

    Returns:
        ``(ctrl_adj, treat_adj, theta)`` where
        ``ctrl_adj`` and ``treat_adj`` are pandas Series of adjusted values
        and ``theta`` is the OLS coefficient used.

    Raises:
        ValueError: If fewer than 3 matched units exist (adjustment unstable).
    """
    post_rows = metrics[
        (metrics["metric_name"] == metric_name) & (metrics["period"] == "post")
    ][["unit_id", "metric_value"]].rename(columns={"metric_value": "post"})

    pre_rows = metrics[
        (metrics["metric_name"] == metric_name) & (metrics["period"] == "pre")
    ][["unit_id", "metric_value"]].rename(columns={"metric_value": "pre"})

    matched = (
        post_rows
        .merge(pre_rows, on="unit_id", how="inner")
        .merge(assignments[["unit_id", "variant"]], on="unit_id", how="inner")
        .dropna(subset=["pre", "post"])
    )

    if len(matched) < 3:
        raise ValueError(
            f"Cannot apply CUPED for '{metric_name}': only {len(matched)} matched "
            "units found (need at least 3 for a stable OLS estimate)."
        )

    # Theta estimated on all matched units (pooled) — see module docstring.
    pre_mean  = matched["pre"].mean()
    pre_var   = matched["pre"].var(ddof=1)

    if pre_var == 0.0:
        raise ValueError(
            f"Cannot apply CUPED for '{metric_name}': pre-period variance is zero "
            "(all pre-period values are identical)."
        )

    cov = matched[["pre", "post"]].cov(ddof=1).iloc[0, 1]
    theta = cov / pre_var

    matched["post_adj"] = matched["post"] - theta * (matched["pre"] - pre_mean)

    ctrl_adj  = matched.loc[matched["variant"] == control_variant,   "post_adj"]
    treat_adj = matched.loc[matched["variant"] == treatment_variant, "post_adj"]

    return ctrl_adj, treat_adj, float(theta)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _pearson_r(x: list[float], y: list[float]) -> float:
    """Compute Pearson r from two equal-length lists. Returns 0.0 if degenerate."""
    n = len(x)
    if n < 2:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    den = math.sqrt(
        sum((xi - mx) ** 2 for xi in x) * sum((yi - my) ** 2 for yi in y)
    )
    return num / den if den > 0 else 0.0


def _classify_readiness(
    coverage: float,
    r: float,
    variance_reduction: float,
) -> tuple[CupedRecommendation, list[str]]:
    """
    Apply the tier rules from 03-schemas.md (using named policy constants).

    Returns (recommendation, caveats).
    """
    caveats: list[str] = []

    if coverage >= CUPED_COVERAGE_RECOMMENDED and r >= CUPED_R_RECOMMENDED:
        rec = CupedRecommendation.recommended
    elif coverage >= CUPED_COVERAGE_OPTIONAL and r >= CUPED_R_OPTIONAL:
        rec = CupedRecommendation.optional
    else:
        rec = CupedRecommendation.not_recommended

    if coverage < CUPED_COVERAGE_RECOMMENDED:
        caveats.append(
            f"matched_coverage={coverage:.3f} is below the 'recommended' threshold "
            f"({CUPED_COVERAGE_RECOMMENDED:.2f}). The CUPED estimate will be based on a "
            "partial subset of units; interpret with caution."
        )
    if r < CUPED_R_OPTIONAL:
        caveats.append(
            f"pre_post_correlation={r:.4f} is near zero or negative. "
            "CUPED adjustment is unlikely to reduce variance and may add noise."
        )
    elif r < CUPED_R_RECOMMENDED:
        caveats.append(
            f"pre_post_correlation={r:.4f} is modest. "
            "Variance reduction will be limited "
            f"(estimated_variance_reduction={variance_reduction:.4f})."
        )

    return rec, caveats


def _no_data_readiness(metric_name: str) -> CupedReadiness:
    """Return a not_recommended result when no post-period data exists."""
    return CupedReadiness(
        recommendation=CupedRecommendation.not_recommended,
        matched_coverage=0.0,
        pre_post_correlation=0.0,
        estimated_variance_reduction=0.0,
        explanation=(
            f"CUPED readiness for '{metric_name}': no post-period data found. "
            "Cannot assess readiness."
        ),
        caveats=["No post-period metric rows found for this metric."],
    )


def _insufficient_matched_readiness(
    metric_name: str,
    coverage: float,
    n_matched: int,
) -> CupedReadiness:
    """Return a not_recommended result when fewer than 3 matched pairs exist."""
    return CupedReadiness(
        recommendation=CupedRecommendation.not_recommended,
        matched_coverage=round(coverage, 6),
        pre_post_correlation=0.0,
        estimated_variance_reduction=0.0,
        explanation=(
            f"CUPED readiness for '{metric_name}': "
            f"only {n_matched} matched pre/post unit(s) found (need at least 3). "
            "Cannot compute a stable correlation."
        ),
        caveats=[
            f"Only {n_matched} matched pre/post unit(s) available "
            "(need at least 3 for a stable OLS estimate)."
        ],
    )
