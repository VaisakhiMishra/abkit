# Architecture

## Overview

The system is organized into four layers so that statistical logic, user experience, and agent workflows remain decoupled.

```text
User / Agent
   │
   ├── Streamlit UI (`abkit-app`)
   ├── Agent skills (`abkit-skills`)
   └── Templates (`abkit-templates`)
             │
             ▼
        Shared schemas
             │
             ▼
        Python core (`abkit-core`)
```

This structure keeps the statistical and validation logic in one place while allowing different front ends to reuse the same inputs and outputs.

## Layer 1: `abkit-core`

`abkit-core` is the only place where business logic and statistical utilities should live.

Responsibilities:

- Validate experiment specs.
- Estimate sample size and rough duration.
- Detect SRM and other assignment-quality issues.
- Check randomization balance and simple data integrity issues.
- Assess CUPED readiness and perform optional adjustment.
- Compute experiment lift, uncertainty, and decision summaries.
- Produce a canonical result payload for all downstream consumers.

Suggested module layout:

```text
abkit-core/
  src/abkit_core/
    schemas.py
    design.py
    quality.py
    variance.py
    analysis.py
    reporting.py
```

Rules:

- No Streamlit code in this package.
- No prompt text or agent-specific logic in this package.
- Functions should accept validated structures and return structured outputs.
- Core logic should be testable using small fixture datasets.

## Layer 2: `abkit-app`

`abkit-app` is a Streamlit workbench for interactive analyst workflows.

Responsibilities:

- Collect user inputs through forms and file uploads.
- Display validation issues, diagnostics, and analysis summaries.
- Call `abkit-core` for all substantive logic.
- Export markdown, JSON, or YAML artifacts.

Suggested pages:

- `Setup` — experiment metadata and design assumptions.
- `Plan` — sample size and duration estimator.
- `QA` — assignment and metric diagnostics, including SRM.
- `Analysis` — raw vs. CUPED-adjusted results when available.
- `Memo` — human-readable summary and export.

Rules:

- UI should be thin.
- The app should not duplicate statistical formulas or validation logic.
- Outputs should always be traceable back to `abkit-core` results.

## Layer 3: `abkit-skills`

`abkit-skills` contains reusable instructions, prompt scaffolds, and agent-compatible definitions for Claude, Codex, IBM Bob, or other assistants.

Responsibilities:

- Convert structured experiment inputs into guided AI workflows.
- Reuse the same schema names and output contracts as the app and core package.
- Support later tasks such as spec review, SQL scaffolding, QA interpretation, memo generation, and retro creation.

Suggested contents:

```text
abkit-skills/
  spec-review/
  prelaunch-qa/
  results-memo/
  retro/
  shared/
```

Rules:

- Skills should consume structured inputs whenever possible.
- Skill outputs should be inspectable and easy to test with example payloads.
- Prompt text should not redefine business rules inconsistently with `abkit-core`.

## Layer 4: `abkit-templates`

`abkit-templates` contains reusable static assets and text templates.

Examples:

- Experiment specification template.
- Metric definition template.
- Launch checklist.
- Analysis plan.
- Results memo template.
- Experiment retro template.
- Example YAML and JSON configs.

Responsibilities:

- Standardize documentation.
- Provide copy-paste scaffolds for people and agents.
- Make the system usable even before the full UI exists.

## Shared schemas

Shared schemas are the backbone of the project.

At minimum, the following structures should be canonical and versioned:

- Experiment config.
- Assignment row schema.
- Metric row schema.
- Analysis result payload.
- Diagnostic issue object.
- Decision memo object.

All layers should either directly use these schemas or generate adapters from them. Schema drift should be treated as a bug.

## Data flow

### Main flow

1. A user or agent provides an experiment config.
2. `abkit-core` validates the config.
3. A user uploads assignment and metric data.
4. `abkit-core` runs QA diagnostics, including SRM where applicable.
5. `abkit-core` runs analysis and optional CUPED adjustment.
6. `abkit-core` emits a result payload.
7. The Streamlit app, templates, and skills render that payload in different forms.

## Repository structure

```text
.
├── README.md
├── docs/
├── abkit-core/
│   ├── pyproject.toml
│   ├── src/abkit_core/
│   └── tests/
├── abkit-app/
│   ├── app.py
│   ├── pages/
│   └── components/
├── abkit-skills/
│   ├── shared/
│   ├── spec-review/
│   ├── prelaunch-qa/
│   ├── results-memo/
│   └── retro/
└── abkit-templates/
    ├── configs/
    ├── memos/
    ├── plans/
    └── specs/
```

## Design decisions

- Centralize business logic in Python.
- Use Streamlit only as a delivery surface.
- Prefer file-based templates and schemas over ad hoc prompt text.
- Keep the first version local-first and single-user.
- Optimize for determinism, clarity, and extensibility rather than platform completeness.
