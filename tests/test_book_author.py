"""A book's model-created byline survives all later projections and retries."""

import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

import book_author
from api.main import create_app
from book_author import AuthorGenerationError, ensure_project_author, generate_author_name
from orchestrator import NovelOrchestrator
from pipeline_runner import PipelineRunner
from prompt_intake import ingest_prompt
from state_manager import StoryState, initialize_project
from llm_client import LLMError


def test_model_receives_book_context_and_generates_original_name():
    calls = []

    def complete(system, prompt):
        calls.append((system, json.loads(prompt)))
        return '{"author":"林  听澜"}'

    name = generate_author_name({
        "title": "归潮", "genre": "现实情感", "language": "Chinese",
        "audience": "成年读者", "premise": "她回到海边重新开始。",
    }, complete=complete)
    assert name == "林 听澜"
    assert calls[0][1]["book"]["premise"] == "她回到海边重新开始。"
    assert calls[0][1]["book"]["language"] == "Chinese"
    assert "fictional" in calls[0][0]


def test_invalid_model_response_gets_one_format_repair():
    answers = iter(['{"author":"..."}', '```json\n{"author":"Mara Vale"}\n```'])
    assert generate_author_name({}, complete=lambda *_: next(answers)) == "Mara Vale"


@pytest.mark.parametrize("response", [
    "Mara Vale", '{"author":null}', '{"author":"<script>bad</script>"}',
    '{"author":"One\\nTwo"}', '{"author":".."}', '{"author":"A"}',
    '{"author":"Mara Vale","biography":"A real person"}',
])
def test_invalid_name_never_becomes_a_byline(response):
    with pytest.raises(AuthorGenerationError, match="invalid pen name"):
        generate_author_name({}, complete=lambda *_: response)


def test_generated_author_is_saved_once_and_recovers_after_state_loss(tmp_path):
    initialize_project(str(tmp_path), "Saltlight", "Romance")
    calls = []

    def complete(*args):
        calls.append(args)
        return '{"author":"Mara Vale"}'

    assert ensure_project_author(tmp_path, complete=complete) == "Mara Vale"
    assert ensure_project_author(tmp_path, complete=complete) == "Mara Vale"
    state = StoryState(str(tmp_path))
    state.metadata["author"] = ""
    state.save_state()
    assert ensure_project_author(tmp_path, complete=complete) == "Mara Vale"
    assert len(calls) == 1
    assert StoryState(str(tmp_path)).metadata["author"] == "Mara Vale"


def test_manual_author_wins_and_refreshes_recovery_record(tmp_path, fake_author_model):
    state = initialize_project(str(tmp_path), "Saltlight", "Romance", author="My Pen Name")
    assert ensure_project_author(tmp_path) == "My Pen Name"
    state.metadata["author"] = "Edited Name"
    state.save_state()
    assert ensure_project_author(tmp_path) == "Edited Name"
    assert json.loads((tmp_path / "outputs/input/author.json").read_text())["author"] == "Edited Name"
    assert fake_author_model == []


@pytest.mark.parametrize("symlink_parent", [True, False])
def test_author_record_cannot_follow_symlinks(tmp_path, symlink_parent, fake_author_model):
    project = tmp_path / "project"
    initialize_project(str(project), "Saltlight", "Romance")
    outside = tmp_path / "outside"
    outside.mkdir()
    record = outside / "author.json"
    original = '{"schema_version":"novel-author.v1","author":"External Name"}'
    record.write_text(original)
    input_dir = project / "outputs/input"
    if symlink_parent:
        input_dir.symlink_to(outside, target_is_directory=True)
    else:
        input_dir.mkdir()
        (input_dir / "author.json").symlink_to(record)
    with pytest.raises(AuthorGenerationError, match="symlink"):
        ensure_project_author(project)
    assert record.read_text() == original
    assert fake_author_model == []


def test_failure_does_not_persist_placeholder_or_lose_input(tmp_path, monkeypatch):
    def failed(*_):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(book_author, "_default_complete", failed)
    prompt = "# Saltlight\n\nGenre: Romance\n\nA cartographer starts over."
    with pytest.raises(AuthorGenerationError, match="configured architect model"):
        ingest_prompt(tmp_path, "-", stdin_text=prompt)
    assert not StoryState(str(tmp_path)).metadata.get("author")
    assert not (tmp_path / "outputs/input/author.json").exists()
    assert (tmp_path / "outputs/input/prompt.md").read_text() == prompt


@pytest.mark.parametrize("cause, retryable", [
    (TimeoutError("model timed out"), True),
    (ConnectionError("connection interrupted"), True),
    (LLMError("unexpected status 401 Unauthorized"), False),
])
def test_author_failure_retains_pipeline_retry_policy(cause, retryable):
    def failed(*_):
        raise cause

    with pytest.raises(AuthorGenerationError) as failure:
        generate_author_name({}, complete=failed)
    assert PipelineRunner._is_retryable(failure.value) is retryable


def test_create_failure_returns_clear_error_and_no_partial_book(tmp_path, monkeypatch):
    root, client = _api(tmp_path)

    def failed(*_):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(book_author, "_default_complete", failed)
    response = client.post("/api/projects", json={"title": "Saltlight", "genre": "Romance"})
    assert response.status_code == 400
    assert "author name" in response.json()["detail"].lower()
    assert list(root.glob("*/outputs/state/story_state.json")) == []


def test_intake_dry_run_then_real_run_and_retry(tmp_path, fake_author_model):
    prompt = "# Saltlight\n\nGenre: Romance\nLanguage: English\n\nA cartographer starts over."
    dry = ingest_prompt(tmp_path, "-", stdin_text=prompt, generate_author=False)
    assert not dry.brief["author"]
    assert fake_author_model == []
    real = ingest_prompt(tmp_path, "-", stdin_text=prompt)
    retry = ingest_prompt(tmp_path, "-", stdin_text=prompt)
    assert real.brief["author"] == retry.brief["author"] == "Test Pen Name"
    assert len(fake_author_model) == 1
    assert json.loads((tmp_path / "outputs/input/brief.json").read_text())["author"] == "Test Pen Name"


def test_prompt_author_is_respected(tmp_path, fake_author_model):
    result = ingest_prompt(tmp_path, "-", stdin_text="# Saltlight\nAuthor: Given Name\n\nA story.")
    assert result.brief["author"] == "Given Name"
    assert StoryState(str(tmp_path)).metadata["author"] == "Given Name"
    assert fake_author_model == []


def test_orchestrator_keeps_generated_author_in_active_state(tmp_path):
    orchestrator = NovelOrchestrator(str(tmp_path))
    orchestrator.init_project("Saltlight", "Romance")
    assert orchestrator.state.metadata["author"] == "Test Pen Name"
    orchestrator.state.save_state()
    assert StoryState(str(tmp_path)).metadata["author"] == "Test Pen Name"


@pytest.mark.parametrize("old_author", ["", "Old Name"])
@pytest.mark.parametrize("clear_current", [False, True])
def test_resume_snapshot_cannot_revert_retained_author(tmp_path, old_author, clear_current, fake_author_model):
    state = initialize_project(str(tmp_path), "Saltlight", "Romance", author=old_author)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(state.state_file.read_bytes())
    state.metadata["author"] = "Current Name"
    state.save_state()
    ensure_project_author(tmp_path)
    if clear_current:
        state.metadata["author"] = ""
        state.save_state()
    PipelineRunner()._restore_state(tmp_path, "snapshot.json")
    assert StoryState(str(tmp_path)).metadata["author"] == "Current Name"
    assert fake_author_model == []


def _api(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    return root, TestClient(create_app(projects_root=root, db_url=f"sqlite:///{tmp_path / 'test.db'}"))


def test_api_create_returns_generated_author_and_patch_persists_it(tmp_path, fake_author_model):
    root, client = _api(tmp_path)
    response = client.post("/api/projects", json={
        "title": "Saltlight", "genre": "Romance", "premise": "A cartographer starts over.",
    })
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]
    assert response.json()["author"] == "Test Pen Name"
    assert len(fake_author_model) == 1
    assert json.loads(fake_author_model[0][1])["book"]["premise"] == "A cartographer starts over."
    response = client.patch(f"/api/projects/{project_id}", json={"author": "Mara Vale"})
    assert response.status_code == 200, response.text
    assert response.json()["author"] == "Mara Vale"
    assert json.loads((root / project_id / "outputs/input/author.json").read_text())["author"] == "Mara Vale"
    assert client.patch(f"/api/projects/{project_id}", json={"author": "  "}).status_code == 400
    assert client.get(f"/api/projects/{project_id}").json()["author"] == "Mara Vale"
    assert len(fake_author_model) == 1


def test_legacy_project_reads_stay_offline_and_compile_adds_epub_author(tmp_path, fake_author_model):
    root, client = _api(tmp_path)
    initialize_project(str(root / "book"), "Saltlight", "Romance")
    for endpoint in ("/api/projects", "/api/projects/book", "/api/projects/book/styles"):
        assert client.get(endpoint).status_code == 200
    assert fake_author_model == []
    response = client.get("/api/projects/book/compile?format=epub")
    assert response.status_code == 200, response.text
    with ZipFile(BytesIO(response.content)) as epub:
        opf = next(epub.read(name).decode() for name in epub.namelist() if name.endswith(".opf"))
    assert "<dc:creator>Test Pen Name</dc:creator>" in opf
    assert len(fake_author_model) == 1
    assert client.get("/api/projects/book").json()["author"] == "Test Pen Name"
