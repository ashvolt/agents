# Conventions for this repo

This is a learning repo. Read [PLAN.md](PLAN.md) before doing anything substantial.

## The house rule

**Do not write the level exercises.** `levels/*/exercise.py` is Pragash's to fill in.

When he is stuck:
1. Ask what he has tried and what he expects to happen.
2. Point at the concept or the docs, or explain the mechanism.
3. Give a hint, then a smaller hint. Not the answer.
4. Only write the code if he explicitly says "just show me" — and then explain why it works.

Reviewing his code after he writes it is the job. Typing it for him defeats the repo.

This rule does **not** apply to: scaffolding, test files, READMEs, the capstone's
infrastructure, or anything he explicitly asks to be built.

## Stack

- Python 3.13, `anthropic` SDK used directly. No agent framework unless one earns its place
  and the reason is written down.
- `pydantic` for schemas, `pytest` for tests, `ruff` for lint.
- Models: `claude-opus-5` for graded runs and final sweeps; `claude-haiku-4-5` for
  iteration. Read the model ID from env, never hardcode it in a level exercise.

## Tests

- Anything hitting the real API is marked `@pytest.mark.integration` and costs money.
- Pure logic must be testable with `pytest -m "not integration"`.
- A test that only passes because it calls the API is a bad test. Separate the logic.

## Secrets

Key comes from `.env` via `os.environ` only. Never a literal, never in a committed notebook
output cell, never in a test fixture. If a key ever lands in a commit, rotating it is the
fix — not `git rebase`.

## Commits

Small and honest. Do not squash the learning history; the progression is the artifact.
A commit that says "L2 eval broken, do not trust results" is worth more than a clean lie.
