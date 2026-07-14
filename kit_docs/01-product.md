# Product definition

## What this kit is

This kit is a focused productivity layer for data scientists and experiment owners who already run or analyze A/B tests but lose time on repetitive setup, validation, QA, and reporting work.

It is meant to help with:

- Defining a clean experiment spec before launch.
- Estimating whether an experiment is plausibly feasible.
- Detecting common trust issues such as sample ratio mismatch and randomization problems.
- Assessing whether pre-period data can support variance reduction methods such as CUPED.
- Turning raw statistical output into consistent summaries and decision memos.
- Creating reusable agent workflows and templates around those steps.

The product should feel like a small toolkit that improves experiment hygiene and analyst throughput rather than a heavyweight enterprise platform.

## What this kit is not

This kit is not:

- A feature flagging platform.
- A traffic allocation or experiment delivery system.
- A warehouse-native experimentation platform.
- A full causal inference workbench for every experimental design.
- A replacement for domain judgment, experiment review, or production monitoring.
- A one-click “declare the winner” black box.

It should not pretend to own the whole experimentation lifecycle. It only helps people do specific high-value tasks more reliably and more quickly.

## Primary users

The main users are:

- Data scientists running product or growth experiments.
- Product analysts who need a guided workflow for experiment QA and readouts.
- Experiment owners who need standardized specs and result summaries.
- AI coding agents that need structured inputs, templates, and outputs for repetitive experimentation tasks.

## Core v1 use cases

### 1. Pre-launch design review

A user fills in an experiment configuration and gets immediate feedback on missing fields, weak assumptions, guardrail gaps, and feasibility concerns.

### 2. Assignment and metric QA

A user uploads experiment assignment and metric files and receives checks for SRM, malformed assignments, missing joins, and simple balance issues.

### 3. Variance reduction readiness

A user uploads pre-period and experiment-period metrics and gets a recommendation on whether CUPED is likely to improve sensitivity enough to matter.

### 4. Result memo generation

A user gets a structured analysis summary that can be turned into an analyst note, executive summary, or agent-consumable payload.

## Product constraints

- Keep the first version small enough to implement in two weeks.
- Prefer clarity and trust over feature count.
- Avoid hidden statistical magic.
- Make assumptions explicit in outputs.
- Ensure the same schema can support Python code, Streamlit UI, templates, and future skills.

## v1 non-goals

These are deliberately out of scope for the first version:

- Sequential testing engines or advanced Bayesian experimentation engines.
- Automated decisioning over live traffic.
- Deep integration with specific warehouses or commercial experimentation systems.
- Rich collaboration features such as comments, approvals, or shared workspaces.
- Advanced visualization beyond what is needed to understand the workflow.
- A polished multi-user SaaS product.

## Product success criteria

A good v1 should let someone perform one end-to-end workflow:

1. Define an experiment.
2. Validate the spec.
3. Upload assignment and metric data.
4. Run trust checks such as SRM and balance diagnostics.
5. Compare raw and CUPED-adjusted results when applicable.
6. Export a readable decision memo and a machine-readable result payload.
