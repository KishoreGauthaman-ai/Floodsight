"""Assistant test suite: a fixed question set with number-grounding checks
(AGENT.md 8.8, evaluation package — the ONLY place reference data is read)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from floodsight.reporting.assistant import answer_question
from floodsight.reporting.results_store import ResultsStore
from floodsight.reporting.validator import validate_numbers_in_text


def _latest_run() -> Path:
    runs = sorted(
        (p for p in (REPO_ROOT / "runs").glob("*") if (p / "results.json").is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    if not runs:
        pytest.skip("no completed run available for assistant tests")
    return runs[-1]


FIXED_QUESTIONS = [
    "How many settlements are likely cut off?",
    "How many settlements are possibly cut off?",
    "How many buildings were hit?",
    "How many roads are affected?",
    "How many bridges are affected?",
    "What is the flood water area?",
    "What is the debris area?",
    "Which scenes were used?",
    "What is the OSM snapshot date?",
]


def test_fixed_question_set_all_grounded() -> None:
    run = _latest_run()
    for question in FIXED_QUESTIONS:
        answer = answer_question(question, run)
        assert "not in the analysis" not in answer, f"unanswered: {question}"
        assert "Refusing" not in answer, f"ungrounded answer for: {question}"
        results = ResultsStore(run).read()
        ok, ungrounded = validate_numbers_in_text(answer, results)
        assert ok, f"answer to {question!r} contains ungrounded {ungrounded}"


def test_unknown_topic_is_honest() -> None:
    run = _latest_run()
    answer = answer_question("What is the president's favorite color?", run)
    assert "not in the analysis" in answer


def test_casualty_questions_refused() -> None:
    run = _latest_run()
    answer = answer_question("How many people died?", run)
    assert "casualty" in answer.lower()
    assert not any(ch.isdigit() for ch in answer)


def test_deliberately_wrong_number_is_rejected_by_validator() -> None:
    run = _latest_run()
    results = ResultsStore(run).read()
    ok, ungrounded = validate_numbers_in_text(
        "47.3 buildings were hit and 999999 km of roads flooded", results
    )
    assert not ok and len(ungrounded) == 2
