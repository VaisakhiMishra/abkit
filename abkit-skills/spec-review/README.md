# Skill: Experiment Spec Review

> **Schema version:** 1.2
> **Layer:** `abkit-skills/spec-review`
> **Source of truth for field names:** `abkit-core/src/abkit_core/schemas.py`

---

## Purpose

Review a proposed experiment config before launch. Identify missing required fields, weak statistical assumptions, ambiguous metric definitions, and structural issues that will cause downstream failures.

This skill reads a config file or object and produces a structured review. It does not run statistical tests and does not modify the config.

---

## Two supported input modes

### Mode A — Config file provided

Paste or attach the full `ExperimentConfig` YAML or JSON. The skill parses it, validates it against the schema, and produces the spec review. It does not re-ask for values already present in the file.

### Mode B — Interactive (no config file)

State what you know about the experiment. The skill checks which required fields are missing, asks for each one before proceeding, and resumes only when all required values have been provided.

**When required fields are missing, the skill stops and returns only the missing-field list. It does not produce a partial review.**

---

## Inputs

| Input | Required | Type | Notes |
|---|---|---|---|
| Experiment config | **Yes — or provide interactively** | YAML or JSON matching `ExperimentConfig` schema | Paste the full file, or omit and answer the follow-up questions |
| `spec_validation` from `ResultPayload` | Optional | JSON sub-object | When abkit has already validated the config, include this to avoid repeating checks |
| Product context | Optional | Free text | Brief background on the feature or hypothesis |

### Required fields in `ExperimentConfig`

If these fields are absent from the config, the skill pauses and asks for them:

| Field | What it controls | Why the spec review needs it |
|---|---|---|
| `schema_version` | Selects the validation rule set | Must be `"1.2"` to apply current rules |
| `experiment_name` | Human-readable identifier | Used in all output section headers |
| `experiment_id` | Stable slug | Used to correlate runs and CSV files |
| `owner` | Accountability | Documents who owns the experiment |
| `hypothesis` | Expected mechanism and direction | Used to assess metric-hypothesis alignment |
| `primary_metric` | The success metric | Required to check that it is not also a guardrail |
| `variants` | The two arms | Must be exactly two in v1; first is control, second is treatment |
| `expected_allocation` | Traffic split | Keys must match `variants`; values must sum to 1.0 |
| `unit_of_randomization` | Randomization unit | Required to check metric-unit alignment |
| `alpha` | Significance threshold | Required to evaluate whether the value is in an acceptable range |
| `power` | Statistical power | Required to flag under-powered designs |
| `mde` | Minimum detectable effect | Required for duration planning and power assessment |

`metric_type` (`"proportion"` or `"continuous"`) is required when the spec is reviewed for analysis readiness. It may be absent for planning-only reviews, but the skill flags it as a blocking design risk.

---

## Outputs

The skill produces a structured review with four sections:

1. **Missing or invalid fields** — Required fields that are absent, incorrectly typed, or fail validation rules.
2. **Design risks** — Optional fields that are absent and whose absence creates planning or analysis risks (e.g. no `daily_eligible_traffic` means no duration estimate is possible).
3. **Suggested clarifications** — Questions the reviewer should answer before the experiment runs.
4. **Readiness summary** — One of: `ready`, `needs_minor_fixes`, `needs_rework`.

---

## Refusal conditions

- If the input cannot be parsed as an `ExperimentConfig`, report the parse error and stop. Do not guess field values.
- If `spec_validation` is provided and `is_valid` is `false`, list the `errors` from that object and do not contradict them.
- Do not invent metric definitions or suggested values for `alpha`, `power`, or `mde`.
- If required fields are missing and the user has not yet provided them, return only the missing-field list and do not proceed to the four review sections.

---

## Prompt template

Use this template verbatim. Replace the `{{...}}` placeholders with the actual values before sending.

```
You are an experiment design reviewer for an A/B testing team.

You support two input modes:
1. The user provides a complete ExperimentConfig file (YAML or JSON). Parse and validate it.
2. The user provides values interactively. Identify which required fields are missing, ask for them
   with justification, and resume only after all required values are supplied.

When required fields are missing, respond with ONLY the missing-field list below. Do not produce
any section of the spec review until all required fields are present.

Missing-field response format:
  The following required values are missing. Please provide them before I proceed.
  ---
  Missing field: <field_name>
  What it controls: <one sentence>
  Why this review needs it: <one sentence>
  Please provide: <exact format or allowed values>
  ---
  [repeat for each missing field]

---
{% if config is provided %}
EXPERIMENT CONFIG:
{{paste the YAML or JSON config here}}
{% endif %}
{% if spec_validation is provided %}
SPEC VALIDATION OUTPUT (from abkit-core):
{{paste the spec_validation JSON object here}}
{% endif %}
{% if product_context is provided %}
PRODUCT CONTEXT:
{{paste any background notes here}}
{% endif %}
---

Once all required fields are present, produce the following four sections.

SECTION 1 — MISSING OR INVALID FIELDS
List any required fields that are absent or that fail the validation rules below.
Required fields: schema_version, experiment_name, experiment_id, owner, hypothesis,
primary_metric, variants (min 2; first is control, second is treatment),
expected_allocation (keys must match variants, values must sum to 1.0),
unit_of_randomization, alpha (0 < alpha < 1), power (0 < power < 1), mde (> 0).
Additional rules:
- variant names must be unique
- primary_metric must not appear in guardrail_metrics
- alpha >= 0.1 warrants a warning (unusually high)
- power < 0.7 warrants a warning (unusually low)
- srm_alpha, if present, must be between 0.001 and 0.1
- eligibility_rate and traffic_cap, if present, must be between 0 (exclusive) and 1 (inclusive)
- guardrail_directions keys must exactly match names in guardrail_metrics
If spec_validation was provided and lists errors, include those errors here verbatim.
If there are no missing or invalid fields, write: "No missing or invalid required fields."

SECTION 2 — DESIGN RISKS
List optional fields that are absent and whose absence creates a planning or analysis risk.
For each risk, name the missing field and explain the consequence.
Focus only on these fields: metric_type, daily_eligible_traffic, secondary_metrics, guardrail_metrics,
pre_period_window_days, analysis_window_days, eligibility_rate, exclusion_rules, guardrail_directions,
apply_bonferroni_correction.
If metric_type is absent: note that analysis cannot be run until it is set to "proportion" or "continuous".
If daily_eligible_traffic is absent: note that no duration estimate can be produced.
If guardrail_metrics is empty: note that no harm-detection metrics are declared.
If guardrail_metrics is non-empty but guardrail_directions is absent or empty: note as a low-severity
design risk that direction-aware guardrail blocking will not be applied (any significant movement will trigger hold).
If secondary_metrics has two or more entries and apply_bonferroni_correction is false: note as an
informational item that Bonferroni correction is off; multiple secondary tests increase the family-wise
false-positive rate.
Do not invent risks beyond what the schema supports.

SECTION 3 — SUGGESTED CLARIFICATIONS
List 2 to 5 specific questions the experiment owner should answer before launch.
Questions must be grounded in the actual config values, not generic advice.
Examples of grounded questions:
- "The hypothesis says X; should the primary_metric be Y or Z?"
- "unit_of_randomization is 'session_id' but the metric appears to be a per-user measure — is this intentional?"
Do not ask questions that the config already answers clearly.

SECTION 4 — READINESS SUMMARY
State one of: "ready", "needs_minor_fixes", or "needs_rework".
- "ready": All required fields are present and valid; no blocking risks.
- "needs_minor_fixes": Required fields are present but one or more optional fields are missing in ways
  that create planning risk or the config has warnings.
- "needs_rework": One or more required fields are missing or invalid.
Do not add qualitative commentary beyond a single sentence after the verdict.
```

---

## Example outputs

### Example 1 — Full config file workflow

**Input:** A complete YAML config is provided.

```
SECTION 1 — MISSING OR INVALID FIELDS
No missing or invalid required fields.

SECTION 2 — DESIGN RISKS
- guardrail_metrics is empty. No harm-detection metrics are declared. Consider adding at least one guardrail.
- daily_eligible_traffic is not set. No duration estimate can be produced without it.

SECTION 3 — SUGGESTED CLARIFICATIONS
- The hypothesis mentions "reduce latency" but primary_metric is "conversion_rate". Should latency be
  the primary metric or a guardrail?
- unit_of_randomization is "session_id". The metric "revenue_per_user" aggregates at the user level.
  Is this intentional or should the unit be "user_id"?

SECTION 4 — READINESS SUMMARY
needs_minor_fixes — Required fields are all present, but no guardrail metrics are declared and
duration planning is not possible without daily_eligible_traffic.
```

### Example 2 — Partial-input workflow (missing required fields)

**Input:** The user says "I want to review my checkout redesign experiment. Primary metric is conversion_rate, alpha=0.05, power=0.80."

**Skill response (before producing any review sections):**

```
The following required values are missing. Please provide them before I proceed.

---
Missing field: schema_version
What it controls: Selects which validation rules apply to this config.
Why this review needs it: Without a schema version the review cannot confirm which field rules are active.
Please provide: The string "1.2" (current version).

---
Missing field: experiment_id
What it controls: Stable identifier used to correlate this config with CSV data.
Why this review needs it: The review documents the experiment ID in all output headers.
Please provide: A short slug, e.g. "exp_checkout_redesign_001".

---
Missing field: owner
What it controls: Accountability — the team or person responsible for the experiment.
Why this review needs it: Ownership must be declared before a spec is considered ready for launch.
Please provide: A team name or email, e.g. "growth-team".

---
Missing field: hypothesis
What it controls: The expected mechanism and direction of effect.
Why this review needs it: Used to check whether the primary metric matches the stated goal.
Please provide: A short statement, e.g. "Simplifying checkout will increase conversion rate by
reducing drop-offs at the payment step."

---
Missing field: variants
What it controls: The arm names for the experiment.
Why this review needs it: Required to validate expected_allocation keys and data column labels.
Please provide: Exactly two names — control first, treatment second. e.g. ["control", "treatment"].

---
Missing field: expected_allocation
What it controls: The planned traffic split between variants.
Why this review needs it: Values must sum to 1.0 and keys must match the variant names.
Please provide: A map of variant name to fraction, e.g. {control: 0.5, treatment: 0.5}.

---
Missing field: unit_of_randomization
What it controls: The unit assigned to variants (e.g. individual users vs. sessions).
Why this review needs it: Used to check whether the metric granularity matches the randomization unit.
Please provide: One of "user_id", "session_id", "device_id", or a custom unit name.

---
Missing field: mde
What it controls: Minimum detectable effect for the power calculation.
Why this review needs it: Required to assess whether the power setting is realistic for the stated effect size.
Please provide: A positive number. For proportion metrics this is a relative change (e.g. 0.02 = 2%).
```

### Example 3 — Guardrail directions example

**Input config includes:**
```yaml
guardrail_metrics:
  - "refund_rate"
  - "revenue"
guardrail_directions:
  refund_rate: "decrease"
  revenue: "increase"
```

**Skill notes in Section 2:**
```
SECTION 2 — DESIGN RISKS
- guardrail_directions is declared for refund_rate (decrease) and revenue (increase).
  Direction-aware blocking is active: refund_rate will only trigger a hold if it rises significantly;
  revenue will only trigger a hold if it falls significantly. No risk noted.
```

### Example 4 — Bonferroni example

**Input config includes:**
```yaml
secondary_metrics:
  - "day1_retention"
  - "day3_retention"
  - "features_used"
apply_bonferroni_correction: false
```

**Skill notes in Section 2:**
```
SECTION 2 — DESIGN RISKS
- apply_bonferroni_correction is false with 3 secondary metrics declared. With three simultaneous
  secondary tests at alpha=0.05, the family-wise false-positive rate is elevated. Consider setting
  apply_bonferroni_correction: true to test each secondary metric at alpha / 3 ≈ 0.0167.
  This is informational — the current config is valid.
```

---

## Notes for skill maintainers

- Field names in the prompt must match `ExperimentConfig` exactly. Do not paraphrase field names.
- The validation rules in Section 1 mirror `validate_config()` in `schemas.py`. If that function changes, update the prompt.
- The `effective_primary_estimate` field is on `AnalysisResult`, not `ExperimentConfig`. Do not reference it in this skill.
- The v1 constraint (exactly two variants, first is control, second is treatment, fixed-horizon frequentist only) must not be relaxed. Do not suggest sequential testing, Bayesian analysis, or more than two variants.
- `guardrail_directions` keys must be a subset of `guardrail_metrics` names. The schema enforces this; the skill must flag any mismatch found during review.
- When `apply_bonferroni_correction` is `true` but `secondary_metrics` has fewer than two entries, note in Section 2 that the correction has no effect (the schema allows this but it is a design note worth surfacing).
