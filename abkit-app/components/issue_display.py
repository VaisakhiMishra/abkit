"""
components/issue_display.py — Render DiagnosticIssue lists in Streamlit.

Shared by Setup, QA, and Analysis pages.
"""
from __future__ import annotations

import streamlit as st


def _severity_badge(severity: str) -> str:
    return {
        "error":   "🔴 error",
        "warning": "🟡 warning",
        "info":    "🔵 info",
    }.get(str(severity), str(severity))


def render_issues(
    issues: list,
    *,
    empty_message: str = "No issues.",
    show_details: bool = False,
) -> None:
    """
    Render a list of DiagnosticIssue objects as Streamlit expander rows.

    Args:
        issues: list of DiagnosticIssue (or any object with .severity, .code, .message).
        empty_message: text shown when the list is empty.
        show_details: when True, render the .details dict if present.
    """
    if not issues:
        st.success(empty_message)
        return

    for issue in issues:
        sev = str(issue.severity)
        badge = _severity_badge(sev)
        label = f"{badge} — `{issue.code}`"
        if sev == "error":
            with st.expander(label, expanded=True):
                st.error(issue.message)
                if show_details and issue.details:
                    st.json(issue.details)
        elif sev == "warning":
            with st.expander(label, expanded=False):
                st.warning(issue.message)
                if show_details and issue.details:
                    st.json(issue.details)
        else:
            with st.expander(label, expanded=False):
                st.info(issue.message)
                if show_details and issue.details:
                    st.json(issue.details)


def render_issue_summary(issues: list) -> None:
    """Single-line counts of errors / warnings / infos."""
    if not issues:
        return
    errors   = sum(1 for i in issues if str(i.severity) == "error")
    warnings = sum(1 for i in issues if str(i.severity) == "warning")
    infos    = sum(1 for i in issues if str(i.severity) == "info")
    parts = []
    if errors:
        parts.append(f"🔴 {errors} error{'s' if errors != 1 else ''}")
    if warnings:
        parts.append(f"🟡 {warnings} warning{'s' if warnings != 1 else ''}")
    if infos:
        parts.append(f"🔵 {infos} info")
    st.caption("  ·  ".join(parts))
