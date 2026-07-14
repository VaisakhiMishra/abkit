"""
components/metric_table.py — Render MetricEstimate objects in Streamlit.

Shared by Analysis and Memo pages.
"""
from __future__ import annotations

import math

import streamlit as st


def render_metric_estimate(est, *, alpha: float = 0.05, label: str | None = None) -> None:
    """
    Render a single MetricEstimate as a compact table plus significance badge.

    Args:
        est: MetricEstimate schema object.
        alpha: significance threshold (from config) for display.
        label: optional display name override (defaults to est.metric_name).
    """
    if est is None:
        st.caption("No estimate available.")
        return

    name = label or est.metric_name

    sig_badge = ""
    if est.is_significant is True:
        sig_badge = " &nbsp; ✅ **significant**"
    elif est.is_significant is False:
        sig_badge = " &nbsp; ⬜ not significant"

    st.markdown(f"**{name}**{sig_badge}", unsafe_allow_html=True)

    rel_lift_str = (
        f"{est.relative_lift * 100:+.4f}%"
        if est.relative_lift is not None
        else "— (undefined: zero control mean)"
    )

    rows = [
        ("Control mean",   f"{est.control_mean:.6g}"),
        ("Treatment mean", f"{est.treatment_mean:.6g}"),
        ("Absolute lift",  f"{est.absolute_lift:+.6g}"),
        ("Relative lift",  rel_lift_str),
    ]
    if est.p_value is not None:
        rows.append(("p-value", f"{est.p_value:.4g}  (alpha = {alpha})"))
    if est.ci_lower is not None and est.ci_upper is not None:
        rows.append(("95% CI", f"[{est.ci_lower:+.6g}, {est.ci_upper:+.6g}]"))

    cols = st.columns([2, 2])
    with cols[0]:
        for k, v in rows[:len(rows)//2 + len(rows) % 2]:
            st.markdown(f"**{k}:** {v}")
    with cols[1]:
        for k, v in rows[len(rows)//2 + len(rows) % 2:]:
            st.markdown(f"**{k}:** {v}")


def render_metric_estimates_table(estimates: list, *, alpha: float = 0.05) -> None:
    """
    Render a list of MetricEstimates as a compact pandas-style table.
    """
    if not estimates:
        st.caption("No estimates.")
        return

    import pandas as pd

    rows = []
    for est in estimates:
        rows.append({
            "Metric": est.metric_name,
            "Control": f"{est.control_mean:.4g}",
            "Treatment": f"{est.treatment_mean:.4g}",
            "Abs lift": f"{est.absolute_lift:+.4g}",
            "Rel lift %": (
                f"{est.relative_lift * 100:+.2f}%"
                if est.relative_lift is not None
                else "—"
            ),
            "p-value": f"{est.p_value:.4g}" if est.p_value is not None else "—",
            "Sig?": "✅" if est.is_significant is True else ("—" if est.is_significant is False else "~"),
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
