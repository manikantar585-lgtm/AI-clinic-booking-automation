"""NLU evaluation: per-field accuracy of the rule parser or an LLM on a labelled dataset.

    python -m evals.run                       # rules only, offline
    python -m evals.run --extractor llm       # uses LLM_* settings from .env
    python -m evals.run --min-accuracy 0.85   # exit 1 if overall accuracy drops below
    python -m evals.run --suite safety        # emergency / self-harm red-flag detector

Each case labels intent, specialty, date ("+N" = N days from today, or ISO) and time.
A null label means "nothing should be extracted"; a wrong guess there counts as an error,
because inventing a date or doctor is worse than asking.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from app.ai.llm import Extraction, OpenAICompatibleExtractor
from app.ai.nlu import detect_intent, match_doctor, parse_date, parse_time
from app.ai.safety import detect_red_flag
from app.core.config import Settings
from app.core.database import DEMO_DOCTORS
from app.domain.models import Doctor

DATASET = Path(__file__).parent / "datasets" / "nlu.jsonl"
SAFETY_DATASET = Path(__file__).parent / "datasets" / "safety.jsonl"
TODAY = date(2026, 11, 2)  # a Monday; keeps relative dates in the dataset stable
FIELDS = ("intent", "specialty", "date", "time")
DOCTORS = [
    Doctor(
        id=i,
        name=n,
        specialty=s,
        work_days=frozenset(int(x) for x in wd.split(",")),
        start_time=a,
        end_time=b,
        slot_minutes=m,
        fee=f,
    )
    for i, (n, s, wd, a, b, m, f) in enumerate(DEMO_DOCTORS, 1)
]


def load_cases(path: Path = DATASET) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _expected_date(label: str | None) -> str | None:
    if label is None:
        return None
    if label.startswith("+"):
        return (TODAY + timedelta(days=int(label[1:]))).isoformat()
    return label


def predict_rules(text: str) -> dict[str, str | None]:
    intent = detect_intent(text)
    doctor = match_doctor(text, DOCTORS)
    day = parse_date(text, TODAY)
    wanted = parse_time(text)
    return {
        "intent": intent.value if intent else None,
        "specialty": doctor.specialty if doctor else None,
        "date": day.isoformat() if day else None,
        "time": wanted.hhmm if wanted else None,
    }


def predict_llm(extractor: OpenAICompatibleExtractor, text: str) -> dict[str, str | None]:
    e: Extraction = extractor.extract(text, DOCTORS, TODAY)
    specialty = next((d.specialty for d in DOCTORS if d.id == e.doctor_id), None)
    return {
        "intent": e.intent.value if e.intent else None,
        "specialty": specialty,
        "date": e.date.isoformat() if e.date else None,
        "time": e.time,
    }


@dataclass
class Report:
    correct: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIELDS, 0))
    total: int = 0
    failures: list[tuple[str, str, object, object]] = field(default_factory=list)

    def accuracy(self, name: str | None = None) -> float:
        if not self.total:
            return 0.0
        if name:
            return self.correct[name] / self.total
        return sum(self.correct.values()) / (self.total * len(FIELDS))


def evaluate(predict, cases: list[dict], skip_intent_for_llm: bool = False) -> Report:
    report = Report()
    for case in cases:
        report.total += 1
        got = predict(case["text"])
        expected = {**{k: case[k] for k in FIELDS}, "date": _expected_date(case["date"])}
        for name in FIELDS:
            # The LLM is never asked for greet/help: the rules always handle those first.
            if skip_intent_for_llm and name == "intent" and expected["intent"] in ("greet", "help", None):
                report.correct[name] += 1
                continue
            if got[name] == expected[name]:
                report.correct[name] += 1
            else:
                report.failures.append((case["id"], name, expected[name], got[name]))
    return report


@dataclass
class SafetyReport:
    """Recall: share of real red flags caught. Specificity: share of normal messages left alone."""

    caught: int = 0
    positives: int = 0
    passed: int = 0
    negatives: int = 0
    failures: list[tuple[str, object, object]] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.caught / self.positives if self.positives else 1.0

    @property
    def specificity(self) -> float:
        return self.passed / self.negatives if self.negatives else 1.0


def evaluate_safety(cases: list[dict]) -> SafetyReport:
    report = SafetyReport()
    for case in cases:
        flag = detect_red_flag(case["text"])
        got = flag.value if flag else None
        if case["label"]:
            report.positives += 1
            report.caught += got == case["label"]
        else:
            report.negatives += 1
            report.passed += got is None
        if got != case["label"]:
            report.failures.append((case["id"], case["label"], got))
    return report


def _run_safety(args: argparse.Namespace) -> int:
    report = evaluate_safety(load_cases(SAFETY_DATASET))
    print(f"\nSafety eval · {report.positives} red flags · {report.negatives} normal messages\n")
    print(f"  recall       {report.recall:6.1%}   (emergencies caught)")
    print(f"  specificity  {report.specificity:6.1%}   (normal messages not flagged)\n")
    if args.show_failures and report.failures:
        for f in report.failures:
            print(f"  {f[0]:<8} expected {f[1]!s:<10} got {f[2]!s}")
    # Recall must always be 100%: a missed emergency is the one failure we never accept.
    if report.recall < 1.0 or report.specificity < args.min_accuracy:
        print("FAIL: every labelled emergency must be caught", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", choices=["nlu", "safety"], default="nlu")
    parser.add_argument("--extractor", choices=["rules", "llm"], default="rules")
    parser.add_argument("--min-accuracy", type=float, default=0.0)
    parser.add_argument("--show-failures", action="store_true")
    args = parser.parse_args(argv)
    if args.suite == "safety":
        return _run_safety(args)

    cases = load_cases()
    started = time.perf_counter()
    if args.extractor == "llm":
        settings = Settings()
        if not settings.llm_enabled:
            print("LLM_API_KEY is not set", file=sys.stderr)
            return 2
        extractor = OpenAICompatibleExtractor(
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        report = evaluate(lambda t: predict_llm(extractor, t), cases, skip_intent_for_llm=True)
        extractor.close()
        label = f"llm ({settings.llm_model})"
    else:
        report = evaluate(predict_rules, cases)
        label = "rules"

    elapsed = time.perf_counter() - started
    print(f"\nNLU eval · {label} · {report.total} cases · {elapsed:.1f}s\n")
    for name in FIELDS:
        print(f"  {name:<10} {report.accuracy(name):6.1%}")
    print(f"  {'overall':<10} {report.accuracy():6.1%}\n")
    if args.show_failures and report.failures:
        print("Failures (case, field, expected, got):")
        for f in report.failures:
            print(f"  {f[0]:<8} {f[1]:<10} {f[2]!s:<20} {f[3]!s}")
    if report.accuracy() < args.min_accuracy:
        print(f"FAIL: overall accuracy below {args.min_accuracy:.0%}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
