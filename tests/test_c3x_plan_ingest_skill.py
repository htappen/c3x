from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "c3x-plan-ingest" / "SKILL.md"
FIXTURE = ROOT / "tests" / "fixtures" / "c3x-plan-ingest" / "simple-plan.md"


def test_c3x_plan_ingest_skill_is_portable_codex_skill() -> None:
    text = SKILL.read_text()

    assert "name: c3x-plan-ingest" in text
    assert "normal Codex session" in text
    assert "Target project directory where `c3x` should run" in text
    assert "run plain `c3x add" in text
    assert "Do not use `--no-validate`" in text
    assert "answer from the source docs when possible" in text
    assert "Redirect a c3x question to the user" in text


def test_c3x_plan_ingest_skill_requires_doc_backed_task_descriptions() -> None:
    text = SKILL.read_text()

    assert "Source docs:" in text
    assert "Requirements:" in text
    assert "Acceptance criteria:" in text
    assert "Verification:" in text
    assert "Notes for c3x master:" in text
    assert "Use source paths plus concise extracted requirements" in text


def test_simple_multi_step_fixture_exercises_task_splitting_contract() -> None:
    fixture = FIXTURE.read_text()
    skill = SKILL.read_text()

    assert "Step 1: Parse Uploads" in fixture
    assert "Step 2: Preview Changes" in fixture
    assert "Step 3: Commit Import" in fixture
    assert "Prefer tasks one worker can complete with a narrow file scope" in skill
    assert "Preserve milestone order and explicit prerequisites" in skill
