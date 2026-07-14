# Schemas

## Principles

Schemas should be explicit, minimal, and shared across the core library, app, skills, and templates.

Each schema should define:

- Required fields.
- Optional fields.
- Allowed values where practical.
- Validation expectations.
- A version field when serialized to JSON or YAML.

## Experiment config schema

The experiment config is the primary control object for an analysis run.

### Required fields

| Field | Type | Description |
|---|---|---|
| `schema_version` | string | Version of the config structure. |
| `experiment_name` | string | Human-readable name of the experiment. |
| `experiment_id` | string | Stable identifier for the experiment. |
| `owner` | string | Primary owner or team. |
| `hypothesis` | string | Short statement of expected impact. |
| `primary_metric` | string | Canonical name of the main success metric. |
| `metric_type` | string | `proportion` or `continuous`. Determines which statistical test to use. Required for analysis runs; validated as a `DiagnosticIssue` error at `error` severity if absent when analysis is requested. May be omitted for spec-validation-only runs. |
| `variants` | array of strings | Allowed variant names, such as `control` and `treatment`. |
| `expected_allocation` | object | Planned traffic split by variant. |
| `unit_of_randomization` | string | Unit assigned to variants, such as `user_id` or `session_id`. |
| `alpha` | number | Significance threshold used for planning or reporting. |
| `power` | number | Desired power for planning. |
| `mde` | number | Minimum detectable effect for planning. |

### Optional fields

| Field | Type | Description |
|---|---|---|
| `secondary_metrics` | array of strings | Additional metrics of interest. |
| `guardrail_metrics` | array of strings | Metrics used to detect harmful side effects. |
| `segments` | array of strings | Segment dimensions to analyze, such as country or device. |
| `pre_period_window_days` | integer | Number of days used for CUPED covariates. |
| `analysis_window_days` | integer | Planned experiment window. |
| `srm_alpha` | number | Per-experiment SRM detection threshold. Overrides the global default of 0.01. Must be between 0.001 and 0.1. |
| `daily_eligible_traffic` | integer | Expected number of eligible units entering the experiment per day. Used by the duration planner. |
| `eligibility_rate` | number | Fraction of total traffic that is eligible. Between 0 and 1. Used as a modifier in duration planning when `daily_eligible_traffic` is not provided directly. |
| `traffic_cap` | number | Maximum fraction of eligible traffic to route to the experiment. Between 0 and 1. Defaults to 1.0 if not set. |
| `ramp_up_days` | integer | Number of days before full traffic allocation is reached. Treated as a planning buffer, not a statistical model. |
| `planning_buffer_pct` | number | Additional percentage buffer to add to the planned duration as a safety margin. For example, 0.2 means add 20 percent. |
| `exclusion_rules` | array of strings | Plain-language exclusions or filter notes. |
| `notes` | string | Free-form context. |

### Validation expectations

- Variant names must be unique.
- Expected allocations must sum to 1.0 within a small tolerance.
- `primary_metric` must not also be listed as a guardrail unless that behavior is explicitly allowed.
- `alpha`, `power`, and `mde` must be positive and within reasonable ranges.
- `srm_alpha`, if provided, must be between 0.001 and 0.1.
- `eligibility_rate` and `traffic_cap`, if provided, must be between 0 and 1 exclusive.
- `planning_buffer_pct`, if provided, must be non-negative.
- The declared randomization unit should match the assignment file headers.
- `metric_type` must be `proportion` or `continuous`.
- A missing `metric_type` is valid at spec-validation time but must produce a `DiagnosticIssue` with `severity: error` and code `MISSING_METRIC_TYPE` if an analysis run is attempted without it.

### Example

```yaml
schema_version: "1.0"
experiment_name: "Checkout button redesign"
experiment_id: "exp_checkout_btn_001"
owner: "growth-data-science"
hypothesis: "A more prominent checkout CTA will increase conversion rate."
primary_metric: "conversion_rate"
metric_type: "proportion"
secondary_metrics:
  - "revenue_per_user"
guardrail_metrics:
  - "refund_rate"
variants:
  - "control"
  - "treatment"
expected_allocation:
  control: 0.5
  treatment: 0.5
unit_of_randomization: "user_id"
alpha: 0.05
power: 0.8
mde: 0.02
srm_alpha: 0.01                 # optional — defaults to global 0.01
pre_period_window_days: 14
analysis_window_days: 14
daily_eligible_traffic: 5000
eligibility_rate: 0.6
traffic_cap: 1.0
ramp_up_days: 2
planning_buffer_pct: 0.15
segments:
  - "country"
  - "device_type"
```

## Assignment CSV schema

The assignment file captures who was assigned to which variant and when.

### Required columns

| Column | Type | Description |
|---|---|---|
| `experiment_id` | string | Experiment identifier. |
| `unit_id` | string | Randomization unit identifier. |
| `variant` | string | Assigned variant. |
| `assignment_ts` | timestamp | Assignment timestamp. |

### Optional columns

| Column | Type | Description |
|---|---|---|
| `exposed` | boolean | Whether the unit was actually exposed. |
| `exposure_ts` | timestamp | Exposure timestamp, if different from assignment. |
| `country` | string | Segment field. |
| `device_type` | string | Segment field. |
| `tenure_bucket` | string | Segment field. |
| `is_eligible` | boolean | Whether the unit met eligibility rules. |

### Validation expectations

- Each `unit_id` should map to one stable variant for the experiment.
- Assignment timestamps must be parseable.
- Variants must belong to the allowed config variant set.
- Missing or duplicate assignments should be flagged.
- The observed allocation should be compared with expected allocation for SRM detection.

### Example CSV header

```text
experiment_id,unit_id,variant,assignment_ts,exposed,exposure_ts,country,device_type
```

## Metric CSV schema

The metric file captures analysis-period and optionally pre-period values for each unit.

### Required columns

| Column | Type | Description |
|---|---|---|
| `experiment_id` | string | Experiment identifier. |
| `unit_id` | string | Unit identifier joining to the assignment file. |
| `metric_name` | string | Canonical metric name. |
| `metric_value` | number | Observed metric value. |
| `period` | string | Either `pre` or `post`. |

### Optional columns

| Column | Type | Description |
|---|---|---|
| `metric_ts` | timestamp | Observation timestamp. |
| `is_guardrail` | boolean | Whether the metric is a guardrail. |
| `country` | string | Segment field. |
| `device_type` | string | Segment field. |
| `currency` | string | Optional context for revenue metrics. |

### Validation expectations

- `period` values should be constrained to supported values.
- `metric_name` should match names declared in the config or be intentionally allowed extras.
- `metric_value` must be numeric for the current v1 scope.
- For CUPED readiness, both `pre` and `post` values should exist for enough matched units to estimate a stable adjustment coefficient.

### Example CSV header

```text
experiment_id,unit_id,metric_name,metric_value,period,metric_ts,country,device_type
```

## Result payload schema

The result payload is the canonical output of an analysis run. It should support app rendering, markdown export, and future skill consumption.

### Top-level fields

| Field | Type | Description |
|---|---|---|
| `schema_version` | string | Version of the result schema. |
| `experiment_id` | string | Experiment identifier. |
| `run_id` | string | Unique identifier for the analysis run. |
| `status` | string | Overall status such as `success`, `warning`, or `failed`. |
| `generated_at` | timestamp | Result generation time. |
| `spec_validation` | object | Output from config validation. |
| `quality_checks` | object | Assignment and metric QA results. |
| `analysis` | object | Statistical result summaries. |
| `decision` | object | Suggested decision language. |
| `artifacts` | object | Optional paths or serialized exports. |

### `spec_validation`

Should include:

- `is_valid`
- `errors`
- `warnings`
- `normalized_config`

### `quality_checks`

Should include:

- `srm_check`
- `balance_checks`
- `missingness_checks`
- `join_integrity_checks`
- `guardrail_status`

#### `srm_check` object

The SRM check is one of the core trust gates in the system. It must not rely on p-value alone. The full object is:

| Field | Type | Description |
|---|---|---|
| `severity` | string | `pass`, `warning`, or `critical`. Determined by both p-value and observed drift (see rules below). |
| `chi2_stat` | number | Chi-squared test statistic. |
| `p_value` | number | p-value from the chi-squared test. |
| `srm_alpha_used` | number | The threshold actually applied: per-experiment `srm_alpha` if set, otherwise global default 0.01. |
| `observed_counts` | object | Map of variant name to observed assignment count. |
| `expected_counts` | object | Map of variant name to expected count derived from `expected_allocation`. |
| `observed_allocation` | object | Map of variant name to observed fraction of total assignments. |
| `expected_allocation` | object | Map of variant name to expected fraction from config. |
| `max_absolute_drift` | number | Largest absolute difference between observed and expected fractions across all variants. |
| `explanation` | string | Human-readable plain-English explanation of what the check found and what it means. |
| `issues` | array of `DiagnosticIssue` | Zero or more structured issue objects for downstream consumers. |

**Severity classification rules:**

| Condition | Severity |
|---|---|
| p-value ≥ `srm_alpha_used` AND `max_absolute_drift` < 0.01 | `pass` |
| p-value < `srm_alpha_used` AND `max_absolute_drift` < 0.02 | `warning` — statistically significant but drift is small |
| p-value < `srm_alpha_used` AND `max_absolute_drift` ≥ 0.02 | `critical` — both p-value and drift indicate a trust problem |
| p-value ≥ `srm_alpha_used` AND `max_absolute_drift` ≥ 0.01 | `warning` — drift is visible even without statistical significance |

The `critical` level should block a `ship` recommendation in the decision object. The `warning` level should surface a caveat but not block shipping on its own.

### `analysis`

Should include:

- `primary_metric_result`
- `effective_primary_estimate`
- `secondary_metric_results`
- `guardrail_metric_results`
- `cuped_estimate` when applicable.
- `segment_summaries`
- `cuped_readiness` — the `CupedReadiness` object described below, always present when pre-period data was provided.

#### `CupedReadiness` object

| Field | Type | Description |
|---|---|---|
| `recommendation` | string | One of `recommended`, `optional`, or `not_recommended`. |
| `matched_coverage` | number | Fraction of post-period units that also have pre-period data. Between 0 and 1. |
| `pre_post_correlation` | number | Pearson r between pre-period and post-period metric values for matched units. |
| `estimated_variance_reduction` | number | Estimated fractional reduction in variance if CUPED is applied: `1 - (1 - r²)`. Between 0 and 1. |
| `explanation` | string | Human-readable explanation of the recommendation and the values that drove it. |
| `caveats` | array of strings | Specific conditions worth flagging, such as low sample overlap or near-zero correlation. |

**Recommendation tier rules:**

| Condition | Recommendation |
|---|---|
| `matched_coverage` ≥ 0.8 AND `pre_post_correlation` ≥ 0.3 | `recommended` — strong potential; apply CUPED |
| `matched_coverage` ≥ 0.5 AND `pre_post_correlation` ≥ 0.1 | `optional` — modest potential; analyst judgment required |
| Any other combination | `not_recommended` — coverage or correlation too low to justify the added complexity |

The thresholds are defaults. The `explanation` field must always state the actual values so an analyst can apply their own judgment regardless of the tier label.

Note: `estimated_variance_reduction` is computed from correlation as `1 - (1 - r²)` and is an approximation. It assumes the relationship between pre and post is approximately linear and that the CUPED coefficient θ = Cov(pre, post) / Var(pre) is stable. These assumptions are stated in the explanation text when CUPED is applied.

### `decision`

Should include:

- `recommendation` such as `ship`, `hold`, `rerun`, or `inconclusive`
- `reasoning_summary`
- `key_caveats`
- `next_actions`

### `duration_plan` object

The duration plan is produced by the planner before or independently of a live analysis run. It is also embedded in the result payload under a top-level `duration_plan` field when planning inputs are available.

| Field | Type | Description |
|---|---|---|
| `required_n_per_variant` | integer | Estimated required sample size per variant from the power calculation. |
| `required_n_total` | integer | Total required sample size across all variants. |
| `daily_eligible_traffic` | integer | Eligible traffic per day used in calculations, sourced from config or provided directly. |
| `optimistic_days` | integer | Duration assuming immediate full ramp and no buffer. `ceil(required_n_total / daily_eligible_traffic)`. |
| `planned_days` | integer | Duration with ramp-up days added and traffic cap applied: `ceil(required_n_total / (daily_eligible_traffic * traffic_cap)) + ramp_up_days`. |
| `conservative_days` | integer | Duration with planning buffer applied on top of planned: `ceil(planned_days * (1 + planning_buffer_pct))`. |
| `assumptions` | object | Echo of the inputs used: `traffic_cap`, `ramp_up_days`, `planning_buffer_pct`, `eligibility_rate`. |
| `caveats` | array of strings | Plain-language notes about novelty effects, seasonality, eligibility rate uncertainty, or other factors not modeled. |

**Computation rules:**

1. If `daily_eligible_traffic` is not provided directly but `eligibility_rate` is available, the system must surface a `DiagnosticIssue` at `warning` severity indicating that total daily traffic is needed to derive the eligible count. It does not fabricate a number.
2. `traffic_cap` defaults to 1.0 when not set.
3. `ramp_up_days` defaults to 0 when not set.
4. `planning_buffer_pct` defaults to 0 when not set.
5. Novelty effects are never modeled numerically. When `ramp_up_days` > 0, the `caveats` array must include a note that ramp-up days are treated as a conservative planning margin, not a novelty-effect model.
6. All three duration estimates must be integers (ceiling). They must satisfy `optimistic_days ≤ planned_days ≤ conservative_days`.

## Updated `ResultPayload` top-level fields

The full set of top-level fields in the result payload is:

| Field | Type | Description |
|---|---|---|
| `schema_version` | string | Version of the result schema. |
| `experiment_id` | string | Experiment identifier. |
| `run_id` | string | Unique identifier for the analysis run. |
| `status` | string | Overall status: `success`, `warning`, or `failed`. |
| `generated_at` | timestamp | Result generation time. |
| `spec_validation` | object | Output from config validation. |
| `duration_plan` | object | `DurationPlan` object when planning inputs are available. Null otherwise. |
| `quality_checks` | object | Assignment and metric QA results including the full `srm_check` object. |
| `analysis` | object | Statistical result summaries including `cuped_readiness` when pre-period data is present. |
| `decision` | object | Suggested decision with recommendation, reasoning, caveats, and next actions. |
| `artifacts` | object | Optional paths or serialized exports. |

## Diagnostic issue schema

A standard issue object should be reusable across validation and QA.

| Field | Type | Description |
|---|---|---|
| `code` | string | Stable machine-readable issue code. |
| `severity` | string | `info`, `warning`, or `error`. |
| `message` | string | Human-readable explanation. |
| `field` | string or null | Field or column involved. |
| `details` | object | Optional structured details. |

## Schema change policy

- New optional fields are acceptable in minor versions.
- Breaking field renames require a version bump.
- Example files in templates should always reflect the latest stable schema.
