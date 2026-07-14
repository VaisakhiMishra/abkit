# Skill: Results Memo

> **Schema version:** 1.0  
> **Layer:** `abkit-skills/results-memo`  
> **Source of truth for field names:** `abkit-core/src/abkit_core/schemas.py`  
> **Contains:** Analysis review prompt · Decision memo drafting prompt

---

## Purpose

This skill covers two closely related tasks that are always performed together after an analysis run:

1. **Analysis review** — Interpret the statistical results from `analysis` and `quality_checks` in plain language.
2. **Decision memo drafting** — Convert the `decision` object and the analysis review into a concise, audience-appropriate memo.

Both prompts are provided below. Run Analysis Review first; use its output as an input to the Memo Draft.

---

## Inputs

| Input | Required | Type | Notes |
|---|---|---|---|
| Full `ResultPayload` | Yes | JSON | The complete payload from the analysis run |
| Target audience | Optional | string | `"analyst"` (default), `"product_manager"`, or `"executive"` |
| Exported memo text | Optional | Markdown string | When the Streamlit app exported a `.md` file, include it as additional context |

---

## Outputs

### Analysis Review
- Headline result with the correct estimate (`effective_primary_estimate`)
- CUPED status and whether it changed the conclusion
- Secondary and guardrail metric summaries
- Any trust caveats from `quality_checks`

### Decision Memo Draft
- One-paragraph executive summary
- Recommendation (`ship` / `hold` / `rerun` / `inconclusive`) with reasoning
- Key caveats and next actions
- Formatted for the requested audience

---

## Refusal conditions

- If `analysis` is `null`, state that analysis was not run and stop. Do not interpret metric values.
- If `quality_checks.srm_check.severity == "critical"`, lead the memo with a trust warning before the headline result.
- Do not change the `recommendation` value from `decision.recommendation`. The memo may explain it but must not override it.
- Do not re-compute statistical tests. Use only the values provided in the payload.
- If `decision` is `null`, produce the analysis review only and note that a decision memo cannot be drafted without a decision object.

---

## Prompt 1: Analysis Review

Use this template to produce a plain-language interpretation of the statistical results.

```
You are an experiment analyst writing a structured analysis review for an A/B test.

You will be given a ResultPayload JSON object (abkit schema version 1.0).
Your task is to interpret the analysis results in plain language.
Do not re-run any statistical tests. Read all values directly from the payload.

---
RESULT PAYLOAD:
{{paste the full ResultPayload JSON here}}
---

Produce the following four sections.

SECTION 1 — HEADLINE RESULT
State the primary metric result using analysis.effective_primary_estimate (not primary_metric_result).
Report: metric_name, control_mean, treatment_mean, absolute_lift, relative_lift, p_value, ci_lower, ci_upper, is_significant.
State whether the result is statistically significant using is_significant.
If is_significant is true, write: "The primary metric moved significantly."
If is_significant is false, write: "The primary metric did not move significantly."
If is_significant is null, write: "Significance was not computed."
Include the alpha threshold from decision.alpha_used when writing the significance statement (e.g. "p=0.02 < alpha=0.05").
If analysis.cuped_estimate is present and differs from analysis.primary_metric_result, note that the headline uses the CUPED-adjusted estimate and state the raw vs. adjusted absolute_lift values.

SECTION 2 — SECONDARY AND GUARDRAIL METRICS
For each entry in analysis.secondary_metric_results, report: metric_name, absolute_lift, relative_lift, is_significant.
For each entry in analysis.guardrail_metric_results, report: metric_name, absolute_lift, is_significant.
Guardrails: if any guardrail metric shows is_significant=true with a negative absolute_lift, flag it explicitly as a potential harm signal.
If both lists are empty, write: "No secondary or guardrail metrics were analyzed."

SECTION 3 — TRUST CAVEATS
Report any trust-level concerns from quality_checks.
If quality_checks is null: write "Quality checks were not run."
If srm_check.severity is "critical": quote srm_check.explanation and state that results should not be acted upon until SRM is resolved.
If srm_check.severity is "warning": quote srm_check.explanation and note it as a caveat.
If srm_check.severity is "pass": write "SRM check passed."
Summarize any error-severity DiagnosticIssue items from balance_checks, missingness_checks, join_integrity_checks, or guardrail_status.

SECTION 4 — CUPED STATUS
If analysis.cuped_readiness is null: write "CUPED was not assessed (no pre-period data)."
Otherwise report:
- cuped_readiness.recommendation ("recommended", "optional", or "not_recommended")
- cuped_readiness.matched_coverage (as a percentage)
- cuped_readiness.pre_post_correlation
- cuped_readiness.estimated_variance_reduction (as a percentage)
- Whether cuped_estimate is present (i.e., CUPED was actually applied)
Quote cuped_readiness.explanation verbatim.
```

---

## Prompt 2: Decision Memo Draft

Run Prompt 1 first. Then use this template to draft the memo. Provide both the original payload and the analysis review as inputs.

```
You are drafting an experiment decision memo for {{audience}}.

Audience options and their tone:
- "analyst": technical, full detail, include confidence intervals and p-values
- "product_manager": semi-technical, focus on effect size and business meaning, include p-value but not CI bounds
- "executive": non-technical, lead with the recommendation and business impact, omit statistical notation

You will be given a ResultPayload JSON and an analysis review already produced by a reviewer.
Do not re-compute any values. Write using the information provided.

---
RESULT PAYLOAD:
{{paste the full ResultPayload JSON here}}
---
ANALYSIS REVIEW:
{{paste the output of Prompt 1 here}}
---
{% if memo_text is provided %}
EXPORTED MEMO TEXT (for additional context):
{{paste the exported .md memo text here}}
{% endif %}
---

Write a memo with the following structure.

EXPERIMENT: state experiment_name from spec_validation.normalized_config (or experiment_id if name is unavailable).
DATE: state generated_at formatted as YYYY-MM-DD.
RECOMMENDATION: state decision.recommendation exactly as written. Do not paraphrase the enum value.

ONE-PARAGRAPH SUMMARY
Write 3 to 5 sentences summarizing: what was tested, what the primary metric result was, and what the recommendation is.
Calibrate technical detail to the audience setting above.
If srm_check.severity is "critical", lead this paragraph with a trust warning.

RESULTS
- Primary metric: use analysis.effective_primary_estimate. State effect size and significance per audience level.
- Secondary metrics: list each from analysis.secondary_metric_results with a one-line summary.
- Guardrails: list each from analysis.guardrail_metric_results with a one-line summary. Flag any harm signals.

REASONING
Quote or paraphrase decision.reasoning_summary. Do not add reasoning that is not present in the payload.

CAVEATS
List each item in decision.key_caveats as a bullet. If key_caveats is empty, omit this section.

NEXT ACTIONS
List each item in decision.next_actions as a numbered action. If next_actions is empty, omit this section.
```

---

## Example memo (analyst audience)

```
EXPERIMENT: Checkout button redesign (exp_checkout_btn_001)
DATE: 2025-07-14
RECOMMENDATION: ship

ONE-PARAGRAPH SUMMARY
The checkout button redesign experiment ran for 14 days across 12,400 users (50/50 split).
The primary metric, conversion_rate, increased by 1.8 percentage points (relative lift +9.2%,
p=0.008 < alpha=0.05), using the CUPED-adjusted estimate. The SRM check passed. The recommendation
is to ship the treatment.

RESULTS
- conversion_rate (CUPED-adjusted): control=0.196, treatment=0.214, absolute_lift=+0.018,
  relative_lift=+9.2%, p=0.008, CI [0.005, 0.031], significant=true
- revenue_per_user: absolute_lift=+$0.43, significant=false
- refund_rate (guardrail): absolute_lift=+0.001, significant=false — no harm signal

REASONING
Primary metric moved significantly above the MDE of 0.02. SRM check passed. No guardrail
regression observed. The CUPED adjustment improved precision (estimated variance reduction: 18%).

CAVEATS
- The revenue_per_user lift is directionally positive but not significant at alpha=0.05. A longer
  run would be needed to confirm it.

NEXT ACTIONS
1. Ship the treatment to 100% of eligible traffic.
2. Monitor refund_rate for 7 days post-ship.
3. Schedule a retro to document learnings for the checkout team.
```

---

## Notes for skill maintainers

- Always use `effective_primary_estimate` for the headline. Never use `primary_metric_result` directly for the final result statement.
- `alpha_used` is a field on `DecisionMemo`, not on `ExperimentConfig`. It is always safe to read from the payload without re-loading the config.
- The `decision.recommendation` enum values are: `ship`, `hold`, `rerun`, `inconclusive`. Do not map these to other words in the final memo.
- If `decision` is populated but `decision.key_caveats` and `decision.next_actions` are both empty lists, the memo sections CAVEATS and NEXT ACTIONS should be omitted entirely rather than showing empty bullets.
