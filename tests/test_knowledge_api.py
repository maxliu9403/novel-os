"""Author Markdown stays byte-exact through editing, activation and export."""

from hashlib import sha256
from pathlib import Path

from test_workshop_api import client  # noqa: F401


def test_markdown_keeps_trailing_whitespace_and_roundtrips_via_api(client):
    record = client.get("/api/creative-types/tag.betrayal").json()
    markdown = record["draft"] + "\n\n<!-- editorial note -->  \n\n"
    payload = {
        "markdown": markdown,
        "revision": record["revision"],
        "operation_id": "exact-markdown-save",
    }
    saved = client.post("/api/creative-types/tag.betrayal/draft", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["draft"] == markdown
    assert client.get("/api/creative-types/tag.betrayal").json()["draft"] == markdown
    payload.update(
        revision=saved.json()["revision"], operation_id="exact-markdown-activate"
    )
    active = client.post("/api/creative-types/tag.betrayal/activate", json=payload)
    assert active.status_code == 200, active.text
    version = active.json()["versions"][-1]
    assert version["markdown"] == markdown
    assert version["sha256"] == sha256(markdown.encode()).hexdigest()


def test_retired_reader_review_cannot_start_or_resume(client):
    assert client.get("/api/reader-reviews").status_code == 404
    assert client.post("/api/reader-reviews", json={}).status_code == 404
    assert client.post("/api/reader-reviews/old/resume", json={}).status_code == 404


def test_restore_reuses_immutable_version_and_is_revision_checked(client):
    identifier = "tag.betrayal"
    old = client.get(f"/api/creative-types/{identifier}").json()
    changed = old["draft"] + "\n\n<!-- author edit -->\n"
    new = client.post(f"/api/creative-types/{identifier}/activate", json={
        "revision": old["revision"], "operation_id": "activate-new-version", "markdown": changed,
    }).json()
    body = {"revision": new["revision"], "operation_id": "restore-prior-version", "version": 1}
    restored = client.post(f"/api/creative-types/{identifier}/restore", json=body)
    assert restored.status_code == 200, restored.text
    result = restored.json()
    assert result["active_version"] == 1
    assert result["draft"] == old["draft"]
    assert result["versions"] == new["versions"]
    assert result["revision"] == new["revision"] + 1
    assert client.post(f"/api/creative-types/{identifier}/restore", json=body).json() == result
    assert client.post(f"/api/creative-types/{identifier}/restore", json={**body, "operation_id": "stale-version-request"}).status_code == 409
    assert client.post(f"/api/creative-types/{identifier}/restore", json={**body, "revision": result["revision"], "version": 999, "operation_id": "unknown-version-request"}).status_code == 409


def test_author_tag_format_roundtrip_and_model_preview(client):
    markdown = (Path(__file__).parents[1] / "resources/knowledge-imports/romance-35-tags/Betrayal.md").read_bytes().decode("utf-8")
    preview = client.post("/api/creative-types/preview", json={"markdown": markdown})
    assert preview.status_code == 200, preview.text
    assert preview.json()["document"]["format"] == "tag-prompt.v1"
    record = client.get("/api/creative-types/tag.betrayal").json()
    activated = client.post("/api/creative-types/tag.betrayal/activate", json={
        "markdown": markdown, "revision": record["revision"], "operation_id": "author-tag-format-import",
    })
    assert activated.status_code == 200, activated.text
    assert client.get("/api/creative-types/tag.betrayal/markdown").content == markdown.encode()
    records = [client.get("/api/creative-types/genre.romance").json(), activated.json()]
    refs = [{"id": r["id"], "version": r["active_version"], "source_sha256": r["versions"][-1]["sha256"]} for r in records]
    for stage in ["brainstorm", "draft", "cover", "publication"]:
        response = client.post("/api/creative-types/preview", json={"refs": refs, "stage": stage})
        assert response.status_code == 200, response.text
        assert response.json()["context"]["documents"][1]["markdown"] == markdown


def test_flexible_import_preview_activation_and_model_input(client):
    from core.knowledge_markdown import document_body
    original = client.get('/api/creative-types/tag.dark_romance').json()
    header = original['draft'].split('---', 2)[1]
    body = '**系统定位**\n完整保留前言。  \n\n## 一、自定义标题\n内容一\n## 一、自定义标题\n内容二\n'
    raw = '---' + header + '---\n' + body
    response = client.post('/api/creative-types/prepare', json={'markdown': raw})
    assert response.status_code == 200, response.text
    prepared = response.json()
    assert prepared['document']['body'] == body
    # Prepare is read-only, even when matching an existing ID.
    assert client.get('/api/creative-types/tag.dark_romance').json() == original
    active = client.post('/api/creative-types/tag.dark_romance/activate', json={
        'markdown': prepared['markdown'], 'revision': original['revision'],
        'operation_id': 'activate-free-markdown',
    })
    assert active.status_code == 200, active.text
    assert active.json()['document']['body'] == body
    assert document_body(client.get('/api/creative-types/tag.dark_romance/markdown').text) == body
    records = [client.get('/api/creative-types/genre.romance').json(), active.json()]
    refs = [{'id': r['id'], 'version': r['active_version'], 'source_sha256': r['versions'][-1]['sha256']} for r in records]
    for stage in ['brainstorm', 'story_design', 'chapter_plan', 'draft', 'review', 'publication', 'cover']:
        result = client.post('/api/creative-types/preview', json={'refs': refs, 'stage': stage})
        assert result.status_code == 200, result.text
        assert result.json()['context']['documents'][1]['markdown'] == body


def test_plain_file_identity_and_body_edit_keep_public_metadata(client):
    initial = client.post('/api/creative-types/prepare', json={'markdown': '自由正文\n'})
    assert initial.json() == {'needs_identity': True, 'body': '自由正文\n'}
    record = client.get('/api/creative-types/tag.dark_romance').json()
    prepared = client.post('/api/creative-types/prepare', json={
        'markdown': '新的普通正文\n', 'base_markdown': record['draft'],
    }).json()
    assert prepared['document']['id'] == record['id']
    assert prepared['document']['publication'] == record['document']['publication']
    assert prepared['document']['genre_ids'] == record['document']['genre_ids']
    assert prepared['document']['body'] == '新的普通正文\n'
    assert client.post('/api/creative-types/prepare', json={
        'markdown': 'x', 'base_markdown': record['draft'],
        'identity': {'id': 'genre.custom', 'kind': 'genre', 'name': 'x'},
    }).status_code == 422


def test_new_template_has_no_required_sections_or_user_written_yaml(client):
    response = client.get('/api/creative-types/template', params={
        'id': 'tag.new_type', 'kind': 'tag', 'name': '新资料', 'genre_id': 'genre.romance',
    })
    assert response.status_code == 200, response.text
    prepared = client.post('/api/creative-types/prepare', json={'markdown': response.json()['markdown']}).json()
    assert prepared['document']['body'] == ''
    assert prepared['document']['missing_sections'] == []
