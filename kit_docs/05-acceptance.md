# Acceptance criteria

## Goal

The first version is acceptable if it supports one complete and trustworthy experiment workflow from configuration through summary output.

## Functional acceptance

### 1. Experiment config

The system should:

- Accept a structured experiment config file.
- Validate required fields.
- Flag invalid allocations, duplicate variants, and obviously inconsistent settings.
- Return structured validation errors and warnings.

### 2. Assignment QA

The system should:

- Ingest a CSV assignment file.
- Validate schema and basic data integrity.
- Detect duplicate or conflicting assignments.
- Compare observed and expected allocations and produce a full `srm_check` result including chi-squared statistic, p-value, observed vs. expected allocation fractions, max absolute drift, severity classification (`pass`, `warning`, or `critical`), and a human-readable explanation.
- Apply a per-experiment `srm_alpha` override when present in the config; otherwise use the global default of 0.01.
- Classify SRM severity using both the statistical test and observed drift, not p-value alone.

### 3. Metric QA

The system should:

- Ingest a CSV metric file.
- Validate required columns and numeric metric values.
- Detect join gaps between assignment and metric data.
- Flag suspicious missingness or unsupported metric names.

### 4. Variance reduction support

The system should:

- Identify whether sufficient matched pre-period and post-period data exists for CUPED-style adjustment.
- Compute `matched_coverage`, `pre_post_correlation`, and `estimated_variance_reduction` for the primary metric.
- Classify CUPED readiness as `recommended`, `optional`, or `not_recommended` based on tiered thresholds.
- Always include the actual numeric values in the `explanation` field so analysts can apply their own judgment.
- Show both raw and adjusted estimates when adjustment is applied.
- Surface specific caveats when coverage is partial or correlation is near zero.

### 5. Duration planning

The system should:

- Compute required sample size per variant and total, given `mde`, `alpha`, `power`, and `metric_type`.
- Produce three duration estimates — `optimistic_days`, `planned_days`, and `conservative_days` — when `daily_eligible_traffic` is provided.
- Apply `traffic_cap`, `ramp_up_days`, and `planning_buffer_pct` as optional modifiers when present in the config.
- Always satisfy `optimistic_days ≤ planned_days ≤ conservative_days`.
- Echo all assumptions used in the `assumptions` field so outputs are reproducible.
- Include caveats about novelty effects and other unmodeled factors. Never model novelty effects numerically.
- Emit a `DiagnosticIssue` at `warning` severity if `daily_eligible_traffic` is missing and the planner cannot produce duration estimates.

### 6. Analysis and reporting

The system should:

- Compute lift and uncertainty for the primary metric.
- Report secondary and guardrail metrics when available.
- Emit a canonical machine-readable result payload including `duration_plan`, `srm_check`, `cuped_readiness`, and `decision`.
- Generate a readable summary with recommendation, caveats, and next actions.
- Block a `ship` recommendation when `srm_check.severity` is `critical`.
- Surface a caveat (but not block shipping) when `srm_check.severity` is `warning`.

## Non-functional acceptance

### Simplicity

- Business logic is centralized in `abkit-core`.
- The Streamlit app acts as a thin presentation layer.
- Shared schemas are reused across layers.

### Testability

- Core validation and QA functions have unit tests.
- At least three fixture datasets exist:
  - Scenario A: clean run, balanced assignment, joined metrics, CUPED-recommended case.
  - Scenario B: SRM present with `critical` severity — skewed allocation, large drift.
  - Scenario C: weak CUPED case — pre-period data exists but low correlation or low matched coverage.
- An additional fixture for SRM `warning` severity (statistically significant but small drift) is recommended but not required for v1.
- Example configs and CSVs are versioned in the repository.
- Duration planner tests verify all three duration estimates against hand-computed reference values.
- CUPED readiness tests verify all three tiers (`recommended`, `optional`, `not_recommended`) are reachable with known inputs.
- SRM severity tests cover all four classification conditions (pass, warning via drift, warning via p-value, critical).

### Clarity

- Validation and QA outputs use stable issue codes and readable explanations.
- The memo output makes assumptions and caveats explicit.
- The README and docs are sufficient for a new contributor to run the project locally.

## Demo scenarios

A good v1 demo should cover at least these scenarios:

### Scenario A: Clean experiment

- Balanced assignment.
- Valid config.
- Joined metric data.
- Optional CUPED recommendation with acceptable inputs.
- Clear summary output.

### Scenario B: Trust issue detected

- Skewed assignment allocation producing both a significant chi-squared result and `max_absolute_drift` ≥ 0.02.
- `srm_check.severity` is `critical`.
- The `decision.recommendation` is `hold`, not `ship`.
- The memo clearly states the result cannot be trusted without remediation and names the issue.

### Scenario C: Weak CUPED case

- Pre-period data exists but `pre_post_correlation` < 0.1 or `matched_coverage` < 0.5.
- `cuped_readiness.recommendation` is `not_recommended`.
- The explanation states the actual correlation and coverage values.
- The system still runs and returns raw estimates; it does not fail.

### Scenario D: Duration planning with modifiers

- Config includes `daily_eligible_traffic`, `traffic_cap`, `ramp_up_days`, and `planning_buffer_pct`.
- All three duration estimates are present and ordered correctly.
- The `assumptions` object echoes the inputs used.
- The `caveats` array includes the ramp-up margin note.

## Exit criteria for v1

Version 1 is done when:

- A new user can run one end-to-end analysis locally.
- The app and core package use the same schemas.
- At least three core docs are accurate and complete.
- One skill can consume the result payload without manual rewriting.
- The project is small enough that a reviewer can understand the full workflow in a single sitting.
