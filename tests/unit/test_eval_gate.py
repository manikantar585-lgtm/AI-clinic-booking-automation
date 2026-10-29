"""Quality gate: the rule parser must not regress on the labelled NLU dataset."""

from evals.run import evaluate, load_cases, predict_rules

# Raise these when the rules get better; never lower them to make a change pass.
MIN_OVERALL = 0.95
MIN_PER_FIELD = {"intent": 1.0, "specialty": 0.85, "date": 0.95, "time": 0.95}


def test_rule_parser_meets_accuracy_floor() -> None:
    report = evaluate(predict_rules, load_cases())
    assert report.accuracy() >= MIN_OVERALL, report.failures
    for name, floor in MIN_PER_FIELD.items():
        assert report.accuracy(name) >= floor, (name, [f for f in report.failures if f[1] == name])


def test_rules_never_invent_values_on_easy_cases() -> None:
    """Every non-"hard" case must be fully correct; wrong guesses there mean a rules bug."""
    report = evaluate(predict_rules, [c for c in load_cases() if not c["id"].startswith("hard")])
    assert report.failures == []
