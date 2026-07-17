"""
pages/1_Setup.py — Experiment config upload, spec validation, and duration plan.

Responsibilities:
  - Accept a YAML experiment config via file upload or text paste.
  - Parse and validate it using abkit_core.validate_experiment_spec.
  - Display structured validation errors and warnings.
  - Run the duration planner when planning fields are present.
  - Store config + spec_validation + duration_plan in session state.

Invalidation contract (Gap 5):
  Loading a new config clears all downstream outputs (QA, analysis, result_payload)
  so stale results from a previous experiment never survive a config reload.
"""
from __future__ import annotations

import math
import sys
import os

import streamlit as st
import yaml

# Make the app root importable so components can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from components.issue_display import render_issues, render_issue_summary  # noqa: E402

import abkit_core  # noqa: E402
from abkit_core.schemas import DurationPlanAssumptions  # noqa: E402

st.set_page_config(page_title="Setup — abkit", layout="wide")

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
# Sample config download
# ---------------------------------------------------------------------------
_SAMPLE_CONFIG_YAML = """\
schema_version: "1.2"
experiment_id: <your-experiment-id>
experiment_name: <Human-readable experiment name>
owner: <owner-email-or-team>
hypothesis: <Short statement of the expected mechanism and direction of the effect.>
unit_of_randomization: user_id   # e.g. user_id, session_id, device_id

# Variant names — first entry is treated as the control
variants:
  - control
  - treatment

# Expected allocation fractions (must sum to 1.0)
expected_allocation:
  control: 0.5
  treatment: 0.5

# Primary metric name — must match the metric_name values in your metric CSV
primary_metric: <primary_metric_name>
metric_type: <proportion|continuous>  # choose one

# Optional secondary metrics (plain string → inferred as continuous)
secondary_metrics:
  - <secondary_metric_name_1>
  - <secondary_metric_name_2>

# Optional guardrail metrics (plain string → inferred as continuous)
guardrail_metrics:
  - <guardrail_metric_name>

# Optional: declare desired direction per guardrail (increase | decrease | flat)
# Keys must match names in guardrail_metrics above.
# When omitted, any significant movement triggers a hold.
guardrail_directions:
  <guardrail_metric_name>: <increase|decrease|flat>

# Optional: Bonferroni correction for secondary metrics (default: false)
apply_bonferroni_correction: false

# Statistical parameters
alpha: 0.05
power: 0.8
mde: 0.02         # Minimum detectable effect (absolute for continuous; relative for proportion)
srm_alpha: 0.01   # SRM chi-squared test threshold

# Duration planning fields (optional — remove if not needed)
daily_eligible_traffic: <integer>  # e.g. 10000
traffic_cap: 1.0                   # fraction of eligible traffic in experiment (0, 1]
ramp_up_days: 0
planning_buffer_pct: 0.1
eligibility_rate: null             # fraction of total traffic that is eligible, e.g. 0.8 or null
"""

st.title("1 · Setup")
st.caption(
    "Upload your experiment config YAML. "
    "The spec will be validated and the duration planner will run if planning fields are present."
)

with st.expander("📥 Download sample config template", expanded=False):
    st.markdown(
        "Download the template below, fill in the placeholders, then upload it above."
    )
    st.download_button(
        label="⬇ Download sample_config.yaml",
        data=_SAMPLE_CONFIG_YAML,
        file_name="sample_config.yaml",
        mime="text/yaml",
        use_container_width=True,
    )
    st.code(_SAMPLE_CONFIG_YAML, language="yaml")

# ---------------------------------------------------------------------------
# Helper: duration plan computation
# ---------------------------------------------------------------------------

def _compute_duration_plan(cfg: abkit_core.ExperimentConfig) -> abkit_core.DurationPlan | None:
    """
    Compute the duration plan from ExperimentConfig planning fields.

    Returns None and adds a warning if daily_eligible_traffic is missing.
    """
    if cfg.daily_eligible_traffic is None:
        return None

    # ---------------------------------------------------------------------------
    # APPROXIMATION WARNING — this is planning arithmetic, not statistical inference.
    # Formula: n = sigma² × (z_α/2 + z_β)² / δ²   (two-arm equal-split)
    # Proportion: sigma² = 2×p×(1−p), p assumed = 0.1 (no observed base rate yet).
    # Continuous: sigma² = 2.0 (unit variance assumed; actual variance is unknown).
    # These estimates must NOT be used as a stopping rule or hypothesis test.
    # The abkit-core analysis uses the actual observed data, not this formula.
    # ---------------------------------------------------------------------------
    import scipy.stats as st_scipy  # local import to avoid top-level cost

    z_alpha = st_scipy.norm.ppf(1 - (cfg.alpha or 0.05) / 2)
    z_beta  = st_scipy.norm.ppf(cfg.power or 0.8)
    mde     = cfg.mde or 0.02

    # Assume proportion metric with p=0.1 base rate if metric_type is proportion,
    # otherwise use unit variance (sigma=1) for continuous.
    # The formula is approximate — its purpose is planning, not inference.
    if cfg.metric_type == "proportion":
        # pooled SE for two proportions; assume symmetric around 0.1 base rate
        p_base = 0.1
        sigma2 = 2 * p_base * (1 - p_base)
    else:
        sigma2 = 2.0  # unit variance for continuous, delta = mde

    required_n_per_variant = math.ceil(
        sigma2 * (z_alpha + z_beta) ** 2 / (mde ** 2)
    )
    n_variants = len(cfg.variants) if cfg.variants else 2
    required_n_total = required_n_per_variant * n_variants

    traffic_cap        = cfg.traffic_cap if cfg.traffic_cap is not None else 1.0
    ramp_up_days       = cfg.ramp_up_days if cfg.ramp_up_days is not None else 0
    planning_buffer    = cfg.planning_buffer_pct if cfg.planning_buffer_pct is not None else 0.0
    daily_traffic      = cfg.daily_eligible_traffic

    optimistic_days = math.ceil(required_n_total / daily_traffic)
    planned_days    = math.ceil(required_n_total / (daily_traffic * traffic_cap)) + ramp_up_days
    conservative_days = math.ceil(planned_days * (1 + planning_buffer))

    # Enforce monotonicity
    optimistic_days   = min(optimistic_days, planned_days)
    conservative_days = max(conservative_days, planned_days)

    caveats = [
        "These estimates are approximate. The sample size formula assumes a two-arm "
        "equal-split experiment and a simplified variance model.",
    ]
    if ramp_up_days > 0:
        caveats.append(
            f"ramp_up_days={ramp_up_days} is treated as a conservative planning margin, "
            "not a novelty-effect model. Actual novelty effects are not modelled numerically."
        )
    if planning_buffer > 0:
        caveats.append(
            f"A planning buffer of {planning_buffer * 100:.0f}% has been applied "
            "to the planned duration."
        )

    return abkit_core.DurationPlan(
        required_n_per_variant=required_n_per_variant,
        required_n_total=required_n_total,
        daily_eligible_traffic=daily_traffic,
        optimistic_days=optimistic_days,
        planned_days=planned_days,
        conservative_days=conservative_days,
        assumptions=DurationPlanAssumptions(
            traffic_cap=traffic_cap,
            ramp_up_days=ramp_up_days,
            planning_buffer_pct=planning_buffer,
            eligibility_rate=cfg.eligibility_rate,
        ),
        caveats=caveats,
    )


# ---------------------------------------------------------------------------
# Upload widget
# ---------------------------------------------------------------------------

tab_upload, tab_paste = st.tabs(["Upload YAML file", "Paste YAML text"])

raw_yaml: str | None = None
new_source: str | None = None  # tracks where the raw_yaml came from this render

with tab_upload:
    uploaded = st.file_uploader(
        "Config YAML file",
        type=["yaml", "yml"],
        help="Must match the experiment config schema (schema_version, experiment_id, …)",
    )
    if uploaded:
        raw_yaml = uploaded.read().decode("utf-8")
        new_source = uploaded.name
        # Gap 5: a new file replaces the session — invalidate all downstream outputs.
        if st.session_state.get("config_bytes") != raw_yaml:
            for _k in (
                "config", "spec_validation", "duration_plan",
                "assignments_df", "metrics_df",
                "quality_checks", "metric_issues", "join_issues",
                "analysis_result", "decision_memo", "result_payload",
                "assignment_filename", "metric_filename",
            ):
                st.session_state[_k] = None
        st.session_state["config_bytes"] = raw_yaml
        st.session_state["config_filename"] = uploaded.name

with tab_paste:
    pasted = st.text_area(
        "Paste YAML here",
        height=300,
        placeholder="schema_version: \"1.2\"\nexperiment_id: ...",
    )
    if pasted.strip():
        # Gap 5: pasted text replacing a different config invalidates downstream.
        if st.session_state.get("config_bytes") != pasted.strip():
            for _k in (
                "config", "spec_validation", "duration_plan",
                "assignments_df", "metrics_df",
                "quality_checks", "metric_issues", "join_issues",
                "analysis_result", "decision_memo", "result_payload",
                "assignment_filename", "metric_filename",
            ):
                st.session_state[_k] = None
            st.session_state["config_filename"] = "(pasted text)"
        raw_yaml = pasted.strip()
        new_source = "(pasted text)"

# Pre-fill from session state if no new upload
if raw_yaml is None and st.session_state.get("config_bytes"):
    raw_yaml = st.session_state["config_bytes"]
    fname = st.session_state.get("config_filename") or "unknown"
    st.info(f"Using previously uploaded config: **{fname}**. Upload a new file to replace it.")

# ---------------------------------------------------------------------------
# Parse + validate
# ---------------------------------------------------------------------------

if raw_yaml:
    with st.expander("Raw YAML preview", expanded=False):
        st.code(raw_yaml, language="yaml")

    run = st.button("▶ Validate config", type="primary", use_container_width=True)

    if run or st.session_state.get("spec_validation") is not None:
        try:
            data = yaml.safe_load(raw_yaml)
        except yaml.YAMLError as exc:
            st.error(f"YAML parse error: {exc}")
            st.stop()

        try:
            cfg = abkit_core.ExperimentConfig(**data)
        except Exception as exc:
            st.error(f"Config schema error: {exc}")
            st.stop()

        validation = abkit_core.validate_experiment_spec(cfg, for_analysis=True)

        # Cache in session state
        st.session_state["config"] = cfg
        st.session_state["spec_validation"] = validation

        # Gap 9: flag multi-variant configs before the user reaches Analysis.
        if len(cfg.variants) > 2:
            st.error(
                f"⛔ **v1 limit:** this config declares {len(cfg.variants)} variants "
                f"({', '.join(cfg.variants)}). "
                "The analysis engine supports exactly **2 variants** (control + one treatment) in v1. "
                "Reduce to 2 variants before running analysis."
            )

        # Duration plan
        try:
            plan = _compute_duration_plan(cfg)
        except Exception as exc:
            plan = None
            st.warning(f"Duration planner error: {exc}")
        st.session_state["duration_plan"] = plan

        # ---------------------
        # Render validation
        # ---------------------
        st.subheader("Spec validation")
        if validation.is_valid:
            st.success("✅ Config is valid.")
        else:
            st.error("🔴 Config has errors — fix before running analysis.")

        all_issues = validation.errors + validation.warnings
        render_issue_summary(all_issues)

        if validation.errors:
            st.markdown("**Errors**")
            render_issues(validation.errors, empty_message="No errors.")

        if validation.warnings:
            st.markdown("**Warnings**")
            render_issues(validation.warnings, empty_message="No warnings.")

        # ---------------------
        # Config summary
        # ---------------------
        st.subheader("Config summary")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Experiment", cfg.experiment_id)
            st.metric("Owner", cfg.owner)
            st.metric("Primary metric", cfg.primary_metric)
        with c2:
            st.metric("Variants", ", ".join(cfg.variants))
            st.metric("Alpha / Power", f"{cfg.alpha} / {cfg.power}")
            st.metric("MDE", f"{cfg.mde}")
        with c3:
            alloc = "  ·  ".join(f"{k}: {v:.0%}" for k, v in cfg.expected_allocation.items())
            st.metric("Expected allocation", alloc)
            st.metric("Metric type", cfg.metric_type or "not set")
            st.metric("SRM alpha", cfg.srm_alpha or "0.01 (default)")

        # ---------------------
        # Duration plan
        # ---------------------
        # Gap 8: label the duration plan section as an approximation prominently.
        st.subheader("Duration plan *(approximation)*")
        st.caption(
            "⚠️  These figures use a simplified variance model (see assumptions below). "
            "They are for planning orientation only and are **not** used as a stopping rule."
        )
        if plan is None:
            st.warning(
                "⚠️  `daily_eligible_traffic` is not set in the config. "
                "Duration estimates cannot be computed."
            )
        else:
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("Required N (per variant) ≈", f"{plan.required_n_per_variant:,}")
            d2.metric("Optimistic ≈", f"{plan.optimistic_days} days")
            d3.metric("Planned ≈", f"{plan.planned_days} days")
            d4.metric("Conservative ≈", f"{plan.conservative_days} days")

            with st.expander("Assumptions & caveats", expanded=True):
                a = plan.assumptions
                st.markdown(
                    f"- **traffic_cap:** {a.traffic_cap}\n"
                    f"- **ramp_up_days:** {a.ramp_up_days}\n"
                    f"- **planning_buffer_pct:** {a.planning_buffer_pct}\n"
                    f"- **eligibility_rate:** {a.eligibility_rate or 'not set'}\n"
                    f"- **base rate assumed** (proportions): 0.1  ·  "
                    f"**variance assumed** (continuous): 1.0\n"
                )
                for cav in plan.caveats:
                    st.caption(f"▸ {cav}")

        st.divider()
        st.success("✔ Setup complete. Continue to **2 · QA** to upload assignment and metric data.")

else:
    st.info("Upload or paste a config YAML to begin.")
