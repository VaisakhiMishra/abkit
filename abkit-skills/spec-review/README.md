# Skill: Experiment Spec Review

> **Schema version:** 1.0  
> **Layer:** `abkit-skills/spec-review`  
> **Source of truth for field names:** `abkit-core/src/abkit_core/schemas.py`

---

## Purpose

Review a proposed experiment config before launch. Identify missing required fields, weak statistical assumptions, ambiguous metric definitions, and structural issues that will cause downstream failures.

This skill reads a config file or object and produces a structured review. It does not run statistical tests and does not modify the config.

---

## Inputs

| Input | Required | Type | Notes |
|---|---|---|---|
| Experiment config | Yes | YAML or JSON matching `ExperimentConfig` schema | Provide the raw file or paste the object |
| `spec_validation` from `ResultPayload` | Optional | JSON sub-object | When abkit has already validated the config, include this to avoid repeating checks |
| Product context | Optional | Free text | Brief background on the feature or hypothesis |

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

---

## Prompt template

Use this template verbatim. Replace the `{{...}}` placeholders with the actual values before sending.

```
You are an experiment design reviewer for an A/B testing team.

You will be given an experiment config object that conforms to the abkit ExperimentConfig schema (version 1.0).
Your task is to review the config and produce a structured spec review.

---
EXPERIMENT CONFIG:
{{paste the YAML or JSON config here}}
---
{% if spec_validation is provided %}
SPEC VALIDATION OUTPUT (from abkit-core):
{{paste the spec_validation JSON object here}}
{% endif %}
{% if product_context is provided %}
PRODUCT CONTEXT:
{{paste any background notes here}}
{% endif %}
---

Review the config and produce the following four sections.

SECTION 1 — MISSING OR INVALID FIELDS
List any required fields that are absent or that fail the validation rules below.
Required fields: schema_version, experiment_name, experiment_id, owner, hypothesis,
primary_metric, variants (min 2), expected_allocation (keys must match variants, values must sum to 1.0),
unit_of_randomization, alpha (0 < alpha < 1), power (0 < power < 1), mde (> 0).
Additional rules:
- variant names must be unique
- primary_metric must not appear in guardrail_metrics
- alpha >= 0.1 warrants a warning (unusually high)
- power < 0.7 warrants a warning (unusually low)
- srm_alpha, if present, must be between 0.001 and 0.1
- eligibility_rate and traffic_cap, if present, must be between 0 (exclusive) and 1 (inclusive)
If spec_validation was provided and lists errors, include those errors here verbatim.
If there are no missing or invalid fields, write: "No missing or invalid required fields."

SECTION 2 — DESIGN RISKS
List optional fields that are absent and whose absence creates a planning or analysis risk.
For each risk, name the missing field and explain the consequence.
Focus only on these fields: metric_type, daily_eligible_traffic, secondary_metrics, guardrail_metrics,
pre_period_window_days, analysis_window_days, eligibility_rate, exclusion_rules.
If metric_type is absent: note that analysis cannot be run until it is set.
If daily_eligible_traffic is absent: note that no duration estimate can be produced.
If guardrail_metrics is empty: note that no harm-detection metrics are declared.
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
- "needs_minor_fixes": Required fields are present but one or more optional fields are missing in ways that create planning risk or the config has warnings.
- "needs_rework": One or more required fields are missing or invalid.
Do not add qualitative commentary beyond a single sentence after the verdict.
```

---

## Example output structure

```
SECTION 1 — MISSING OR INVALID FIELDS
- metric_type is absent. Analysis cannot be run until this is set to "proportion" or "continuous".
- expected_allocation keys {"control": 0.6, "variant_a": 0.4} do not match declared variants ["control", "treatment"].

SECTION 2 — DESIGN RISKS
- daily_eligible_traffic is not set. No duration estimate can be produced without it.
- guardrail_metrics is empty. No harm-detection metrics are declared. Consider adding at least one guardrail.

SECTION 3 — SUGGESTED CLARIFICATIONS
- The hypothesis mentions "reduce latency" but the primary_metric is "conversion_rate". Should latency be the primary metric or a guardrail?
- unit_of_randomization is "session_id". The metric "revenue_per_user" aggregates at the user level. Is this intentional or should the unit be "user_id"?

SECTION 4 — READINESS SUMMARY
needs_rework — Two required-field violations must be resolved before this config can be used for analysis.
```

---

## Notes for skill maintainers

- Field names in the prompt must match `ExperimentConfig` exactly. Do not paraphrase field names.
- The validation rules in Section 1 mirror `validate_config()` in `schemas.py`. If that function changes, update the prompt.
- The `effective_primary_estimate` field is on `AnalysisResult`, not `ExperimentConfig`. Do not reference it in this skill.
