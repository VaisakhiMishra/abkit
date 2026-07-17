# Consuming abkit Results — Agent Guide

> **Schema version:** 1.2
> **Applies to:** `ResultPayload` (JSON) and the exported memo text
> **Source of truth:** `abkit-core/src/abkit_core/schemas.py`

---

## Overview

Every abkit analysis run produces two consumable outputs:

| Output | Format | Primary use |
|---|---|---|
| `ResultPayload` | JSON object | Structured, machine-readable; drive automated review, filtering, and routing |
| Memo text | Markdown string | Human-readable narrative; paste into documents, issue trackers, or chat |

Skills and templates must read from these outputs. They must not re-implement the statistical logic that produced them.

---

## Input modes for skills that consume results

### When the ResultPayload is available

Paste or attach the full JSON. Skills read all values from it and do not prompt for information already present in the payload.

### When the ResultPayload is not yet available

Skills that require a `ResultPayload` (prelaunch-qa and results-memo) **cannot** produce a meaningful output without it. The payload contains statistical values (p-values, effect sizes, SRM results, decisions) that are computed by abkit-core and cannot be supplied by hand.

When the payload is absent, the skill must:
1. State clearly that the `ResultPayload` is required.
2. Explain how to obtain it: run the abkit pipeline with assignment and metrics CSVs uploaded.
3. Not produce a partial or estimated output.

**The one exception** is the `spec-review` skill, which accepts an `ExperimentConfig` interactively. See [`spec-review/README.md`](../spec-review/README.md) for how missing required fields are collected before the review runs.

### What must never be requested interactively

Skills must never ask the user to provide the following values by hand:
- `p_value`, `ci_lower`, `ci_upper`, `is_significant` from any `MetricEstimate`
- `srm_check.severity`, `srm_check.p_value`, `srm_check.explanation`
- `decision.recommendation`, `decision.reasoning_summary`
- `analysis.bonferroni_correction_applied`, `analysis.secondary_alpha_used`
- Any value that is computed by `abkit-core` and stored in the payload

The only interactive inputs that are acceptable are:
- `ExperimentConfig` field values (for `spec-review` in interactive mode)
- Target audience for the memo (`"analyst"` / `"product_manager"` / `"executive"`)

---

## Part 1 — The `ResultPayload` JSON

### Top-level fields

```
schema_version    string    "1.0" for legacy payloads; "1.1" for payloads produced by schema v1.1 configs; "1.2" for payloads produced by schema v1.2+ configs (includes Bonferroni correction fields).
experiment_id     string    Stable identifier — use this to correlate runs.
run_id            string    Unique identifier for this specific analysis run.
status            string    "success" | "warning" | "failed"
generated_at      datetime  ISO 8601 timestamp.
spec_validation   object    Config validation output. Always present.
duration_plan     object    Three-scenario duration estimate. Null if planning inputs were absent.
quality_checks    object    SRM, balance, missingness, join integrity, guardrail status. Null if no data was uploaded.
analysis          object    Statistical results. Null if analysis was not run.
decision          object    Recommendation and narrative. Null if analysis was not run.
artifacts         object    Optional export paths or serialized content.
```

### Reading `status`

Check `status` first. Use it to gate downstream actions:

```
"success"   All required checks passed with no blocking issues.
"warning"   Run completed but one or more non-blocking issues were found. Read quality_checks and spec_validation for details.
"failed"    A blocking error was encountered. The payload may be incomplete. Read spec_validation.errors for the cause.
```

### Reading `spec_validation`

```
is_valid          boolean   True when the config passed all validation rules.
errors            array     DiagnosticIssue objects with severity "error". Empty when is_valid is true.
warnings          array     DiagnosticIssue objects with severity "warning".
normalized_config object    The parsed ExperimentConfig that was actually used. Use this rather than re-reading the raw config file.
```

Each `DiagnosticIssue` has:

```
code      string         Stable machine-readable code, e.g. "MISSING_METRIC_TYPE".
severity  string         "info" | "warning" | "error"
message   string         Human-readable explanation.
field     string | null  Name of the offending field or column, when applicable.
details   object | null  Structured data for programmatic use.
```

### Reading `quality_checks`

```
srm_check              object   Full SRM diagnostic. Null if no assignment data was uploaded.
balance_checks         array    DiagnosticIssue list from pre-experiment covariate balance tests.
missingness_checks     array    DiagnosticIssue list for missing metric values.
join_integrity_checks  array    DiagnosticIssue list for assignment-to-metric join failures.
guardrail_status       array    DiagnosticIssue list for guardrail metric outcomes.
```

The `srm_check` object:

```
severity            string   "pass" | "warning" | "critical"
chi2_stat           number   Chi-squared test statistic.
p_value             number   p-value from chi-squared test.
srm_alpha_used      number   Threshold actually applied (per-experiment override or global 0.01).
observed_counts     object   Map of variant → observed assignment count.
expected_counts     object   Map of variant → expected count from config allocation.
observed_allocation object   Map of variant → observed fraction.
expected_allocation object   Map of variant → expected fraction from config.
max_absolute_drift  number   Largest absolute difference between observed and expected fractions.
explanation         string   Plain-English interpretation already written by abkit-core.
issues              array    DiagnosticIssue list for downstream filtering.
```

**Trust gate rule:** If `srm_check.severity == "critical"`, a `ship` recommendation is not warranted. Surface the `explanation` text and the `max_absolute_drift` value to the reader before discussing results.

### Reading `spec_validation.normalized_config.guardrail_directions`

This field (added in schema v1.1) is an optional map of guardrail metric name → desired direction.
It lives on the `ExperimentConfig` object embedded inside `spec_validation.normalized_config`.

```
guardrail_directions   object   Map of metric name → "increase" | "decrease" | "flat".
                                 Empty when the experiment owner did not declare any directions.
```

Allowed direction values:

| Value | Meaning |
|---|---|
| `increase` | The metric should go up. A significant negative lift blocks. |
| `decrease` | The metric should go down. A significant positive lift blocks. |
| `flat` | No movement desired. Any significant change blocks (same as undeclared). |

When a guardrail metric is **not** listed in this map, the legacy behaviour applies:
any statistically significant movement triggers a hold.

**Do not re-implement this logic.** The `decision.recommendation` and `decision.reasoning_summary`
fields already reflect the correct direction-aware outcome. Read those fields instead.

### Reading `analysis`

```
primary_metric_result         object         MetricEstimate for the primary metric (raw).
effective_primary_estimate    object         The estimate actually used for the decision: CUPED-adjusted if CUPED was applied, raw otherwise.
secondary_metric_results      array          MetricEstimate list for secondary metrics.
guardrail_metric_results      array          MetricEstimate list for guardrail metrics.
cuped_estimate                object         CUPED-adjusted MetricEstimate. Null if CUPED was not applied.
cuped_readiness               object         CupedReadiness assessment. Null if no pre-period data was provided.
segment_summaries             array          Per-segment result objects.
secondary_alpha_used          number | null  The significance threshold applied to secondary metrics.
                                             Equals config.alpha when correction is off or m=1.
                                             Equals config.alpha / m when Bonferroni correction is on and m >= 2.
                                             Null when no secondary metrics are declared.
bonferroni_correction_applied boolean        True when Bonferroni correction was enabled AND m >= 2 secondary
                                             metrics were declared (i.e. the threshold actually differs from alpha).
                                             False in all other cases.
```

**Bonferroni correction:** When `bonferroni_correction_applied` is `true`, secondary metrics in `secondary_metric_results` have their `is_significant` flag set against `secondary_alpha_used`, not against `decision.alpha_used`. Use `secondary_alpha_used` when writing statements like "significant at alpha/3=0.0167" for secondary metrics. The primary metric and guardrail metrics are always evaluated at `decision.alpha_used`.

Each `MetricEstimate` has:

```
metric_name      string          Canonical metric name.
metric_type      string          "proportion" | "continuous"
control_mean     number          Mean value in the control variant.
treatment_mean   number          Mean value in the treatment variant.
absolute_lift    number          treatment_mean minus control_mean.
relative_lift    number | null   Fractional change: absolute_lift / control_mean.
                                 Null when control_mean is zero — the ratio is undefined,
                                 not zero. Renderers must show "—" or "undefined", never
                                 substitute 0.
p_value          number | null   Null when inference was not run.
ci_lower         number | null   Lower bound of the confidence interval.
ci_upper         number | null   Upper bound of the confidence interval.
is_significant   bool | null     True when p_value < alpha_used for this metric's role:
                                 - Primary: uses decision.alpha_used (config.alpha).
                                 - Secondary: uses analysis.secondary_alpha_used
                                   (config.alpha / m when Bonferroni correction applies,
                                   otherwise config.alpha).
                                 - Guardrail: uses decision.alpha_used (config.alpha).
```

**Which estimate to use:** Always read `effective_primary_estimate`, not `primary_metric_result`, when reporting the headline result. `abkit-core` sets this field to the CUPED-adjusted estimate when CUPED was applied, so consuming code never needs to repeat that selection logic.

**Reading guardrail direction context:** When `analysis.guardrail_metric_results` contains a significant metric that caused a hold, the decision object's `reasoning_summary` and `key_caveats` already include the direction explanation produced by `abkit-core`. Do not re-derive blocking logic from the raw metric values; read the decision fields directly.

The `cuped_readiness` object (when present):

```
recommendation              string   "recommended" | "optional" | "not_recommended"
matched_coverage            number   Fraction of post-period units with pre-period data (0–1).
pre_post_correlation        number   Pearson r between pre and post values (−1 to 1).
estimated_variance_reduction number  Approximate fractional variance reduction from CUPED (0–1).
explanation                 string   Plain-English interpretation written by abkit-core.
caveats                     array    Specific conditions flagged (e.g. low overlap).
```

### Reading `decision`

```
recommendation    string   "ship" | "hold" | "rerun" | "inconclusive"
reasoning_summary string   Plain-English explanation of how the recommendation was reached.
key_caveats       array    List of important qualifications.
next_actions      array    Suggested follow-up steps.
alpha_used        number   The significance threshold from the config that drove this decision.
```

Use `alpha_used` when rendering statements like "p=0.02 < alpha=0.05" so the skill does not need to re-read the config.

### Reading `duration_plan`

```
required_n_per_variant  integer   Estimated sample size per variant from the power calculation.
required_n_total        integer   Total required sample size across all variants.
daily_eligible_traffic  integer   Eligible traffic per day used in calculations.
optimistic_days         integer   Fastest plausible duration (no ramp, no buffer).
planned_days            integer   Central estimate (ramp-up applied, traffic cap applied).
conservative_days       integer   Longest estimate (planning buffer applied on top of planned).
assumptions             object    Echo of inputs: traffic_cap, ramp_up_days, planning_buffer_pct, eligibility_rate.
caveats                 array     Plain-language planning notes.
```

These three day estimates always satisfy: `optimistic_days ≤ planned_days ≤ conservative_days`.

---

## Part 2 — The Memo Text

The memo text is a Markdown string exported from the Streamlit app's Memo page. It contains the same information as the `decision` object in human-readable prose. Use it when:

- The consumer needs formatted text rather than JSON (documents, issue trackers, Slack).
- You are drafting a decision memo and want to refine rather than rewrite the narrative.
- The JSON payload is unavailable but the exported `.md` file is.

When both are available, prefer the JSON payload for any conditional logic, and use the memo text only for prose output.

The memo text is not a schema source. Do not parse values out of memo text when the same value is available in the JSON.

---

## Part 3 — What skills must not do

- Do not re-run statistical tests. Read `analysis.effective_primary_estimate` and work from those values.
- Do not re-classify SRM severity. Read `quality_checks.srm_check.severity` directly.
- Do not re-derive the CUPED recommendation. Read `analysis.cuped_readiness.recommendation`.
- Do not invent fields that do not exist in the schema.
- Do not treat a `null` sub-object as an empty pass. A `null` `quality_checks` means no data was uploaded, not that checks passed.

---

## Part 4 — Null safety pattern

Before reading any sub-object, check whether it is null:

```python
# Python example
qc = payload.get("quality_checks")
if qc is None:
    # data was not uploaded; do not report QA results
    pass

srm = (qc or {}).get("srm_check")
if srm and srm["severity"] == "critical":
    # surface trust warning before discussing results
    pass
```

The same pattern applies in any language or prompt: always test for null before reading nested fields.

---

## Part 5 — Stable issue codes

These codes appear in `DiagnosticIssue.code` across all check types. Use them for filtering:

| Code | Meaning |
|---|---|
| `MISSING_METRIC_TYPE` | `metric_type` was absent when analysis was requested |
| `ALLOCATION_SUM` | `expected_allocation` values do not sum to 1.0 |
| `ALLOCATION_KEYS` | Allocation keys do not match declared variants |
| `DUPLICATE_VARIANT` | Variant names are not unique |
| `PRIMARY_METRIC_IN_GUARDRAILS` | Primary metric also listed as a guardrail |
| `ALPHA_OUT_OF_RANGE` | `alpha` is unusually high (≥ 0.1) |
| `POWER_OUT_OF_RANGE` | `power` is unusually low (< 0.7) |
| `UNKNOWN_VARIANT_IN_ASSIGNMENT` | Assignment data contains a variant not in config |
| `MISSING_REQUIRED_CSV_COLUMNS` | A required CSV column is absent |
| `GUARDRAIL_DIRECTION_MISMATCH` | Reserved for future use; a direction check produced a conflict |
