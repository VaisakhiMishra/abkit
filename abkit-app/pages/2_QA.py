"""
pages/2_QA.py — Assignment and metric quality checks.

Responsibilities:
  - Accept assignment CSV and metric CSV uploads.
  - Run abkit_core.check_assignment_quality (includes SRM).
  - Run abkit_core.check_metric_quality.
  - Run abkit_core.check_join_integrity.
  - Run abkit_core.assess_cuped_readiness for the primary metric.
  - Display all diagnostics using shared components.
  - Store quality_checks, metric_issues, join_issues in session state.

Session-state contract (Gaps 4, 6):
  - QA results are ONLY re-executed when the user clicks Run QA.
    Returning to this page after navigation restores the cached display without
    silently re-running checks against potentially different inputs.
  - Uploading a new CSV invalidates analysis_result, decision_memo, and
    result_payload so a stale analysis never survives a data change.
"""
from __future__ import annotations

import io
import sys
import os

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from components.issue_display import render_issues, render_issue_summary  # noqa: E402

import abkit_core  # noqa: E402

st.set_page_config(page_title="QA — abkit", layout="wide")

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

# ---------------------------------------------------------------------------
# Sample CSV templates (headers only — no data rows)
# ---------------------------------------------------------------------------
_ASSIGNMENT_CSV_TEMPLATE = "experiment_id,unit_id,variant,assignment_ts\n"
_METRIC_CSV_TEMPLATE = "experiment_id,unit_id,metric_name,metric_value,period\n"

st.title("2 · QA")
st.caption("Upload assignment and metric CSVs, then run data quality checks.")

with st.expander("📥 Download empty CSV templates", expanded=False):
    st.markdown(
        "Download these templates to see the required column headers "
        "before preparing your data files."
    )
    dl_a, dl_m = st.columns(2)
    with dl_a:
        st.download_button(
            label="⬇ assignments_template.csv",
            data=_ASSIGNMENT_CSV_TEMPLATE,
            file_name="assignments_template.csv",
            mime="text/csv",
            use_container_width=True,
        )
        st.caption(
            "Required columns: `experiment_id`, `unit_id`, `variant`, `assignment_ts`"
        )
    with dl_m:
        st.download_button(
            label="⬇ metrics_template.csv",
            data=_METRIC_CSV_TEMPLATE,
            file_name="metrics_template.csv",
            mime="text/csv",
            use_container_width=True,
        )
        st.caption(
            "Required columns: `experiment_id`, `unit_id`, `metric_name`, "
            "`metric_value`, `period`"
        )

# ---------------------------------------------------------------------------
# Guard: require config
# ---------------------------------------------------------------------------
cfg: abkit_core.ExperimentConfig | None = st.session_state.get("config")
if cfg is None:
    st.warning("⚠️  No config loaded. Complete **1 · Setup** first.")
    st.stop()

st.info(
    f"Running QA for experiment **{cfg.experiment_id}** "
    f"(`{cfg.experiment_name}`)"
)

# ---------------------------------------------------------------------------
# File upload widgets
# ---------------------------------------------------------------------------
col_a, col_m = st.columns(2)

with col_a:
    st.markdown("#### Assignment CSV")
    a_file = st.file_uploader(
        "Assignment file",
        type=["csv"],
        key="assignment_upload",
        help="Required columns: experiment_id, unit_id, variant, assignment_ts",
    )

with col_m:
    st.markdown("#### Metric CSV")
    m_file = st.file_uploader(
        "Metric file",
        type=["csv"],
        key="metric_upload",
        help="Required columns: experiment_id, unit_id, metric_name, metric_value, period",
    )

# Load from session state if not freshly uploaded.
# Guard: only re-parse when the filename differs from what is already stored.
# Without this guard the file_uploader widget returns the same file object on
# every rerender, causing an infinite parse → session-write → rerender loop.
assignments_df: pd.DataFrame | None = st.session_state.get("assignments_df")
metrics_df: pd.DataFrame | None     = st.session_state.get("metrics_df")

if a_file is not None and a_file.name != st.session_state.get("assignment_filename"):
    assignments_df = pd.read_csv(io.StringIO(a_file.read().decode("utf-8")))
    st.session_state["assignments_df"] = assignments_df
    st.session_state["assignment_filename"] = a_file.name
    # Gap 6: new assignment file → prior analysis is stale
    for _k in ("quality_checks", "metric_issues", "join_issues",
               "analysis_result", "decision_memo", "result_payload"):
        st.session_state[_k] = None

if m_file is not None and m_file.name != st.session_state.get("metric_filename"):
    metrics_df = pd.read_csv(io.StringIO(m_file.read().decode("utf-8")))
    st.session_state["metrics_df"] = metrics_df
    st.session_state["metric_filename"] = m_file.name
    # Gap 6: new metric file → prior analysis is stale
    for _k in ("metric_issues", "join_issues",
               "analysis_result", "decision_memo", "result_payload",
               "_cuped_readiness"):
        st.session_state[_k] = None

# ---------------------------------------------------------------------------
# Gap 7: show loaded file names / status
# ---------------------------------------------------------------------------
a_fname = st.session_state.get("assignment_filename")
m_fname = st.session_state.get("metric_filename")

# ---------------------------------------------------------------------------
# Data previews — rendered only from session state to prevent re-read loops
# ---------------------------------------------------------------------------
if st.session_state.get("assignments_df") is not None or st.session_state.get("metrics_df") is not None:
    prev_a, prev_m = st.columns(2)
    with prev_a:
        _adf = st.session_state.get("assignments_df")
        if _adf is not None:
            st.caption(
                f"**Loaded:** {st.session_state.get('assignment_filename') or 'unknown'}  ·  "
                f"{len(_adf):,} rows × {_adf.shape[1]} cols"
            )
            st.dataframe(_adf.head(5), use_container_width=True, hide_index=True)
        else:
            st.caption("Assignment file: not loaded")
    with prev_m:
        _mdf = st.session_state.get("metrics_df")
        if _mdf is not None:
            st.caption(
                f"**Loaded:** {st.session_state.get('metric_filename') or 'unknown'}  ·  "
                f"{len(_mdf):,} rows × {_mdf.shape[1]} cols"
            )
            st.dataframe(_mdf.head(5), use_container_width=True, hide_index=True)
        else:
            st.caption("Metric file: not loaded")

# ---------------------------------------------------------------------------
# Run QA
# ---------------------------------------------------------------------------
can_run_assignment = assignments_df is not None
can_run_metric     = metrics_df is not None

run_qa = st.button(
    "▶ Run QA",
    type="primary",
    use_container_width=True,
    disabled=not (can_run_assignment or can_run_metric),
)

# Gap 4: only execute QA when the button is pressed.
# On subsequent page visits, read results from session state without re-running.
have_cached_results = (
    st.session_state.get("quality_checks") is not None
    or st.session_state.get("metric_issues") is not None
)

if run_qa:
    # ----- Assignment QA -----
    if assignments_df is not None:
        with st.spinner("Running assignment QA…"):
            quality_checks = abkit_core.check_assignment_quality(assignments_df, cfg)
        st.session_state["quality_checks"] = quality_checks
    else:
        quality_checks = None

    # ----- Metric QA -----
    if metrics_df is not None:
        with st.spinner("Running metric QA…"):
            metric_issues = abkit_core.check_metric_quality(metrics_df, cfg)
        st.session_state["metric_issues"] = metric_issues
    else:
        metric_issues = None

    # ----- Join integrity -----
    if assignments_df is not None and metrics_df is not None:
        with st.spinner("Checking join integrity…"):
            join_issues = abkit_core.check_join_integrity(assignments_df, metrics_df, cfg)
        st.session_state["join_issues"] = join_issues
    else:
        join_issues = None
elif have_cached_results:
    # Restore from session state — do NOT re-run checks.
    quality_checks = st.session_state.get("quality_checks")
    metric_issues  = st.session_state.get("metric_issues")
    join_issues    = st.session_state.get("join_issues")

# =====================================================================
# Display results (when button was just clicked or cached results exist)
# =====================================================================
if run_qa or have_cached_results:

    quality_checks = st.session_state.get("quality_checks")
    metric_issues  = st.session_state.get("metric_issues")
    join_issues    = st.session_state.get("join_issues")

    # =====================================================================
    # Display: SRM check
    # =====================================================================
    st.subheader("SRM (Sample Ratio Mismatch) check")
    if quality_checks and quality_checks.srm_check:
        srm = quality_checks.srm_check
        sev = str(srm.severity)

        if sev == "critical":
            st.error(f"🔴 **CRITICAL** — max_drift = {srm.max_absolute_drift:.4f}, p = {srm.p_value:.4g}")
        elif sev == "warning":
            st.warning(f"🟡 **WARNING** — max_drift = {srm.max_absolute_drift:.4f}, p = {srm.p_value:.4g}")
        else:
            st.success(f"✅ **PASS** — max_drift = {srm.max_absolute_drift:.4f}, p = {srm.p_value:.4g}")

        with st.expander("SRM details", expanded=(sev in ("critical", "warning"))):
            st.caption(srm.explanation)

            # Allocation comparison table
            variants_in_check = list(srm.observed_counts.keys())
            rows = []
            for v in variants_in_check:
                rows.append({
                    "Variant": v,
                    "Observed N": srm.observed_counts.get(v, 0),
                    "Expected N": f"{srm.expected_counts.get(v, 0):.1f}",
                    "Observed %": f"{srm.observed_allocation.get(v, 0):.4f}",
                    "Expected %": f"{srm.expected_allocation.get(v, 0):.4f}",
                    "Drift": f"{abs(srm.observed_allocation.get(v,0) - srm.expected_allocation.get(v,0)):.4f}",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            st.markdown(
                f"**Chi² statistic:** {srm.chi2_stat:.4f}  ·  "
                f"**p-value:** {srm.p_value:.6f}  ·  "
                f"**SRM alpha used:** {srm.srm_alpha_used}"
            )
    elif assignments_df is None:
        st.info("Upload assignment CSV to run SRM check.")
    else:
        st.warning("SRM check did not produce a result.")

    # =====================================================================
    # Display: Balance checks
    # =====================================================================
    st.subheader("Assignment balance checks")
    if quality_checks and quality_checks.balance_checks:
        render_issue_summary(quality_checks.balance_checks)
        render_issues(quality_checks.balance_checks, show_details=True)
    elif quality_checks:
        st.success("No balance issues detected.")
    else:
        st.info("Upload assignment CSV to run balance checks.")

    # =====================================================================
    # Display: Metric QA
    # =====================================================================
    st.subheader("Metric quality checks")
    if metric_issues is not None:
        render_issue_summary(metric_issues)
        render_issues(
            metric_issues,
            empty_message="No metric issues detected.",
            show_details=True,
        )
    else:
        st.info("Upload metric CSV to run metric checks.")

    # =====================================================================
    # Display: Join integrity
    # =====================================================================
    st.subheader("Join integrity (assignment ↔ metric)")
    if join_issues is not None:
        render_issue_summary(join_issues)
        render_issues(
            join_issues,
            empty_message="No join gaps detected.",
            show_details=True,
        )
    elif assignments_df is None or metrics_df is None:
        st.info("Both assignment and metric CSVs needed for join integrity check.")

    # =====================================================================
    # Display: CUPED readiness
    # =====================================================================
    st.subheader("CUPED readiness")
    _mdf_for_cuped = st.session_state.get("metrics_df")
    if _mdf_for_cuped is not None:
        # Cache CUPED readiness in session state so it does not re-run on every
        # render (which caused the visible looping table refresh).
        # It is invalidated whenever metric_issues is cleared (new upload).
        if st.session_state.get("_cuped_readiness") is None or run_qa:
            with st.spinner("Assessing CUPED readiness…"):
                _cuped_result = abkit_core.assess_cuped_readiness(
                    _mdf_for_cuped, cfg.primary_metric
                )
            st.session_state["_cuped_readiness"] = _cuped_result
        cuped = st.session_state["_cuped_readiness"]

        rec = str(cuped.recommendation)
        if rec == "recommended":
            st.success(f"✅ **{rec.upper()}** — apply CUPED for variance reduction")
        elif rec == "optional":
            st.warning(f"🟡 **{rec.upper()}** — analyst judgment required")
        else:
            st.info(f"ℹ️  **{rec.upper()}** — CUPED unlikely to help")

        with st.expander("CUPED details", expanded=(rec != "not_recommended")):
            c1, c2, c3 = st.columns(3)
            c1.metric("Matched coverage", f"{cuped.matched_coverage:.3f}")
            c2.metric("Pre-post correlation", f"{cuped.pre_post_correlation:.4f}")
            c3.metric("Est. variance reduction", f"{cuped.estimated_variance_reduction:.4f}")
            st.caption(cuped.explanation)
            for cav in cuped.caveats:
                st.caption(f"▸ {cav}")
    else:
        st.info("Upload metric CSV to assess CUPED readiness.")
        # Clear stale cached CUPED readiness when the metric file is removed
        st.session_state["_cuped_readiness"] = None

    st.divider()

    has_errors = False
    if quality_checks and quality_checks.balance_checks:
        has_errors = any(str(i.severity) == "error" for i in quality_checks.balance_checks)
    if metric_issues:
        has_errors = has_errors or any(str(i.severity) == "error" for i in metric_issues)
    # A critical SRM is a trust-gate failure — treat it as a blocking issue.
    if quality_checks and quality_checks.srm_check:
        if str(quality_checks.srm_check.severity) == "critical":
            has_errors = True

    if has_errors:
        st.warning("⚠️  QA complete — errors present. Review before running analysis.")
    else:
        st.success("✔ QA complete. Continue to **3 · Analysis**.")

else:
    if not can_run_assignment and not can_run_metric:
        st.info("Upload assignment and/or metric CSVs to run QA.")
    else:
        st.info("Click **Run QA** to start checks.")
