# abkit config templates — v1.2

This directory contains the versioned JSON schemas and copy-paste-ready YAML
config templates for abkit experiments.

---

## Files

| File | Purpose |
|---|---|
| [`experiment_config.schema.json`](experiment_config.schema.json) | Machine-generated JSON Schema for `ExperimentConfig` |
| [`result_payload.schema.json`](result_payload.schema.json) | Machine-generated JSON Schema for `ResultPayload` |
| [`experiment_config.template.yaml`](experiment_config.template.yaml) | Full annotated config template — start here |
| [`example_guardrail_directions.yaml`](example_guardrail_directions.yaml) | Example: direction-aware guardrail blocking |
| [`example_bonferroni_off.yaml`](example_bonferroni_off.yaml) | Example: multiple secondaries, Bonferroni correction OFF (default) |
| [`example_bonferroni_on.yaml`](example_bonferroni_on.yaml) | Example: multiple secondaries, Bonferroni correction ON |

---

## `secondary_metrics` and `guardrail_metrics` — string vs object form

Both fields accept **either** a plain string name or a full `MetricSpec` object.
Plain strings are automatically coerced to `MetricSpec(name=..., metric_type="continuous")`.

```yaml
# Plain string — the preferred shorthand for continuous metrics
secondary_metrics:
  - "revenue_per_user"

# Full object — use when the metric is binary (proportion)
secondary_metrics:
  - name: "signup_rate"
    metric_type: "proportion"

# Mixed — both forms can appear in the same list
secondary_metrics:
  - "revenue_per_user"              # → continuous
  - name: "signup_rate"
    metric_type: "proportion"       # → proportion
```

The same rule applies to `guardrail_metrics`.

---

## `guardrail_directions` — direction-aware blocking (v1.1+)

```yaml
guardrail_metrics:
  - "refund_rate"
  - "revenue"

guardrail_directions:
  refund_rate: "decrease"   # HOLD only when refunds go UP significantly
  revenue: "increase"       # HOLD only when revenue goes DOWN significantly
  # If a metric is not listed, any significant movement triggers a HOLD
```

Allowed values: `"increase"`, `"decrease"`, `"flat"`.

When a guardrail metric is not listed in `guardrail_directions`, the legacy
behaviour applies: any statistically significant movement in either direction
triggers a hold.

---

## `apply_bonferroni_correction` — optional secondary-metric correction (v1.2)

```yaml
apply_bonferroni_correction: false   # default — each secondary tested at alpha
apply_bonferroni_correction: true    # each secondary tested at alpha / m
```

Rules:
- Only applies to secondary metrics. Primary and guardrail metrics always use `alpha`.
- Has no effect when `secondary_metrics` has 0 or 1 entries (`alpha / 1 == alpha`).
- With m ≥ 2 secondaries and correction on: `secondary_alpha = alpha / m`.
- Inspect `analysis.secondary_alpha_used` and `analysis.bonferroni_correction_applied`
  in the result payload to confirm what threshold was actually applied.

---

## Required fields (must be present in every config)

```
schema_version, experiment_name, experiment_id, owner, hypothesis,
primary_metric, variants (≥ 2), expected_allocation, unit_of_randomization,
alpha, power, mde
```

`metric_type` is also required whenever analysis is run (not just spec validation).

---

## Regenerating the JSON schema artifacts

The two `.schema.json` files are machine-generated from the live Pydantic
models in `abkit-core`.  Do not edit them by hand.

```bash
cd abkit-core
python3 -c "
import json, sys
sys.path.insert(0, 'src')
from abkit_core.schemas import ExperimentConfig, ResultPayload

ec = ExperimentConfig.model_json_schema()
ec['description'] = 'Schema version 1.2 — canonical experiment configuration for abkit. Generated from abkit_core.schemas.ExperimentConfig.'
with open('../abkit-templates/configs/experiment_config.schema.json', 'w') as f:
    json.dump(ec, f, indent=2); f.write('\n')

rp = ResultPayload.model_json_schema()
rp['description'] = 'Schema version 1.2 — canonical result payload for abkit. Generated from abkit_core.schemas.ResultPayload.'
with open('../abkit-templates/configs/result_payload.schema.json', 'w') as f:
    json.dump(rp, f, indent=2); f.write('\n')

print('Both schema artifacts regenerated.')
"
```
