"""
pages/4_Memo.py — Human-readable experiment summary and JSON export.

Responsibilities:
  - Render a readable memo from the result payload.
  - Provide a download button for the canonical JSON export.
  - Show a text summary suitable for sharing.
"""
from __future__ import annotations

import json
import sys
import os

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

st.set_page_config(page_title="Memo — abkit", layout="wide")

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

st.title("4 · Memo")
st.caption("Human-readable experiment summary and canonical JSON export.")

# ---------------------------------------------------------------------------
# Guard: require result payload
# ---------------------------------------------------------------------------
result_payload = st.session_state.get("result_payload")
cfg            = st.session_state.get("config")

if result_payload is None:
    st.warning("⚠️  No result available. Complete **3 · Analysis** first.")
    st.stop()

decision = result_payload.decision
analysis = result_payload.analysis
spec_val = result_payload.spec_validation
qc       = result_payload.quality_checks
plan     = result_payload.duration_plan

# ---------------------------------------------------------------------------
# Decision headline
# ---------------------------------------------------------------------------
rec = str(decision.recommendation).upper() if decision else "—"
ICONS = {"SHIP": "✅", "HOLD": "🔴", "RERUN": "🔄", "INCONCLUSIVE": "⬜"}
icon = ICONS.get(rec, "—")

st.markdown(f"## {icon} Recommendation: {rec}")
st.markdown(f"**Experiment:** {result_payload.experiment_id}")
st.markdown(
    f"**Generated at:** {result_payload.generated_at.strftime('%Y-%m-%d %H:%M UTC')}  ·  "
    f"**Run ID:** `{result_payload.run_id}`"
)
st.markdown(f"**Status:** `{result_payload.status}`")

st.divider()

# ---------------------------------------------------------------------------
# Reasoning section
# ---------------------------------------------------------------------------
if decision:
    st.markdown("### Reasoning")
    st.markdown(decision.reasoning_summary)

    if decision.key_caveats:
        st.markdown("**Caveats**")
        for cav in decision.key_caveats:
            st.markdown(f"- {cav}")

    if decision.next_actions:
        st.markdown("**Next actions**")
        for action in decision.next_actions:
            st.markdown(f"- {action}")

    if decision.alpha_used is not None:
        st.caption(f"Significance threshold (alpha) used: {decision.alpha_used}")

st.divider()

# ---------------------------------------------------------------------------
# Primary metric summary
# ---------------------------------------------------------------------------
if analysis:
    st.markdown("### Primary metric results")
    effective = analysis.effective_primary_estimate
    raw       = analysis.primary_metric_result
    cuped     = analysis.cuped_estimate

    if effective:
        cuped_applied = cuped is not None
        label = "CUPED-adjusted estimate" if cuped_applied else "Raw estimate"
        cols = st.columns(4)
        cols[0].metric("Control mean",   f"{effective.control_mean:.6g}")
        cols[1].metric("Treatment mean", f"{effective.treatment_mean:.6g}")
        cols[2].metric(
            "Absolute lift",
            f"{effective.absolute_lift:+.6g}",
            delta=f"{effective.absolute_lift:+.6g}",
            delta_color="normal",
        )
        cols[3].metric(
            "Relative lift",
            (
                f"{effective.relative_lift * 100:+.4f}%"
                if effective.relative_lift is not None
                else "— undefined"
            ),
        )

        p_str = f"{effective.p_value:.4g}" if effective.p_value is not None else "—"
        alpha_str = f"{cfg.alpha}" if cfg else "—"
        if effective.is_significant is True:
            sig_str = "✅ significant"
        elif effective.is_significant is False:
            sig_str = "⬜ not significant"
        else:
            sig_str = "— significance not computed"
        ci_str = (
            f"[{effective.ci_lower:+.6g}, {effective.ci_upper:+.6g}]"
            if effective.ci_lower is not None else "—"
        )

        st.markdown(
            f"**Estimate type:** {label}  ·  "
            f"**p-value:** {p_str}  ·  "
            f"**alpha:** {alpha_str}  ·  "
            f"**{sig_str}**  ·  "
            f"**95% CI:** {ci_str}"
        )

        if cuped_applied and raw:
            with st.expander("Raw (unadjusted) estimate for comparison"):
                raw_p = f"{raw.p_value:.4g}" if raw.p_value is not None else "—"
                st.markdown(
                    f"control_mean={raw.control_mean:.6g}  ·  "
                    f"treatment_mean={raw.treatment_mean:.6g}  ·  "
                    f"abs_lift={raw.absolute_lift:+.6g}  ·  "
                    f"p={raw_p}"
                )
        if not cuped_applied:
            # Gap 3 / Gap 10: explicit note that raw == effective for Scenario C.
            st.caption(
                "CUPED was not applied — the raw estimate above is the effective "
                "estimate used for the decision."
            )

    # CUPED readiness
    if analysis.cuped_readiness:
        rd = analysis.cuped_readiness
        st.markdown(
            f"**CUPED readiness:** {rd.recommendation}  ·  "
            f"coverage={rd.matched_coverage:.3f}  ·  "
            f"correlation={rd.pre_post_correlation:.4f}  ·  "
            f"est. variance reduction={rd.estimated_variance_reduction:.4f}"
        )

    # Secondary metrics
    if analysis.secondary_metric_results:
        st.markdown("### Secondary metrics")
        for est in analysis.secondary_metric_results:
            if est.is_significant is True:
                sig = "✅"
            elif est.is_significant is False:
                sig = "—"
            else:
                sig = "~ not computed"
            p_str = f"{est.p_value:.4g}" if est.p_value is not None else "—"
            st.markdown(
                f"- **{est.metric_name}**: "
                f"abs_lift={est.absolute_lift:+.4g}, "
                f"rel_lift="
                + (f"{est.relative_lift * 100:+.2f}%" if est.relative_lift is not None else "—") + ", "
                f"p={p_str} {sig}"
            )

    # Guardrail metrics
    if analysis.guardrail_metric_results:
        st.markdown("### Guardrail metrics")

        # Resolve declared directions from the config (schema v1.1).
        # Falls back gracefully when cfg or guardrail_directions is absent.
        directions: dict = {}
        if cfg is not None and hasattr(cfg, "guardrail_directions"):
            directions = cfg.guardrail_directions or {}

        for est in analysis.guardrail_metric_results:
            p_str = f"{est.p_value:.4g}" if est.p_value is not None else "—"
            dir_ = directions.get(est.metric_name)
            dir_label = f" *(direction: {dir_})*" if dir_ else ""

            if est.is_significant is True:
                # Work out whether the significant movement violates the direction.
                lift = est.absolute_lift
                if dir_ is None or str(dir_) == "flat":
                    sig = "🚨 significant — blocks (no direction declared; any movement is a violation)"
                elif str(dir_) == "increase":
                    if lift is not None and lift < 0:
                        sig = "🚨 significant — **blocks** (desired ↑ but lift is negative)"
                    else:
                        sig = "✅ significant — passes direction check (desired ↑, lift is positive)"
                elif str(dir_) == "decrease":
                    if lift is not None and lift > 0:
                        sig = "🚨 significant — **blocks** (desired ↓ but lift is positive)"
                    else:
                        sig = "✅ significant — passes direction check (desired ↓, lift is negative)"
                else:
                    sig = "🚨 significant"
            elif est.is_significant is False:
                sig = "— not significant"
            else:
                sig = "— significance not computed"

            st.markdown(
                f"- **{est.metric_name}**{dir_label}: "
                f"abs_lift={est.absolute_lift:+.4g}, "
                f"p={p_str} — {sig}"
            )

st.divider()

# ---------------------------------------------------------------------------
# SRM summary
# ---------------------------------------------------------------------------
if qc and qc.srm_check:
    srm = qc.srm_check
    sev = str(srm.severity)
    st.markdown("### Assignment quality (SRM)")
    badge = {"pass": "✅ PASS", "warning": "🟡 WARNING", "critical": "🔴 CRITICAL"}.get(sev, sev)
    st.markdown(f"**SRM severity:** {badge}")
    st.caption(srm.explanation)

# ---------------------------------------------------------------------------
# Duration plan summary
# ---------------------------------------------------------------------------
if plan:
    st.markdown("### Duration plan")
    st.markdown(
        f"Required N: **{plan.required_n_per_variant:,}** per variant "
        f"(**{plan.required_n_total:,}** total) "
        f"at {plan.daily_eligible_traffic:,} eligible units/day.  \n"
        f"Optimistic: **{plan.optimistic_days}** days  ·  "
        f"Planned: **{plan.planned_days}** days  ·  "
        f"Conservative: **{plan.conservative_days}** days"
    )

# ---------------------------------------------------------------------------
# Spec validation summary
# ---------------------------------------------------------------------------
with st.expander("Spec validation summary"):
    if spec_val.is_valid:
        st.success("Config valid.")
    else:
        st.error("Config has errors.")
    if spec_val.errors:
        for e in spec_val.errors:
            st.caption(f"🔴 {e.code}: {e.message}")
    if spec_val.warnings:
        for w in spec_val.warnings:
            st.caption(f"🟡 {w.code}: {w.message}")

st.divider()

# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------
st.markdown("### Export")

try:
    payload_json = result_payload.model_dump_json(indent=2)
except Exception as exc:
    payload_json = f"{{\"error\": \"Serialisation failed: {exc}\"}}"

st.download_button(
    label="⬇ Download result payload (JSON)",
    data=payload_json,
    file_name=f"{result_payload.experiment_id}_{result_payload.run_id[:8]}.json",
    mime="application/json",
    use_container_width=True,
)

with st.expander("Preview JSON payload"):
    st.code(payload_json, language="json")

# ---------------------------------------------------------------------------
# Plain-text memo for copy-paste
# ---------------------------------------------------------------------------
st.markdown("### Plain-text memo")

def _build_text_memo() -> str:
    lines = [
        f"EXPERIMENT MEMO — {result_payload.experiment_id}",
        f"Generated: {result_payload.generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Run ID:    {result_payload.run_id}",
        "",
        f"RECOMMENDATION: {rec}",
        "",
    ]
    if decision:
        lines.append("REASONING")
        lines.append(decision.reasoning_summary)
        lines.append("")
        if decision.key_caveats:
            lines.append("CAVEATS")
            for c in decision.key_caveats:
                lines.append(f"  * {c}")
            lines.append("")
        if decision.next_actions:
            lines.append("NEXT ACTIONS")
            for a in decision.next_actions:
                lines.append(f"  * {a}")
            lines.append("")

    if analysis and analysis.effective_primary_estimate:
        e = analysis.effective_primary_estimate
        lines += [
            "PRIMARY METRIC ESTIMATE",
            f"  Metric:          {cfg.primary_metric if cfg else '—'}",
            f"  Control mean:    {e.control_mean:.6g}",
            f"  Treatment mean:  {e.treatment_mean:.6g}",
            f"  Absolute lift:   {e.absolute_lift:+.6g}",
            "  Relative lift:   " + (
                f"{e.relative_lift * 100:+.4f}%"
                if e.relative_lift is not None
                else "— (undefined: zero control mean)"
            ),
            f"  p-value:         {e.p_value:.4g}" if e.p_value is not None else "  p-value:         —",
            f"  Significant:     {'Yes' if e.is_significant is True else ('No' if e.is_significant is False else 'Not computed')}",
            "",
        ]
        if analysis.cuped_readiness:
            rd = analysis.cuped_readiness
            lines += [
                "CUPED READINESS",
                f"  Recommendation:        {rd.recommendation}",
                f"  Matched coverage:      {rd.matched_coverage:.3f}",
                f"  Pre-post correlation:  {rd.pre_post_correlation:.4f}",
                f"  Est. variance reduc.:  {rd.estimated_variance_reduction:.4f}",
                "",
            ]

    if qc and qc.srm_check:
        srm = qc.srm_check
        lines += [
            "SRM CHECK",
            f"  Severity:          {srm.severity}",
            f"  Max absolute drift:{srm.max_absolute_drift:.4f}",
            f"  p-value:           {srm.p_value:.6f}",
            "",
        ]

    if plan:
        lines += [
            "DURATION PLAN",
            f"  Optimistic:    {plan.optimistic_days} days",
            f"  Planned:       {plan.planned_days} days",
            f"  Conservative:  {plan.conservative_days} days",
            "",
        ]

    return "\n".join(lines)


memo_text = _build_text_memo()
st.text_area("Memo text (copy to clipboard)", memo_text, height=350)

st.download_button(
    label="⬇ Download memo (.txt)",
    data=memo_text,
    file_name=f"{result_payload.experiment_id}_memo.txt",
    mime="text/plain",
    use_container_width=True,
)
