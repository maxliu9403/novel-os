from pathlib import Path
import hashlib

from core.creative_types import CreativeTypeStore
from core.system_prompts import SystemPromptStore
from scripts.export_creation_library import export_library


def test_export_and_restore_preserve_effective_library_and_title_versions(tmp_path):
    original = tmp_path / "source"
    knowledge = CreativeTypeStore(original / "creative_types/default")
    prompt = SystemPromptStore(original / "system_prompts/default")
    prompt.save("# Custom title direction\nPlain concrete stakes.", 0, "custom-title")
    expected_records = knowledge.records()
    expected_prompt = prompt.get()
    result = export_library(original, tmp_path / "export")
    restored = tmp_path / "export/data"
    assert CreativeTypeStore(restored / "creative_types/default").records() == expected_records
    actual = SystemPromptStore(restored / "system_prompts/default").get()
    assert actual["versions"] == expected_prompt["versions"]
    assert actual["revision"] == expected_prompt["revision"]
    for row in result["items"]:
        if row["markdown_path"]:
            md = (tmp_path / "export/resources" / row["markdown_path"]).read_bytes()
            assert hashlib.sha256(md).hexdigest() == row["sha256"]
    # Exporting never materializes defaults or changes the live library.
    assert not knowledge.path.exists()
    assert prompt.get() == expected_prompt
