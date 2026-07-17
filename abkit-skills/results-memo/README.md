# Skill: Results Memo

> **Schema version:** 1.2
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

## Two supported input modes

### Mode A — ResultPayload provided

Paste or attach the full `ResultPayload` JSON. The skill reads it directly and produces the analysis review and memo draft. It does not re-ask for values already present in the payload.

### Mode B — Interactive (no payload yet)

This skill cannot produce a meaningful analysis review or memo without the `ResultPayload` — the statistical values can only come from an abkit pipeline run. If the payload is absent:

1. The skill identifies what is missing and explains why.
2. It instructs the user on how to obtain the payload (run the abkit pipeline after uploading assignment and metrics CSVs).
3. It does not produce a partial or estimated memo.

**The skill does not ask the user to type in p-values, effect sizes, or recommendation values by hand. These are computed by abkit-core and must be read from the payload.**

For the memo draft specifically, the `target audience` is the only value the skill may request interactively (see Prompt 2 below).

---

## Inputs

| Input | Required | Type | Notes |
|---|---|---|---|
| Full `ResultPayload` | **Yes** | JSON | The complete payload from the analysis run — must come from abkit-core |
| Target audience | Optional | string | `"analyst"` (default), `"product_manager"`, or `"executive"` |
| Exported memo text | Optional | Markdown string | When the Streamlit app exported a `.md` file, include it as additional context |

### What the payload must contain

The following sub-objects must be present in the `ResultPayload` for each task:

| Task | Required sub-object | What happens if absent |
|---|---|---|
| Analysis review | `analysis` | Skill stops and states that analysis was not run |
| Analysis review | `quality_checks` | Skill notes QA was not run; proceeds without trust caveats |
| Decision memo | `decision` | Skill produces analysis review only; memo cannot be drafted |
| Both | `spec_validation.normalized_config` | Used for experiment name and guardrail direction lookup |

---

## Outputs

### Analysis Review
- Headline result with the correct estimate (`effective_primary_estimate`)
- CUPED status and whether it changed the conclusion
- Secondary and guardrail metric summaries with Bonferroni-aware significance statements
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
- Do not ask the user to provide p-values, effect sizes, or any computed statistical values interactively.

---

## Prompt 1: Analysis Review

Use this template to produce a plain-language interpretation of the statistical results.

```
You are an experiment analyst writing a structured analysis review for an A/B test.

You support two input modes:
1. The user provides a full ResultPayload JSON. Parse it and produce the four-section analysis review below.
2. The payload is absent. Explain that the ResultPayload must come from an abkit pipeline run and
   instruct the user to run the pipeline with their assignment and metrics CSVs uploaded.
   Do not produce a partial or estimated review.

Note: statistical values (p-values, effect sizes, SRM results, decisions) are computed by abkit-core.
Never ask the user to provide these values interactively.

---
{% if result_payload is provided %}
RESULT PAYLOAD:
{{paste the full ResultPayload JSON here}}
{% endif %}
---

If the ResultPayload is absent, respond with:
  "The ResultPayload is not available. To produce the analysis review, run the abkit analysis
  pipeline with your assignment and metrics CSVs uploaded. Then paste the full ResultPayload here."
Stop and do not produce any of the four sections below.

If analysis is null in the payload, respond with:
  "analysis is null in the payload. The statistical analysis was not run. Confirm that metric
  data was uploaded and re-run the analysis pipeline before requesting a review."
Stop.

Once the payload is present and analysis is non-null, produce the following four sections.

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
Check analysis.bonferroni_correction_applied. If true, note that secondary metrics were tested at analysis.secondary_alpha_used (= config.alpha / m) rather than config.alpha.
For each entry in analysis.secondary_metric_results, report: metric_name, absolute_lift, relative_lift, is_significant.
When bonferroni_correction_applied is true, include the adjusted alpha threshold in the significance statement (e.g. "p=0.03 is not significant at adjusted alpha=0.0167").
When bonferroni_correction_applied is false, note that secondary metrics were tested at the base alpha.
For each entry in analysis.guardrail_metric_results, report: metric_name, absolute_lift, is_significant.
Guardrail direction (schema v1.1): check spec_validation.normalized_config.guardrail_directions for any declared direction for each guardrail metric.
  - If direction is "increase" and the metric is significant with a negative absolute_lift: flag this as a direction-violation harm signal.
  - If direction is "decrease" and the metric is significant with a positive absolute_lift: flag this as a direction-violation harm signal.
  - If direction is "flat" or undeclared: any significant movement is worth flagging.
  - Do not re-derive the blocking decision. The decision.recommendation already reflects the correct outcome.
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

If the target audience is not specified, default to `"analyst"`. If you are unsure, ask: "Which audience should I write for? Options: analyst (full detail), product_manager (semi-technical), executive (non-technical, lead with recommendation)."

```
You are drafting an experiment decision memo for {{audience}}.

Audience options and their tone:
- "analyst": technical, full detail, include confidence intervals and p-values
- "product_manager": semi-technical, focus on effect size and business meaning, include p-value but not CI bounds
- "executive": non-technical, lead with the recommendation and business impact, omit statistical notation

You will be given a ResultPayload JSON and an analysis review already produced by a reviewer.
Do not re-compute any values. Write using the information provided.

If the target audience is not provided, ask the user which audience to write for before proceeding.

---
{% if result_payload is provided %}
RESULT PAYLOAD:
{{paste the full ResultPayload JSON here}}
{% endif %}
---
{% if analysis_review is provided %}
ANALYSIS REVIEW:
{{paste the output of Prompt 1 here}}
{% endif %}
---
{% if memo_text is provided %}
EXPORTED MEMO TEXT (for additional context):
{{paste the exported .md memo text here}}
{% endif %}
---

If the ResultPayload or analysis_review is absent, respond with:
  "The ResultPayload and analysis review are both required. Please run Prompt 1 first to produce
  the analysis review, then paste both the ResultPayload and the review output here."
Stop and do not produce the memo.

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
  If analysis.bonferroni_correction_applied is true, note that these were tested at analysis.secondary_alpha_used
  (not at the base alpha) and include the adjusted threshold in significance statements.
  If analysis.bonferroni_correction_applied is false, no special note is needed for secondary metrics.
- Guardrails: list each from analysis.guardrail_metric_results with a one-line summary.
  For each guardrail, check spec_validation.normalized_config.guardrail_directions for a declared direction.
  Flag a harm signal only when the movement violates the declared direction (or when no direction is declared and movement is significant).
  If the guardrail was significant but moved in the correct direction, note that it passed the direction check.

REASONING
Quote or paraphrase decision.reasoning_summary. Do not add reasoning that is not present in the payload.

CAVEATS
List each item in decision.key_caveats as a bullet. If key_caveats is empty, omit this section.

NEXT ACTIONS
List each item in decision.next_actions as a numbered action. If next_actions is empty, omit this section.
```

---

## Example outputs

### Example 1 — Full payload provided (analyst audience)

**Input:** Complete `ResultPayload` JSON is pasted and audience is `"analyst"`.

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
- revenue_per_user: absolute_lift=+$0.43, significant=false (p=0.18 > alpha=0.05)
- refund_rate (guardrail, direction=decrease): absolute_lift=+0.001, significant=false — no harm signal

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

### Example 2 — No payload available (interactive mode)

**Input:** User asks "Write me a memo for my experiment" without providing a payload.

```
The ResultPayload is not available. To produce the analysis review and memo, run the abkit
analysis pipeline with your assignment and metrics CSVs uploaded. Then paste the full
ResultPayload here.

Once the payload is available, also let me know your target audience:
- analyst (full technical detail with confidence intervals and p-values)
- product_manager (semi-technical, focus on effect size and business meaning)
- executive (non-technical, lead with recommendation and business impact)
```

### Example 3 — Guardrail directions in memo

**Input:** Payload includes guardrail_directions. Guardrail `refund_rate` has `direction=decrease` and moved down significantly.

```
RESULTS
...
- refund_rate (guardrail, direction=decrease): absolute_lift=−0.003, significant=true — direction
  check PASSED. Refund rate decreased significantly, which is the desired direction. This guardrail
  did not block the ship recommendation.
```

**Input:** Same scenario but `revenue` has `direction=increase` and moved down significantly.

```
RESULTS
...
- revenue (guardrail, direction=increase): absolute_lift=−$0.85, significant=true — HARM SIGNAL.
  Revenue decreased significantly. Because the declared direction is "increase", this triggers
  a hold. See decision.recommendation=hold and decision.reasoning_summary for the full explanation.
```

### Example 4 — Bonferroni correction applied

**Input:** Payload has `analysis.bonferroni_correction_applied=true`, `analysis.secondary_alpha_used=0.0167`, three secondary metrics.

```
RESULTS
- conversion_rate (primary): absolute_lift=+0.018, p=0.008 < alpha=0.05, significant=true

Secondary metrics (Bonferroni correction applied: tested at alpha/3 = 0.0167, not 0.05):
- day1_retention: absolute_lift=+0.012, p=0.021 — not significant at adjusted alpha=0.0167
- day3_retention: absolute_lift=+0.009, p=0.041 — not significant at adjusted alpha=0.0167
- features_used: absolute_lift=+0.31, p=0.004 — significant at adjusted alpha=0.0167
```

---

## Notes for skill maintainers

- Always use `effective_primary_estimate` for the headline. Never use `primary_metric_result` directly for the final result statement.
- `alpha_used` is a field on `DecisionMemo`, not on `ExperimentConfig`. It is always safe to read from the payload without re-loading the config.
- The `decision.recommendation` enum values are: `ship`, `hold`, `rerun`, `inconclusive`. Do not map these to other words in the final memo.
- If `decision` is populated but `decision.key_caveats` and `decision.next_actions` are both empty lists, the memo sections CAVEATS and NEXT ACTIONS should be omitted entirely rather than showing empty bullets.
- **Guardrail direction (v1.1):** The `guardrail_directions` field on the config records the intended direction for each guardrail metric. The blocking logic is already applied by `abkit-core` — the `decision` fields are the final word. Skills must not re-implement the blocking rule; they should only surface the explanation already present in `decision.reasoning_summary` and `decision.key_caveats`.
- When a guardrail was significant but moved in the **correct** direction (e.g., `refund_rate` dropped significantly when `direction=decrease`), the decision will be `ship` and the caveats will not flag the guardrail as a problem. The memo should note the direction check passed, not flag a false alarm.
- **Bonferroni correction (v1.2):** The `analysis.bonferroni_correction_applied` flag indicates whether secondary metrics were tested at a stricter threshold. When `true`, use `analysis.secondary_alpha_used` (not `decision.alpha_used`) for secondary-metric significance statements. Primary and guardrail metrics always use `decision.alpha_used`.
- The v1 constraint (fixed-horizon frequentist analysis, exactly two variants) applies to all payloads this skill reads. Do not suggest sequential testing or Bayesian analysis even when discussing results.
- Audience selection for the memo (analyst / product_manager / executive) is the only value this skill may request interactively. All statistical values must come from the payload.
