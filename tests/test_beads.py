from pathlib import Path

from c3x.beads import Beads, BeadsError, BeadSummary, _summaries


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
    assert summaries[0].type == "task"


def test_summaries_accepts_export_field_names() -> None:
    summaries = _summaries(
        [
            {
                "id": "bd-123",
                "title": "Example",
                "issue_type": "task",
                "acceptance_criteria": "done",
            }
        ]
    )

    assert summaries[0].type == "task"
    assert summaries[0].acceptance == "done"


def test_summaries_ignores_unrecognized_payload() -> None:
    assert _summaries({"unexpected": []}) == []


def test_list_closed_requests_all_closed_issues() -> None:
    class RecordingBeads(Beads):
        def __init__(self) -> None:
            super().__init__(Path("/tmp"), executable="bd")
            self.args: list[str] = []

        def _run_json(self, args: list[str]):  # type: ignore[no-untyped-def]
            self.args = args
            return [{"id": "bd-1", "title": "done", "status": "closed"}]

    beads = RecordingBeads()

    closed = beads.list_closed()

    assert beads.args == ["list", "--status", "closed", "--limit", "0", "--json"]
    assert closed[0].status == "closed"


def test_compact_issue_falls_back_to_import_when_admin_compact_is_unsupported() -> None:
    class EmbeddedBeads(Beads):
        def __init__(self) -> None:
            super().__init__(Path("/tmp"), executable="bd")
            self.calls: list[list[str]] = []

        def require_installed(self) -> None:
            return None

        def _run(self, args: list[str], *, expect_json: bool):  # type: ignore[no-untyped-def]
            self.calls.append(args)
            if args[:2] == ["admin", "compact"]:
                raise BeadsError("'bd admin compact' is not yet supported in embedded mode")
            return None

    beads = EmbeddedBeads()

    beads.compact_issue(
        "bd-123",
        "small summary",
        issue=BeadSummary(
            id="bd-123",
            title="Example",
            status="blocked",
            priority=1,
            type="task",
            acceptance="done",
            labels=("flow", "blocked"),
        ),
    )

    assert beads.calls[0][:2] == ["admin", "compact"]
    assert beads.calls[1][0] == "import"
