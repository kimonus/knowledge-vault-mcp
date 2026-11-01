"""Question-only lexical fallback preserves explicit syntax and subject terms."""

from knowledge_vault.services.search import _question_subject_query


def test_plain_question_keeps_subject_and_action():
    assert _question_subject_query("What is the current home nas backup.target?") == (
        "home nas backup.target"
    )
    assert _question_subject_query("Which tablet did the user purchase?") == "tablet purchased"
    assert _question_subject_query("Why did the user select cell-a for the battery?") == (
        "selected cell-a battery"
    )
    assert _question_subject_query("Which violin model did the user purchase?") == (
        "violin model purchased"
    )


def test_exact_or_ambiguous_queries_are_not_rewritten():
    assert _question_subject_query("tablet") is None
    assert _question_subject_query('"tablet purchased"') is None
    assert _question_subject_query("tablet OR camera") is None
    assert _question_subject_query("Which tablet -camera?") is None
    assert _question_subject_query("What is it?") is None
    long_question = (
        "What happened with tablet, camera, monitor, router, nas, printer, keyboard, "
        "laptop, and phone?"
    )
    assert _question_subject_query(long_question) is None
