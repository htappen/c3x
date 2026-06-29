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


def test_list_open_requests_all_open_issues() -> None:
    class RecordingBeads(Beads):
        def __init__(self) -> None:
            super().__init__(Path("/tmp"), executable="bd")
            self.args: list[str] = []

        def _run_json(self, args: list[str]):  # type: ignore[no-untyped-def]
            self.args = args
            return []

    beads = RecordingBeads()

    beads.list_open()

    assert beads.args == ["list", "--status", "open", "--limit", "0", "--json"]


def test_list_active_requests_all_active_issues() -> None:
    class RecordingBeads(Beads):
        def __init__(self) -> None:
            super().__init__(Path("/tmp"), executable="bd")
            self.args: list[str] = []

        def _run_json(self, args: list[str]):  # type: ignore[no-untyped-def]
            self.args = args
            return []

    beads = RecordingBeads()

    beads.list_active()

    assert beads.args == ["list", "--status", "open,in_progress,blocked", "--limit", "0", "--json"]


def test_list_active_is_cached_until_write() -> None:
    class RecordingBeads(Beads):
        def __init__(self) -> None:
            super().__init__(Path("/tmp"), executable="bd")
            self.json_calls = 0
            self.run_calls: list[list[str]] = []

        def require_installed(self) -> None:
            return None

        def _run_json(self, args: list[str]):  # type: ignore[no-untyped-def]
            self.json_calls += 1
            return [{"id": f"bd-{self.json_calls}", "title": "active", "status": "open"}]

        def _run(self, args: list[str], *, expect_json: bool):  # type: ignore[no-untyped-def]
            self.run_calls.append(args)
            return None

    beads = RecordingBeads()

    first = beads.list_active()
    second = beads.list_active()
    beads.add_labels("bd-1", ["flow"])
    third = beads.list_active()

    assert [item.id for item in first] == ["bd-1"]
    assert [item.id for item in second] == ["bd-1"]
    assert [item.id for item in third] == ["bd-2"]
    assert beads.json_calls == 2


def test_list_active_export_reads_jsonl_without_bd(tmp_path: Path) -> None:
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    (beads_dir / "issues.jsonl").write_text(
        '{"id":"bd-open","title":"Open","status":"open","labels":["flow"]}\n'
        '{"id":"bd-done","title":"Done","status":"closed","labels":["flow"]}\n',
        encoding="utf-8",
    )
    beads = Beads(tmp_path)

    active = beads.list_active_export()

    assert [item.id for item in active] == ["bd-open"]


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
