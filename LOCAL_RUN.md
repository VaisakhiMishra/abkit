# Local run guide — abkit-app

## Prerequisites

- Python 3.10 or later
- pip

## Install

```bash
# From the repo root
pip install -e abkit-core          # install abkit-core in editable mode
pip install streamlit pyyaml       # app dependencies
```

Or use the pyproject.toml inside `abkit-app`:

```bash
pip install -e abkit-core
pip install -e abkit-app           # installs streamlit and pyyaml via dependency spec
```

## Run

```bash
# From the repo root
streamlit run abkit-app/app.py
```

Streamlit opens at `http://localhost:8501` by default.

## End-to-end walkthrough

The app enforces a four-page workflow. Complete each page in order.

### 1 · Setup

- Upload or paste a config YAML from `abkit-core/tests/fixtures/clean/config.yaml`.
- Click **Validate config**.
- Observe: spec valid, 0 errors, 0 warnings, duration plan displayed.

### 2 · QA

- Upload `abkit-core/tests/fixtures/clean/assignments.csv`.
- Upload `abkit-core/tests/fixtures/clean/metrics.csv`.
- Click **Run QA**.
- Observe: SRM PASS, 0 metric issues, 0 join gaps, CUPED recommended.

### 3 · Analysis

- Click **Run analysis**.
- Observe: decision = **SHIP**, CUPED-adjusted estimate shown, no guardrail failures.

### 4 · Memo

- Read the formatted summary.
- Use **Download result payload (JSON)** or **Download memo (.txt)**.

---

## Test scenarios

### Scenario A — clean run (Scenario A fixtures)

Files: `abkit-core/tests/fixtures/clean/`

Expected results:
- Spec valid
- SRM: pass
- CUPED: recommended (coverage=1.0, r≈0.77)
- Decision: ship

### Scenario B — critical SRM (Scenario B fixtures)

Files: `abkit-core/tests/fixtures/srm/`

Expected results:
- Spec valid
- SRM: **critical** (drift=0.15, p≈0.0)
- Decision: **hold** — trust gate blocks ship

### Scenario C — weak CUPED (Scenario C fixtures)

Files: `abkit-core/tests/fixtures/weak_cuped/`

Expected results:
- Spec valid
- SRM: pass
- CUPED: **not_recommended** (coverage=0.4, r≈−0.11)
- Decision: ship (raw estimate used, CUPED not applied)

### Scenario D — guardrail ship (Scenario D fixtures)

Files: `abkit-core/tests/fixtures/guardrail_ship/`

This scenario tests the direction-aware guardrail blocking rule. The guardrail metric
(`activation_rate`) rises significantly in the treatment arm — but because the config
declares `direction: increase`, this movement is in the correct direction and must NOT
trigger a hold.

Expected results:
- Spec valid
- SRM: pass
- CUPED: **recommended** (pre-period data present, high pre-post correlation)
- Guardrail: `activation_rate` is significant with positive lift → correct direction → does NOT block
- Decision: **ship** (guardrail increase is correct direction; primary metric significant)

**Common mistake:** If the guardrail direction is omitted from the config, the legacy
rule applies — any significant movement blocks — and the decision becomes `hold` even
though the metric improved. Always declare `guardrail_directions` when the metric has
a known desired direction.

### Scenario E — Bonferroni correction ON and OFF

Files:
- `abkit-core/tests/fixtures/bonferroni_on/` (apply_bonferroni_correction: true)
- `abkit-core/tests/fixtures/bonferroni_off/` (apply_bonferroni_correction: false, same CSV data)

Both scenarios use the same CSV files (120 assignments, 60/arm; metrics for `conversion_value`,
`metric_a`, `metric_b`, `metric_c`, and `activation_rate`).

**Bonferroni ON** — Upload `bonferroni_on/config.yaml` with the shared CSV files:

Expected results:
- Spec valid
- SRM: pass (balanced 50/50)
- CUPED: **recommended** (pre-period data present for `conversion_value`)
- `secondary_alpha_used` = 0.05 / 3 ≈ 0.0167 (shown in analysis payload)
- `bonferroni_correction_applied` = true
- Decision: **ship** (primary metric strongly significant, guardrail not triggered)

**Bonferroni OFF** — Upload `bonferroni_off/config.yaml` with the same CSV files:

Expected results:
- Spec valid
- `secondary_alpha_used` = 0.05 (no adjustment)
- `bonferroni_correction_applied` = false
- Decision: **ship** (same primary metric; correction status only affects secondary thresholds)

**What to observe:** The primary metric p-value and the decision are identical between ON and OFF —
only the significance threshold applied to secondary metrics changes. With a marginal secondary
effect, you would see `is_significant=false` (ON) vs `is_significant=true` (OFF).

---

## UX rough edges (intentionally deferred)

1. **Re-running analysis after re-upload** requires clicking **Run QA** and **Run analysis** again in sequence. There is no automatic re-run on file change.

2. **File state persistence** uses `st.session_state`, which is scoped to the browser tab. Refreshing the page clears all uploaded files.

3. **Multi-variant experiments** are not supported (v1 enforces exactly two variants). Uploading a config with more variants will raise an error on the Analysis page.

4. **Duration planner formula** on the Setup page is a simplified approximation. It uses a pooled-proportion SE with a fixed 10% base rate for proportion metrics. The abkit-core analysis uses the actual observed data.

5. **YAML paste in Setup** only activates when the text area is non-empty and the button is clicked; it does not auto-parse on type.

6. **No sticky file names** — file uploader labels do not show the name of the currently loaded file when returning to a page.

7. **Guardrail directionality** is modelled via `guardrail_directions` in the config (schema v1.1+). When a direction is declared, only movement that violates that direction triggers a hold. When no direction is declared, the legacy behaviour applies: any significant movement blocks. The Memo page shows a per-metric direction badge.

8. **Bonferroni correction for secondary metrics** is an opt-in flag (`apply_bonferroni_correction: true` in the config, schema v1.2). When enabled with m ≥ 2 secondary metrics, each secondary is tested at `alpha / m` rather than `alpha`. The primary and guardrail metrics always use the declared `alpha`. See `abkit-templates/configs/example_bonferroni_on.yaml` for a copy-paste example.

## Schema artifact

`abkit-templates/configs/result_payload.schema.json` and
`abkit-templates/configs/experiment_config.schema.json` are machine-generated
from the live Pydantic models.  Do not edit them by hand.  To regenerate after
a schema change:

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

---

