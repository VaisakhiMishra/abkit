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

---

## UX rough edges (intentionally deferred)

1. **Re-running analysis after re-upload** requires clicking **Run QA** and **Run analysis** again in sequence. There is no automatic re-run on file change.

2. **File state persistence** uses `st.session_state`, which is scoped to the browser tab. Refreshing the page clears all uploaded files.

3. **Multi-variant experiments** are not supported (v1 enforces exactly two variants). Uploading a config with more variants will raise an error on the Analysis page.

4. **Duration planner formula** on the Setup page is a simplified approximation. It uses a pooled-proportion SE with a fixed 10% base rate for proportion metrics. The abkit-core analysis uses the actual observed data.

5. **YAML paste in Setup** only activates when the text area is non-empty and the button is clicked; it does not auto-parse on type.

6. **No sticky file names** — file uploader labels do not show the name of the currently loaded file when returning to a page.

7. **Guardrail directionality** is not modelled — any significant guardrail triggers a hold recommendation. The memo notes this explicitly. Analysts should inspect the direction manually.

## Schema artifact

`abkit-templates/configs/result_payload.schema.json` is machine-generated from the
live Pydantic models.  Do not edit it by hand.  To regenerate after a schema change:

```bash
cd abkit-core
python3 -c "
from src.abkit_core.schemas import ResultPayload
import json
with open('../abkit-templates/configs/result_payload.schema.json', 'w') as f:
    json.dump(ResultPayload.model_json_schema(), f, indent=2)
    f.write('\n')
"
```

---

