# Skill roadmap

## Purpose

This document defines the agent skills that should be implemented after the core package and Streamlit workbench are stable.

The guiding principle is that skills should automate repetitive, structured work around A/B testing rather than replace statistical judgment.

## Skill design rules

Each skill should have:

- A clearly scoped purpose.
- Required and optional inputs.
- An expected output schema.
- Failure modes and refusal conditions.
- At least one realistic example.
- Terminology aligned with the shared project schemas.

Skills should prefer structured inputs over long natural-language descriptions whenever possible.

## Planned skills

### 1. Experiment spec reviewer

**Purpose**
Review a proposed experiment and identify missing fields, weak assumptions, and ambiguous metric definitions before implementation.

**Inputs**
- Experiment config.
- Optional product context or hypothesis notes.

**Outputs**
- Missing information.
- Design risks.
- Suggested clarifications.
- A readiness summary.

**Why it matters**
Many A/B testing failures start as unclear specs rather than bad statistics.

### 2. Pre-launch QA reviewer

**Purpose**
Interpret assignment and metric QA outputs and explain what needs attention before trusting results.

**Inputs**
- Result payload or QA payload.
- Assignment diagnostics.
- Metric diagnostics.

**Outputs**
- Plain-language explanation of SRM, missing joins, malformed assignments, and suspicious guardrail behavior.
- Ranked remediation suggestions.

### 3. SQL scaffold generator

**Purpose**
Generate starter SQL for exposure tables, metric aggregation, and pre-period metric extraction.

**Inputs**
- Experiment config.
- Metric definitions.
- Warehouse or SQL dialect notes.

**Outputs**
- SQL skeletons.
- Assumption notes.
- Data quality reminders.

**Constraints**
The skill should produce scaffolding, not assert that the SQL is production-ready without review.

### 4. CUPED readiness interpreter

**Purpose**
Explain whether pre-period data supports CUPED and what trade-offs or assumptions apply.

**Inputs**
- CUPED readiness output.
- Metric metadata.

**Outputs**
- Recommendation on whether to use CUPED.
- Caveats about matched units, pre/post correlation, and interpretability.

### 5. Results memo writer

**Purpose**
Convert structured analysis outputs into a concise readout for analysts, product managers, or executives.

**Inputs**
- Result payload.
- Optional target audience.

**Outputs**
- Executive summary.
- Analyst summary.
- Recommendation and caveats.
- Suggested next actions.

### 6. Experiment retro generator

**Purpose**
Create a reusable retrospective from experiment outcomes, issues, and follow-up ideas.

**Inputs**
- Result payload.
- Human notes.

**Outputs**
- What was learned.
- What went wrong.
- What should change next time.
- Follow-up hypotheses.

## Deferred skills

These are good future candidates but should not be built before the core workflow is working:

- Sequential monitoring coach.
- Segment interpretation assistant.
- Guardrail triage assistant.
- Experiment portfolio summarizer.
- Metric naming and taxonomy normalizer.

## Skill packaging recommendation

Each skill should eventually contain:

```text
skill-name/
  README.md
  input.schema.json
  output.schema.json
  examples/
  prompts/
```

This makes the skill portable across Claude, Codex, IBM Bob, or future orchestration layers.
