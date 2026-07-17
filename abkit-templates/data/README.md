# abkit CSV input reference — v1.2

This directory contains minimal copy-paste-ready CSV templates for the two
upload inputs the abkit Streamlit app accepts on the **QA page**.

---

## assignments.csv

Each row is one unit assignment to a variant.

### Required columns

| Column | Type | Notes |
|---|---|---|
| `experiment_id` | string | Must match the `experiment_id` in the config YAML |
| `unit_id` | string | Unique identifier for the randomization unit (e.g. user_id) |
| `variant` | string | Must match one of the names listed in `variants` in the config |
| `assignment_ts` | ISO 8601 datetime | e.g. `2024-03-01T09:00:00` |

### Optional columns (read by QA checks, safe to omit)

| Column | Type | Notes |
|---|---|---|
| `exposed` | bool | `True`/`False` — whether the unit was exposed to the variant |
| `exposure_ts` | ISO 8601 datetime | Timestamp of first exposure |
| `country` | string | Segment dimension |
| `device_type` | string | Segment dimension |
| `tenure_bucket` | string | Segment dimension |
| `is_eligible` | bool | Whether the unit met eligibility criteria |

### Notes

- The QA layer reads `experiment_id`, `unit_id`, `variant`, and `assignment_ts` for all core checks.
- Duplicate rows for the same `unit_id` + `variant` emit a warning; conflicting rows (same `unit_id`, different `variant`) emit an error.
- Variant names are validated against `config.variants`.

### Template: [`assignments_template.csv`](assignments_template.csv)

```
experiment_id,unit_id,variant,assignment_ts,exposed,country,device_type
exp_YOUR_ID,u0001,control,2024-03-01T09:00:00,True,US,mobile
exp_YOUR_ID,u0002,treatment,2024-03-01T09:01:00,True,US,desktop
```

---

## metrics.csv

Each row is one metric observation for one unit in one period. The file uses
**long format**: one row per (unit × metric × period) combination.

### Required columns

| Column | Type | Notes |
|---|---|---|
| `experiment_id` | string | Must match the `experiment_id` in the config YAML |
| `unit_id` | string | Must match `unit_id` values in `assignments.csv` |
| `metric_name` | string | Must match the names declared in `primary_metric`, `secondary_metrics`, or `guardrail_metrics` in the config |
| `metric_value` | numeric | Observed value for this metric and unit |
| `period` | string | `"pre"` or `"post"`. Post rows are used for analysis; pre rows are used for CUPED adjustment only |

### Optional columns (safe to omit)

| Column | Type | Notes |
|---|---|---|
| `metric_ts` | ISO 8601 datetime | Timestamp of the observation |
| `is_guardrail` | bool | Legacy — not read by the analysis layer |
| `country` | string | Segment dimension |
| `device_type` | string | Segment dimension |
| `currency` | string | Currency code if the metric is monetary |

### Notes

- **Long format**: if a unit has three metrics, it has three rows (one per metric), not one wide row.
- **Pre-period rows** are only needed when you want CUPED variance reduction. If no pre-period data is available, supply post rows only.
- **Metric names must match the config exactly.** `conversion_rate` in the CSV and `conversion_rate` in `primary_metric` must be identical strings.
- **Proportion metrics** (binary 0/1 indicator per unit) are detected automatically. A unit-level aggregated rate stored as a float will use Welch t-test regardless of the `metric_type` declared in the config.
- **Guardrail metrics and secondary metrics** use the same CSV format. The analysis layer distinguishes them by which names appear in `guardrail_metrics` vs `secondary_metrics` in the config.
- **Bonferroni correction** is a config toggle (`apply_bonferroni_correction`). It does not require a different CSV shape.

### Template: [`metrics_template.csv`](metrics_template.csv)

```
experiment_id,unit_id,metric_name,metric_value,period,country,device_type
exp_YOUR_ID,u0001,conversion_rate,0.0,post,US,mobile
exp_YOUR_ID,u0001,revenue_per_user,12.50,post,US,mobile
exp_YOUR_ID,u0001,refund_rate,0.03,post,US,mobile
exp_YOUR_ID,u0001,conversion_rate,0.08,pre,US,mobile
```

---

## Internal consistency rules

Before uploading, verify:

1. `experiment_id` is identical across the config YAML, assignments CSV, and metrics CSV.
2. Every variant name in the assignments CSV appears in `config.variants`.
3. Every metric name used in the metrics CSV that you expect to be analyzed also appears in the config (`primary_metric`, `secondary_metrics`, or `guardrail_metrics`). Unrecognized names are reported as warnings but do not block analysis.
4. Variant names in `expected_allocation` must match `config.variants` exactly (used for SRM expected-count calculation).
5. For guardrail direction examples: the direction in `guardrail_directions` declares what the metric **should** do; the CSV itself is unchanged — it just carries the observed values.
