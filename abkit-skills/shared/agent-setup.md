# abkit Skills — Agent Setup Guide

> **Schema version:** 1.0  
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

## How to use these skills in each environment

### Claude (Anthropic — claude.ai or API)

1. Open a new conversation.
2. Paste the skill's **prompt template** into your first message.
3. Replace all `{{...}}` placeholders with the actual values (JSON objects, config YAML, etc.).
4. Remove any `{% if ... %}` conditional blocks that do not apply to your run.
5. Send.

**Tip for long payloads:** If the `ResultPayload` JSON is large, paste it as a code block (triple backtick, json) inside the prompt so Claude treats it as structured data rather than prose.

**System prompt option:** For repeated use, copy the skill's prompt template into Claude's system prompt. Subsequent user messages can then supply just the payload without repeating the instructions.

Example for spec review:

```
[System]
You are an experiment design reviewer for an A/B testing team.
You will be given an experiment config object that conforms to the abkit ExperimentConfig schema (version 1.0).
Your task is to review the config and produce a structured spec review.
[The rest of the prompt template from spec-review/README.md]

[User]
EXPERIMENT CONFIG:
```yaml
schema_version: "1.0"
experiment_name: "Checkout button redesign"
...
```
```

---

### Codex (OpenAI — API or ChatGPT with code interpreter)

1. Copy the prompt template from the relevant `README.md`.
2. Replace `{{...}}` placeholders with actual content.
3. For JSON inputs, pass the payload as a Python dict or read it from a file using the code interpreter.
4. Send as a single chat completion request (role: user) or as part of a function-calling workflow.

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

Example invocations:

```
Review this experiment config for readiness:
[paste YAML config]
```

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
| Before launch — is the experiment spec complete and coherent? | `spec-review` | `ExperimentConfig` YAML or JSON |
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

---

## Versioning

These skill documents are pinned to **schema version 1.0**.

If `schema_version` in a payload reads something other than `"1.0"`, do not apply these skill templates without checking whether the field names or structure have changed.

When `abkit-core/src/abkit_core/schemas.py` is updated, the corresponding skill READMEs must be reviewed for any prompt text that references affected field names.
