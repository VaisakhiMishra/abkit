"""
abkit-app — Streamlit workbench for the A/B Testing Productivity Kit.

Entry point. Run with:
    streamlit run abkit-app/app.py
"""
import streamlit as st

st.set_page_config(
    page_title="ABKIT",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state keys used across all pages.
# Each page reads and writes these keys; no page owns them exclusively.
# ---------------------------------------------------------------------------
DEFAULTS: dict = {
    # ---- Uploaded raw content ----
    "config_bytes": None,       # str  — raw YAML text
    "config_filename": None,    # str  — display name for the uploaded config file
    "assignment_filename": None,# str  — display name for the uploaded assignment file
    "metric_filename": None,    # str  — display name for the uploaded metric file
    # ---- Parsed inputs (abkit-core objects) ----
    "config": None,             # ExperimentConfig
    "assignments_df": None,     # pd.DataFrame
    "metrics_df": None,         # pd.DataFrame
    # ---- Core outputs ----
    # Invalidation contract:
    #   - Loading a new config clears everything below this line.
    #   - Loading new CSV files clears analysis_result, decision_memo, result_payload.
    "spec_validation": None,    # SpecValidation
    "quality_checks": None,     # QualityChecks  (from check_assignment_quality)
    "metric_issues": None,      # list[DiagnosticIssue]
    "join_issues": None,        # list[DiagnosticIssue]
    "analysis_result": None,    # AnalysisResult
    "decision_memo": None,      # DecisionMemo
    "duration_plan": None,      # DurationPlan | None  (approximation, planning only)
    "result_payload": None,     # ResultPayload  (canonical serialisable output)
}

for key, default in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ---------------------------------------------------------------------------
# Sidebar: workflow Status
# ---------------------------------------------------------------------------

def _status_icon(key: str) -> str:
    return "✅" if st.session_state.get(key) is not None else "○"


with st.sidebar:
    # CSS: nav links only — uppercase, navy blue, letter-spacing.
    # No other sidebar spacing or font overrides.
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

    [data-testid="stSidebarNav"] a:hover span,
    [data-testid="stSidebarNav"] a:hover {
        color: inherit;
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

    # Short description of what the app does
    st.markdown(
        """
        <div style="font-size:0.85rem; line-height:1.55; color:#444; margin-top:0.25rem;">
        ABKIT guides you through a complete A/B experiment — from uploading your
        config and running data quality checks, through statistical analysis and
        variance reduction, to a clear ship-or-hold decision and a shareable memo.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.divider()

    # Workflow Status — compact HTML list so each item is one line,
    # but font size is left at the browser default (no inline font-size override).
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
    if st.button("🔄 Reset everything", use_container_width=True):
        for key, default in DEFAULTS.items():
            st.session_state[key] = default
        st.rerun()

    st.divider()
    st.caption(
        "Pages: **SETUP → QA → ANALYSIS → MEMO**  \n"
        "Navigate using the sidebar above."
    )

# ---------------------------------------------------------------------------
# Landing page content
# ---------------------------------------------------------------------------
st.title("ABKIT — A/B Testing Productivity Kit")
st.markdown("<br>", unsafe_allow_html=True)
st.markdown("<br>", unsafe_allow_html=True)
st.markdown("This workbench walks you through a complete experiment analysis in four steps.")
st.markdown("<br>", unsafe_allow_html=True)

st.markdown("""
| Page | What it does |
|---|---|
| **1 · SETUP** | Upload config YAML, validate spec, run duration planner |
| **2 · QA** | Upload assignment and metric CSVs, run SRM + CUPED checks |
| **3 · ANALYSIS** | Run statistical analysis, view raw vs CUPED estimates |
| **4 · MEMO** | Read the decision summary, export the result payload as JSON |
""")

st.markdown("<br>", unsafe_allow_html=True)
st.markdown("**Start** on the **SETUP** page using the sidebar.")
