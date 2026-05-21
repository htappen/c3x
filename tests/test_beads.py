from c3x.beads import _summaries


def test_summaries_accepts_issue_list_payload() -> None:
    summaries = _summaries(
        {
            "issues": [
                {
                    "id": "bd-123",
                    "title": "Example",
                    "status": "open",
                    "priority": 2,
                    "type": "task",
                    "labels": ["flow", "inbox", "idea"],
                }
            ]
        }
    )

    assert summaries[0].id == "bd-123"
    assert summaries[0].labels == ("flow", "inbox", "idea")


def test_summaries_ignores_unrecognized_payload() -> None:
    assert _summaries({"unexpected": []}) == []

