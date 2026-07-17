"""
pages/3_Analysis.py — Statistical analysis: raw vs CUPED estimates, decision memo.

Responsibilities:
  - Call abkit_core.run_analysis with loaded assignments, metrics, config, quality_checks.
  - Display primary metric raw and CUPED-adjusted estimates.
  - Display secondary and guardrail metric results.
  - Display the DecisionMemo recommendation.
  - Store analysis_result, decision_memo, result_payload in session state.

No statistical logic lives in this file. All estimates, significance flags,
and the decision recommendation come directly from abkit-core and are rendered
as-is from the returned objects.
"""
from __future__ import annotations

import sys
import os
import uuid
from datetime import datetime, timezone

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from components.issue_display import render_issues  # noqa: E402
from components.metric_table import render_metric_estimate, render_metric_estimates_table  # noqa: E402

import abkit_core  # noqa: E402
from abkit_core.schemas import RunStatus, SpecValidation  # noqa: E402

st.set_page_config(page_title="Analysis — abkit", layout="wide")

# ---------------------------------------------------------------------------
# Sidebar: consistent workflow status across all pages
# ---------------------------------------------------------------------------
def _sidebar_status() -> None:
    with st.sidebar:
        st.markdown(
            """
            <style>
            [data-testid="stSidebarNav"] a span,
            [data-testid="stSidebarNav"] li a,
            [data-testid="stSidebarNav"] a {
                text-transform: uppercase;
                font-weight: 600;
                letter-spacing: 0.04em;
            }
            [data-testid="stSidebarNav"] a[aria-current="page"] span,
            [data-testid="stSidebarNav"] a[aria-current="page"] {
                font-weight: 700;
            }
            [data-testid="stSidebar"] hr {
                margin-top: 0.3rem;
                margin-bottom: 0.3rem;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        st.title("🧪 ABKIT")
        st.caption("A/B Testing Productivity Kit")
        st.markdown(
            "<div style='font-size:0.85rem;line-height:1.55;color:#444;margin-top:0.25rem;'>"
            "ABKIT guides you through a complete A/B experiment — from uploading your "
            "config and running data quality checks, through statistical analysis and "
            "variance reduction, to a clear ship-or-hold decision and a shareable memo."
            "</div>",
            unsafe_allow_html=True,
        )
        st.divider()

        def _row(key: str, label: str) -> str:
            icon = "✅" if st.session_state.get(key) is not None else "○"
            return f"<li style='margin:0;padding:1px 0;'>{icon}&nbsp;{label}</li>"

        st.markdown("**Workflow Status**", unsafe_allow_html=False)
        st.markdown(
            "<ul style='list-style:none;margin:0.2rem 0 0 0;padding:0;line-height:1.4;'>"
            + _row("config",          "Config loaded")
            + _row("assignments_df",  "Assignments loaded")
            + _row("metrics_df",      "Metrics loaded")
            + _row("spec_validation", "Spec validated")
            + _row("quality_checks",  "QA run")
            + _row("analysis_result", "Analysis run")
            + _row("result_payload",  "Result ready")
            + "</ul>",
            unsafe_allow_html=True,
        )
        st.divider()

        DEFAULTS_RESET = {
            "config_bytes": None, "config_filename": None,
            "assignment_filename": None, "metric_filename": None,
            "config": None, "assignments_df": None, "metrics_df": None,
            "spec_validation": None, "quality_checks": None,
            "metric_issues": None, "join_issues": None,
            "analysis_result": None, "decision_memo": None,
            "duration_plan": None, "result_payload": None,
        }
        if st.button("🔄 Reset everything", use_container_width=True):
            for k, v in DEFAULTS_RESET.items():
                st.session_state[k] = v
            st.rerun()

        st.divider()
        st.caption(
            "Pages: **SETUP → QA → ANALYSIS → MEMO**  \n"
            "Navigate using the sidebar above."
        )

_sidebar_status()

st.title("3 · Analysis")
st.caption("Run statistical analysis and view primary metric results, CUPED estimates, and the decision.")

# ---------------------------------------------------------------------------
# Guard: require config and QA data
# ---------------------------------------------------------------------------
cfg: abkit_core.ExperimentConfig | None = st.session_state.get("config")
assignments_df = st.session_state.get("assignments_df")
metrics_df     = st.session_state.get("metrics_df")
quality_checks = st.session_state.get("quality_checks")

missing = []
if cfg is None:
    missing.append("config (Setup page)")
if assignments_df is None:
    missing.append("assignment CSV (QA page)")
if metrics_df is None:
    missing.append("metric CSV (QA page)")

if missing:
    st.warning(f"⚠️  Missing required inputs: {', '.join(missing)}.")
    st.stop()

# Gap 9: surface multi-variant limit before the button is even shown.
if cfg is not None and len(cfg.variants) > 2:
    st.error(
        f"⛔ **v1 limit:** this config declares {len(cfg.variants)} variants "
        f"({', '.join(cfg.variants)}). "
        "Analysis supports exactly **2 variants** in v1. "
        "Return to **1 · Setup** and load a 2-variant config."
    )
    st.stop()

# Gap 7: show which files are loaded.
a_fname = st.session_state.get("assignment_filename") or "unknown file"
m_fname = st.session_state.get("metric_filename") or "unknown file"
st.info(
    f"Analysing **{cfg.experiment_id}** — "
    f"primary metric: **{cfg.primary_metric}** ({cfg.metric_type or 'type not set'})  \n"
    f"Assignment: `{a_fname}`  ·  Metrics: `{m_fname}`"
)

# ---------------------------------------------------------------------------
# SRM gate warning banner
# ---------------------------------------------------------------------------
if quality_checks and quality_checks.srm_check:
    sev = str(quality_checks.srm_check.severity)
    if sev == "critical":
        st.error(
            "🔴 **SRM CRITICAL** — assignment allocation is unreliable. "
            "The analysis will run, but the decision will be blocked to **hold**."
        )
    elif sev == "warning":
        st.warning(
            "🟡 **SRM WARNING** — allocation drift detected. "
            "Results are available but should be interpreted with caution."
        )

# ---------------------------------------------------------------------------
# Run button
# ---------------------------------------------------------------------------
run_analysis = st.button(
    "▶ Run analysis",
    type="primary",
    use_container_width=True,
    disabled=(cfg.metric_type is None),
)
if cfg.metric_type is None:
    st.error("metric_type is not set in the config. Update the config and reload Setup.")
    st.stop()

# ---------------------------------------------------------------------------
# Execute or restore from session state
# ---------------------------------------------------------------------------
analysis_result = st.session_state.get("analysis_result")
decision_memo   = st.session_state.get("decision_memo")

if run_analysis:
    with st.spinner("Running analysis…"):
        try:
            analysis_result, decision_memo = abkit_core.run_analysis(
                assignments=assignments_df,
                metrics=metrics_df,
                config=cfg,
                quality_checks=quality_checks,
            )
        except Exception as exc:
            st.error(f"Analysis failed: {exc}")
            st.stop()

    # Build the canonical ResultPayload.
    # Gap 2: RunStatus derivation is UI-assembly logic, not statistical logic.
    # It is kept here because it assembles schema objects from schema objects —
    # no formulas or significance checks are recomputed.
    spec_validation: SpecValidation = st.session_state.get("spec_validation") or SpecValidation(
        is_valid=True,
        normalized_config=cfg,
    )
    duration_plan = st.session_state.get("duration_plan")
    join_issues   = st.session_state.get("join_issues") or []
    metric_issues = st.session_state.get("metric_issues") or []

    full_qc = abkit_core.QualityChecks(
        srm_check=(quality_checks.srm_check if quality_checks else None),
        balance_checks=(quality_checks.balance_checks if quality_checks else []),
        missingness_checks=metric_issues,
        join_integrity_checks=join_issues,
    )

    # Status rules (schema-level assembly, not statistical computation):
    #   failed  → spec errors or critical SRM
    #   warning → SRM warning, metric issues, or join issues
    #   success → everything else
    has_spec_errors = not spec_validation.is_valid
    srm_sev = str(full_qc.srm_check.severity) if full_qc.srm_check else "pass"
    if has_spec_errors or srm_sev == "critical":
        status = RunStatus.failed
    elif srm_sev == "warning" or metric_issues or join_issues:
        status = RunStatus.warning
    else:
        status = RunStatus.success

    result_payload = abkit_core.ResultPayload(
        experiment_id=cfg.experiment_id,
        run_id=str(uuid.uuid4()),
        status=status,
        generated_at=datetime.now(timezone.utc),
        spec_validation=spec_validation,
        duration_plan=duration_plan,
        quality_checks=full_qc,
        analysis=analysis_result,
        decision=decision_memo,
    )

    st.session_state["analysis_result"] = analysis_result
    st.session_state["decision_memo"]   = decision_memo
    st.session_state["result_payload"]  = result_payload

if analysis_result is None:
    st.info("Click **Run analysis** to compute results.")
    st.stop()

# ---------------------------------------------------------------------------
# Decision banner
# ---------------------------------------------------------------------------
st.subheader("Decision")
rec = str(decision_memo.recommendation) if decision_memo else "—"

DECISION_STYLE = {
    "ship":         ("✅", "success"),
    "hold":         ("🔴", "error"),
    "rerun":        ("🔄", "warning"),
    "inconclusive": ("⬜", "info"),
}
icon, style = DECISION_STYLE.get(rec, ("—", "info"))

if style == "success":
    st.success(f"{icon} **{rec.upper()}** — {decision_memo.reasoning_summary}")
elif style == "error":
    st.error(f"{icon} **{rec.upper()}** — {decision_memo.reasoning_summary}")
elif style == "warning":
    st.warning(f"{icon} **{rec.upper()}** — {decision_memo.reasoning_summary}")
else:
    st.info(f"{icon} **{rec.upper()}** — {decision_memo.reasoning_summary}")

if decision_memo:
    if decision_memo.key_caveats:
        with st.expander("Caveats", expanded=(rec in ("hold",))):
            for cav in decision_memo.key_caveats:
                st.caption(f"▸ {cav}")
    if decision_memo.next_actions:
        with st.expander("Next actions"):
            for action in decision_memo.next_actions:
                st.markdown(f"- {action}")

# ---------------------------------------------------------------------------
# Primary metric estimates
# ---------------------------------------------------------------------------
st.subheader("Primary metric — " + cfg.primary_metric)

raw = analysis_result.primary_metric_result
cuped = analysis_result.cuped_estimate
effective = analysis_result.effective_primary_estimate

col_raw, col_adj = st.columns(2)
with col_raw:
    st.markdown("##### Raw estimate")
    render_metric_estimate(raw, alpha=cfg.alpha)
    if raw is None:
        st.caption(
            "Possible causes: no post-period rows in the metric CSV for "
            f"`{cfg.primary_metric}`, or no unit_id overlap between the "
            "assignments and metric CSVs after an inner join. "
            "Check that the metric CSV contains a `period=post` row for "
            f"`{cfg.primary_metric}` for each assigned unit."
        )

with col_adj:
    if cuped is not None:
        st.markdown("##### CUPED-adjusted estimate ✦")
        render_metric_estimate(cuped, alpha=cfg.alpha, label=cfg.primary_metric + " (CUPED)")
        st.caption("✦ CUPED adjustment applied — this is the **effective** estimate used for the decision.")
    else:
        st.markdown("##### CUPED estimate")
        # Gap 3: make explicit that the raw estimate IS the effective estimate used for
        # the decision when CUPED is not applied — e.g. Scenario C.
        st.caption(
            "CUPED was not applied. The **raw estimate is used as the effective estimate "
            "for the decision** (shown left)."
        )
        if analysis_result.cuped_readiness:
            rd = analysis_result.cuped_readiness
            st.caption(
                f"CUPED readiness: **{rd.recommendation}** "
                f"(coverage={rd.matched_coverage:.3f}, r={rd.pre_post_correlation:.4f}) — "
                "not applied."
            )
        else:
            st.caption("No pre-period data found — CUPED readiness could not be assessed.")

# ---------------------------------------------------------------------------
# CUPED readiness block (repeated inline for analyst convenience)
# ---------------------------------------------------------------------------
if analysis_result.cuped_readiness:
    with st.expander("CUPED readiness detail"):
        rd = analysis_result.cuped_readiness
        c1, c2, c3 = st.columns(3)
        c1.metric("Matched coverage", f"{rd.matched_coverage:.3f}")
        c2.metric("Pre-post correlation", f"{rd.pre_post_correlation:.4f}")
        c3.metric("Est. variance reduction", f"{rd.estimated_variance_reduction:.4f}")
        st.caption(rd.explanation)
        for cav in rd.caveats:
            st.caption(f"▸ {cav}")

# ---------------------------------------------------------------------------
# Secondary metrics
# ---------------------------------------------------------------------------
if analysis_result.secondary_metric_results:
    st.subheader("Secondary metrics")
    render_metric_estimates_table(analysis_result.secondary_metric_results, alpha=cfg.alpha)

# ---------------------------------------------------------------------------
# Guardrail metrics
# ---------------------------------------------------------------------------
if analysis_result.guardrail_metric_results:
    st.subheader("Guardrail metrics")
    render_metric_estimates_table(analysis_result.guardrail_metric_results, alpha=cfg.alpha)

    # Resolve declared directions from the config for display.
    directions: dict = {}
    if cfg is not None and hasattr(cfg, "guardrail_directions"):
        directions = cfg.guardrail_directions or {}

    any_blocking = False
    for g in analysis_result.guardrail_metric_results:
        if not g.is_significant:
            continue
        dir_ = directions.get(g.metric_name)
        from abkit_core.schemas import GuardrailDirection
        # Mirrors _guardrail_blocks() in analysis.py
        if dir_ is None or str(dir_) == "flat":
            any_blocking = True
        elif str(dir_) == "increase" and g.absolute_lift is not None and g.absolute_lift < 0:
            any_blocking = True
        elif str(dir_) == "decrease" and g.absolute_lift is not None and g.absolute_lift > 0:
            any_blocking = True

    if any_blocking:
        st.warning(
            "⚠️  One or more guardrail metrics violated their declared direction. "
            "Review the decision above — `abkit-core` has already applied the direction-aware "
            "blocking rule and set the recommendation accordingly."
        )
    else:
        st.caption(
            "ℹ️  No guardrail violations. "
            "Significant movement in the declared direction does not trigger a hold."
        )
else:
    if cfg.guardrail_metrics:
        st.caption(
            "Guardrail metrics declared in config but not found in the metric data: "
            + ", ".join(m.name for m in cfg.guardrail_metrics)
        )

st.divider()
st.success("✔ Analysis complete. Continue to **4 · Memo** to read the summary and export JSON.")
