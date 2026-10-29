# Contributing

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pre-commit install
cp .env.example .env
```

## Before you push

```bash
make check      # ruff + mypy --strict + pytest (coverage ≥ 90%) + NLU eval gate
```

Without `make` (e.g. on Windows): `ruff check app tests evals && ruff format --check app tests evals && mypy && pytest --cov && python -m evals.run --min-accuracy 0.95`.

## Conventions

- **Layering:** `api → ai → services → domain`. Routers hold no business logic, and `domain/` does no I/O.
- **Errors:** raise a subclass of `app.core.errors.AppError` with a patient-friendly message.
  The API maps it to a status code and the assistant shows the message as-is.
- **Time:** never call `datetime.now()` in services. Use the injected clock, so tests can freeze it.
- **Schema changes:** append to `MIGRATIONS` and never edit an existing entry.
- **Prompt changes:** bump `PROMPT_VERSION`, and run `python -m evals.run --extractor llm` before and after.
- **NLU changes:** add a case to `evals/datasets/nlu.jsonl` first, then make it pass.
  If accuracy improves, raise the floor in `tests/unit/test_eval_gate.py`.
- **Commits:** small and focused, in the imperative mood ("Add slot lead time setting").
