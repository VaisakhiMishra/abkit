# abkit Skills — Agent Setup Guide

> **Schema version:** 1.2
> **Applies to:** Claude (Anthropic), Codex (OpenAI), IBM Bob (watsonx Orchestrate)

---

## What this directory contains

```
abkit-skills/
  shared/
    consuming-results.md   ← How to read ResultPayload JSON and memo text
    agent-setup.md         ← This file
  spec-review/
    README.md              ← Experiment spec review skill + prompt template
  prelaunch-qa/
    README.md              ← Pre-launch QA review skill + prompt template
  results-memo/
    README.md              ← Analysis review + decision memo drafting prompts
```

Each `README.md` is a self-contained skill document. It specifies required inputs, expected outputs, refusal conditions, and a fill-in prompt template.

Before using any skill, read [`shared/consuming-results.md`](consuming-results.md). It explains the `ResultPayload` JSON structure that all skills consume.

---

## Two supported input modes

Every skill in this directory supports two equivalent workflows. Use whichever is available.

### Mode A — Config file provided

Paste or attach the full `ExperimentConfig` YAML (or JSON) and the skill will:

1. Parse the config and validate it against the live schema.
2. Use the config as the sole source of truth.
3. Not ask for values that are already present in the file.
4. Prompt only if a required field is missing or a value fails a validation rule.

### Mode B — Interactive (no config file)

Provide values conversationally. The skill will:

1. Identify which required fields are missing.
2. Explain what each missing field controls and why the skill needs it.
3. Ask for the exact missing values before proceeding.
4. Resume only after all required values are supplied.
5. Not invent defaults for required fields.

**Important:** When required fields are missing, the skill stops completely and returns only the list of missing fields with justifications. It does not produce a partial answer.

---

## Required fields — always needed

The following fields are mandatory for every skill that reads an `ExperimentConfig`. If any are absent, the skill must pause and request them:

| Field | Why it is needed |
|---|---|
| `schema_version` | Selects the correct validation rules; must be `"1.2"` |
| `experiment_name` | Identifies the experiment in all output sections |
| `experiment_id` | Stable slug used to correlate runs and CSV data |
| `owner` | Documents accountability for the experiment |
| `hypothesis` | Required to assess design coherence and metric alignment |
| `primary_metric` | The success metric; all significance statements reference it |
| `variants` | Defines the two arms (control first, treatment second) |
| `expected_allocation` | Traffic split; keys must match `variants`; values must sum to 1.0 |
| `unit_of_randomization` | The unit assigned to variants (e.g. `user_id`) |
| `alpha` | Significance threshold for all primary and guardrail metric tests |
| `power` | Statistical power used in sample-size and duration planning |
| `mde` | Minimum detectable effect for the power calculation |

`metric_type` (`"proportion"` or `"continuous"`) is required when an analysis run is requested or when reviewing a config for analysis readiness. It may be absent for spec-validation-only reviews.

---

## Missing-value prompt format

When one or more required values are absent, the skill must respond using this exact structure before doing anything else:

```
The following required values are missing. Please provide them before I proceed.

---
Missing field: <field_name>
What it controls: <one sentence>
Why this skill needs it: <one sentence>
Please provide: <exact format or allowed values>

---
Missing field: <field_name>
...
```

Group fields by purpose when multiple are missing (e.g., all statistical parameters together, all identity fields together).

---

## Optional fields — behavior when absent

These fields have documented defaults. If absent, the skill proceeds and documents the assumption:

| Field | Default when absent |
|---|---|
| `metric_type` | `null` — skill notes analysis cannot be run until set |
| `guardrail_metrics` | `[]` — no harm-detection; skill flags this as a design risk |
| `guardrail_directions` | `{}` — legacy blocking (any significant movement holds) |
| `secondary_metrics` | `[]` — no secondary metrics analyzed |
| `apply_bonferroni_correction` | `false` — secondary metrics tested at base `alpha` |
| `daily_eligible_traffic` | `null` — duration estimate cannot be produced |
| `pre_period_window_days` | `null` — CUPED not available |
| `analysis_window_days` | `null` — no declared run duration |
| `srm_alpha` | `0.01` global default |
| `traffic_cap` | `1.0` (all eligible traffic routed) |
| `eligibility_rate` | `null` — not applied in duration planning |
| `ramp_up_days` | `0` |
| `planning_buffer_pct` | `0.0` |

---

## Guardrail and Bonferroni — interactive prompting rules

When a user fills values interactively, only ask these follow-up questions when relevant:

**Guardrail direction** — ask only when `guardrail_metrics` is non-empty:
> "You declared guardrail metric(s): `[names]`. For each, should it go up (`increase`), go down (`decrease`), or stay flat (`flat`) for the experiment to pass? If you have no preference, omit this and any significant movement will trigger a hold."

**Bonferroni correction** — ask only when `secondary_metrics` has two or more entries:
> "You declared `m` secondary metrics. Should Bonferroni correction be applied? If yes, each secondary metric will be tested at `alpha / m` instead of `alpha`. This reduces false positives but also reduces power for secondary metrics. Default is `false`."

Do not ask about guardrail directions when there are no guardrails. Do not ask about Bonferroni when there are fewer than two secondary metrics.

---

## How to use these skills in each environment

### Claude (Anthropic — claude.ai or API)

1. Open a new conversation.
2. Paste the skill's **prompt template** into your first message.
3. **If you have a config file:** replace the `{{EXPERIMENT CONFIG}}` placeholder with the file contents.
4. **If you do not have a config file:** state the values you know. The skill will ask for any missing required values before proceeding.
5. Remove any `{% if ... %}` conditional blocks that do not apply to your run.
6. Send.

**Tip for long payloads:** If the `ResultPayload` JSON is large, paste it as a code block (triple backtick, json) inside the prompt so Claude treats it as structured data rather than prose.

**System prompt option:** For repeated use, copy the skill's prompt template into Claude's system prompt. Subsequent user messages can then supply just the payload or config without repeating the instructions.

Example for spec review:

```
[System]
You are an experiment design reviewer for an A/B testing team.
You will be given an experiment config object that conforms to the abkit ExperimentConfig schema (version 1.2).
If the config is complete, review it and produce a structured spec review.
If required fields are missing, list them with justifications and ask for the values before proceeding.
[The rest of the prompt template from spec-review/README.md]

[User]
EXPERIMENT CONFIG:
```yaml
schema_version: "1.2"
experiment_name: "Checkout button redesign"
...
```
```

---

### Codex (OpenAI — API or ChatGPT with code interpreter)

1. Copy the prompt template from the relevant `README.md`.
2. **If you have a config:** replace `{{...}}` placeholders with actual content.
3. **If you do not:** send the prompt without the config section. The model will ask for missing values.
4. For JSON inputs, pass the payload as a Python dict or read it from a file using the code interpreter.
5. Send as a single chat completion request (role: user) or as part of a function-calling workflow.

**Structured output option:** When using the API directly, request a JSON response format and set the output schema to match the expected sections (e.g. `missing_fields`, `design_risks`, `clarifications`, `readiness_summary` for spec review).

Example Python call pattern:

```python
import json
import openai

with open("result_payload.json") as f:
    payload = json.load(f)

prompt = f"""
You are an experiment analyst writing a structured analysis review.
...
RESULT PAYLOAD:
{json.dumps(payload, indent=2)}
...
"""

response = openai.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": prompt}],
)
print(response.choices[0].message.content)
```

**File-based workflow:** Save the result payload as `result_payload.json` and the prompt template as `prompt.txt`. Inject the payload into the prompt at the `{{...}}` marker using any text templating library.

---

### IBM Bob (watsonx Orchestrate ADK)

IBM Bob skills are loaded as SKILL.md files. Each `README.md` in `abkit-skills/` is structured to be compatible with Bob's skill format.

**Step 1 — Fetch or copy the skill**

If you are inside the Bob IDE, use `fetch_skill` from the watsonx Orchestrate ADK MCP server to load the skill file into the `.bob/skills/` directory. Otherwise, copy the relevant `README.md` manually.

**Step 2 — Register the skill**

Bob skill files are placed in `.bob/skills/<skill-name>/`. The skill is activated by the Bob agent when the user's request matches the skill's purpose statement.

Example directory:

```
.bob/skills/
  abkit-spec-review/
    SKILL.md          ← contents of abkit-skills/spec-review/README.md
  abkit-prelaunch-qa/
    SKILL.md          ← contents of abkit-skills/prelaunch-qa/README.md
  abkit-results-memo/
    SKILL.md          ← contents of abkit-skills/results-memo/README.md
```

**Step 3 — Invoke**

Ask Bob to perform the task in natural language. Bob will activate the matching skill and apply the prompt template. You supply the payload or config inline or as an attached file.

Example invocations — with config file:

```
Review this experiment config for readiness:
[paste YAML config]
```

Example invocations — interactive (no config file):

```
I want to review my experiment spec. I don't have a config file yet.
The experiment is a checkout button redesign. My primary metric is conversion_rate.
```

Bob will then ask for each missing required field before producing the spec review.

```
Interpret the QA results from this payload and tell me what needs attention before I trust the analysis:
[paste quality_checks JSON]
```

```
Draft an analyst-level decision memo from this result payload:
[paste full ResultPayload JSON]
```

**Passing JSON as context:** When attaching a `ResultPayload` JSON file, Bob reads it as structured context. Reference it in your message with "from the attached payload" or paste it inline.

---

## Which skill to use when

| Task | Skill | Key input |
|---|---|---|
| Before launch — is the experiment spec complete and coherent? | `spec-review` | `ExperimentConfig` YAML or JSON, or interactive values |
| Before analyzing — can I trust the assignment and metric data? | `prelaunch-qa` | `quality_checks` from `ResultPayload` |
| After analysis — what do the results mean? | `results-memo` (Prompt 1) | Full `ResultPayload` |
| After analysis — write a decision memo | `results-memo` (Prompt 2) | Full `ResultPayload` + Prompt 1 output |

Run these in order for a full end-to-end workflow: spec review → QA review → analysis review → memo draft.

---

## Rules all skills share

1. **Do not re-run statistical tests.** All test outputs are already in the `ResultPayload`. Read them; do not recompute them.
2. **Use exact schema field names.** The field names in each prompt template match the Pydantic models in `abkit-core/src/abkit_core/schemas.py`. Do not paraphrase or rename them.
3. **Use `effective_primary_estimate` for headline results**, not `primary_metric_result`. The former is the CUPED-adjusted value when applicable.
4. **Treat a `null` sub-object as absent, not as a pass.** A `null` `quality_checks` means data was not uploaded.
5. **Do not override the `decision.recommendation` enum.** The memo explains the recommendation; it does not change it.
6. **Check `srm_check.severity` before reporting results.** A `"critical"` SRM finding must appear as a trust warning before any headline metric statement.
7. **Do not invent defaults for required fields.** If a required field is missing, pause and ask for it.
8. **Do not produce partial answers when required fields are missing.** Return only the missing-field list and request the values before doing any analysis.

---

## Versioning

These skill documents are pinned to **schema version 1.2**.

Schema version history:
- **1.0** — Initial release.
- **1.1** — Added `guardrail_directions` to `ExperimentConfig` and direction-aware guardrail blocking to `analysis.py`. `ResultPayload.schema_version` emitted `"1.1"`.
- **1.2** — Added `apply_bonferroni_correction` to `ExperimentConfig`. Added `secondary_alpha_used` and `bonferroni_correction_applied` to `AnalysisResult`. `ResultPayload.schema_version` now emits `"1.2"`.

Backward compatibility:
- Payload with `schema_version: "1.0"` — `guardrail_directions` absent; treat as `{}` (legacy blocking applies to all guardrails).
- Payload with `schema_version: "1.1"` — `apply_bonferroni_correction` absent from normalized config; treat as `false`. `secondary_alpha_used` and `bonferroni_correction_applied` absent from `analysis`; treat correction as off.

When `abkit-core/src/abkit_core/schemas.py` is updated, the corresponding skill READMEs must be reviewed for any prompt text that references affected field names.
