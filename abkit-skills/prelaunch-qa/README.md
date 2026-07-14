# Skill: Pre-launch QA Review

> **Schema version:** 1.0  
> **Layer:** `abkit-skills/prelaunch-qa`  
> **Source of truth for field names:** `abkit-core/src/abkit_core/schemas.py`

---

## Purpose

Interpret assignment and metric QA outputs produced by abkit-core. Explain what each finding means, which issues require action before trusting results, and what the remediation options are.

This skill reads the `quality_checks` sub-object from a `ResultPayload`. It does not re-run statistical tests and does not modify the data.

---

## Inputs

| Input | Required | Type | Notes |
|---|---|---|---|
| `quality_checks` from `ResultPayload` | Yes | JSON sub-object | The full `QualityChecks` object |
| `spec_validation.normalized_config` | Recommended | JSON sub-object | Needed to interpret variant names and expected allocations |
| Full `ResultPayload` | Optional | JSON | Provide the full payload when available; the skill will read the correct sub-objects |

---

## Outputs

The skill produces a structured QA review with five sections:

1. **SRM finding** — Plain-English interpretation of the sample ratio mismatch check.
2. **Balance and missingness** — Summary of balance checks, missingness checks, and join integrity issues.
3. **Guardrail status** — Summary of guardrail metric diagnostic issues.
4. **Blocking issues** — Issues that must be resolved before results can be trusted.
5. **Remediation suggestions** — Ranked list of concrete next steps.

---

## Refusal conditions

- If `quality_checks` is `null`, state that no data was uploaded and stop. Do not report a passing QA.
- If `srm_check` is `null` within `quality_checks`, state that no assignment data was available for SRM testing.
- Do not classify SRM severity yourself. Use the `srm_check.severity` value as written.
- Do not contradict the `explanation` string already written by abkit-core. Quote it or paraphrase it; do not replace it with a different interpretation.

---

## Prompt template

Use this template verbatim. Replace the `{{...}}` placeholders with the actual values before sending.

```
You are a data quality reviewer for an A/B testing team.

You will be given the quality_checks output from an abkit ResultPayload (schema version 1.0).
Your task is to interpret the QA findings and produce a structured review that explains what the issues mean
and what the team should do before relying on the results.

---
QUALITY CHECKS:
{{paste the quality_checks JSON object here}}
---
{% if normalized_config is provided %}
EXPERIMENT CONFIG (normalized):
{{paste the normalized_config object here}}
{% endif %}
---

Produce the following five sections.

SECTION 1 — SRM FINDING
Report the value of srm_check.severity: it will be "pass", "warning", or "critical".
If severity is "pass": state that the assignment ratio check passed and the observed allocation matches the expected allocation within acceptable tolerance.
If severity is "warning": quote srm_check.explanation verbatim and note that results can be reported with the caveat listed.
If severity is "critical": quote srm_check.explanation verbatim. State clearly that a "ship" recommendation is not appropriate until the SRM cause is identified and resolved. Do not suggest workarounds that contradict this rule.
Include: srm_check.p_value, srm_check.srm_alpha_used, srm_check.max_absolute_drift, and the variant-level observed vs. expected counts from srm_check.observed_allocation and srm_check.expected_allocation.
If srm_check is null: state that no assignment data was provided so SRM could not be tested.

SECTION 2 — BALANCE AND MISSINGNESS
Summarize the issues in:
- balance_checks: pre-experiment covariate balance failures
- missingness_checks: missing metric value warnings
- join_integrity_checks: units present in assignments but absent from metrics, or vice versa

For each non-empty list, group issues by severity and quote the message field for each DiagnosticIssue.
If all three lists are empty, write: "No balance, missingness, or join integrity issues were found."

SECTION 3 — GUARDRAIL STATUS
Summarize the issues in guardrail_status.
For each DiagnosticIssue, state the severity, the message, and the field (if present).
If guardrail_status is empty, write: "No guardrail issues were reported."
Do not interpret guardrail metric values beyond what the issue messages state.

SECTION 4 — BLOCKING ISSUES
List every DiagnosticIssue with severity "error" from all five check lists combined.
For each blocking issue, state: the source (srm_check, balance_checks, etc.), the code, and the message.
If srm_check.severity is "critical", list it here as a blocking issue even though it is not a DiagnosticIssue object.
If there are no blocking issues, write: "No blocking issues found. Results can be reviewed subject to any warnings noted above."

SECTION 5 — REMEDIATION SUGGESTIONS
Provide a ranked list of at most five concrete next steps.
Order from highest to lowest urgency based on the severity and source of the issues found.
Each suggestion must reference a specific field or issue found in the data — do not give generic advice.
Examples of concrete suggestions:
- "Investigate SRM: variant 'treatment' has observed allocation 0.42 vs expected 0.50 (drift = 0.08). Check the assignment pipeline for the period [assignment_ts range if available]."
- "Re-check join keys: join_integrity_checks reports N units in assignments with no matching metric rows. Verify that unit_id is consistent between the two files."
If there are no blocking issues and no warnings, write: "No remediation required. Proceed to analysis."
```

---

## Example output structure

```
SECTION 1 — SRM FINDING
Severity: critical

"Assignment ratio is significantly imbalanced. Observed allocation: control=0.44, treatment=0.56.
Expected: control=0.50, treatment=0.50. Max absolute drift: 0.06. p=0.0003 < srm_alpha=0.01.
Both the statistical test and the drift threshold indicate a trust problem. The cause must be
identified before results are considered reliable."

A "ship" recommendation is not appropriate until the root cause of the assignment imbalance is found
and resolved.

SECTION 2 — BALANCE AND MISSINGNESS
missingness_checks (1 warning):
- "Metric 'revenue_per_user' has 12% missing values in the treatment arm. Check for differential data collection failures."

No balance or join integrity issues were found.

SECTION 3 — GUARDRAIL STATUS
No guardrail issues were reported.

SECTION 4 — BLOCKING ISSUES
1. srm_check: severity=critical — Assignment ratio imbalance detected. See Section 1.

SECTION 5 — REMEDIATION SUGGESTIONS
1. Investigate the SRM root cause. Examine assignment logs for control vs. treatment for the experiment period. Common causes: sticky bucketing failure, holdout leakage, bot traffic asymmetry.
2. After resolving the SRM cause, re-run the analysis pipeline and re-check srm_check.severity before reviewing results.
3. Investigate the 12% missingness for 'revenue_per_user' in the treatment arm to rule out differential data collection errors.
```

---

## Notes for skill maintainers

- The SRM severity classification rules are defined in `kit_docs/03-schemas.md`. Do not restate them in the prompt; the `severity` value in the payload is already the output of those rules.
- `guardrail_status` contains `DiagnosticIssue` objects, not `MetricEstimate` objects. Do not attempt to read `p_value` or `absolute_lift` from it.
- The `effective_primary_estimate` field is on `AnalysisResult`, not `QualityChecks`. It is out of scope for this skill.
