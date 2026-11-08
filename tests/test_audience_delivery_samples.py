"""The offline handoff kit exercises production export and validation boundaries."""
import json
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

from scripts.verify_audience_delivery import build


def test_audience_sample_kit_has_three_consistent_channels_and_isolated_failures(tmp_path):
    report = build(tmp_path)
    assert report["model_calls"] == 0 and report["external_h5_tested"] is False
    assert len(report["cases"]) == 6
    for case in report["cases"]:
        assert case["actual"] == case["expected"]
        if case["actual"] != "passed":
            continue
        with ZipFile(tmp_path / case["file"]) as package:
            manifest = json.loads(package.read("package-manifest.json"))
            public = json.loads(package.read(manifest["classification"]["path"]))
            assert public["audience"]["channel"] == case["audience_channel"]
            epub_entry = next(entry for entry in manifest["files"] if entry["media_type"] == "application/epub+zip")
            with ZipFile(BytesIO(package.read(epub_entry["path"]))) as epub:
                opf_path = next(path for path in epub.namelist() if path.endswith(".opf"))
                opf = ElementTree.fromstring(epub.read(opf_path))
                embedded = next(node for node in opf.findall(".//{*}meta")
                                if node.get("property") == "novel-os:classification")
                assert json.loads(embedded.text) == public
            assert manifest["schema_version"] == 4
            h5 = json.loads(package.read(manifest["h5_import"]["path"]))
            assert h5["status"] == "ready" and h5["body_source"] == "epub"
            assert json.loads(package.read(h5["classification"]["path"])) == public
            assert not any("/private/" in name or "knowledge" in name for name in package.namelist())
    errors = {case["file"]: case for case in report["cases"] if case["actual"] == "rejected"}
    assert "audience.channel is unknown" in errors["invalid-unknown_channel.zip"]["cause"]
    assert "EPUB classification conflicts" in errors["invalid-mismatch.zip"]["cause"]
    assert errors["invalid-hash_corrupt.zip"]["error_code"] == "file_digest"
    missing = json.loads((tmp_path / "new-v2-missing-channel.json").read_text())
    assert missing["actual"] == "rejected" and missing["boundary"] == "api.workshop.SessionInput"
    assert "请选择男频或女频" in missing["errors"][0]["msg"]
    assert json.loads((tmp_path / "verification-report.json").read_text()) == report
    assert "不代表外部 H5" in (tmp_path / "README.md").read_text()
