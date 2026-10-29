# ADR 0002: Rules first, LLM as a fallback

**Status:** accepted

## Context
Most booking messages are formulaic ("dentist tomorrow 11am", "cancel CB1A2B3C"). Some are not
("the little one needs her shots"). An LLM handles both, but it adds latency (300 ms to 2 s),
cost, an external dependency, and output that can't be trusted blindly.

## Decision
- The deterministic parser in `app/ai/nlu.py` is the primary path.
- `app/ai/llm.py` is consulted only at doctor/date/time steps, when the rules found nothing.
- LLM output is parsed into a schema, invalid fields are dropped one at a time, and values are
  grounded against real data.
- Any LLM failure means "nothing extracted". The assistant then asks a clarifying question.

## Consequences
- The product works fully offline with no API key, and that's how the tests run.
- Rule accuracy is measured (`python -m evals.run`) and gated in CI. The remaining failures are
  exactly the cases that need the LLM (`hard-*` in the dataset).
- An LLM can be swapped or upgraded by running the same eval with `--extractor llm` before
  switching.
