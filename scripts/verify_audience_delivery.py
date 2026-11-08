#!/usr/bin/env python3
"""Build and inspect synthetic audience-channel H5 acceptance packages offline."""
from __future__ import annotations

import argparse
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from xml.etree import ElementTree
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from compile_book import gather  # noqa: E402
from compile_epub import render_epub  # noqa: E402
from styles import StyleSheet  # noqa: E402
from core.delivery_package import build_delivery_package  # noqa: E402
from core.h5_acceptance import inspect_package  # noqa: E402
from core.h5_import import digest, object_digest  # noqa: E402
from core.narrative_format import infer_narrative_format, serialization_payload  # noqa: E402
from core.publication_taxonomy import classification  # noqa: E402


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def synthetic_classification(channel):
    taxonomy = {"schema_version": "novel-taxonomy.v1", "namespace": "novel-os.acceptance",
        "genre": {"id": "genre.romance", "revision": 1,
                  "labels": {"zh-CN": "浪漫", "en-US": "Romance"},
                  "definition": "Synthetic acceptance metadata; no private writing guidance."},
        "tags": [{"id": "tag.betrayal", "revision": 1,
                  "labels": {"zh-CN": "背叛", "en-US": "Betrayal"},
                  "definition": "A broken promise changes a relationship."}]}
    # general exists only for previously frozen legacy data, never a new v2 choice.
    return classification(taxonomy, 2, audience_channel=None if channel == "general" else channel)


def valid_package(root, channel, target):
    selected = synthetic_classification(channel)
    public = selected.to_dict()
    serialization = serialization_payload(infer_narrative_format(
        selected, chapters=2, target_words=2000, explicit_length=True), None, allow_legacy_defaults=True)
    book = gather(title="A Different Choice", author="Acceptance Fixture", genre="Romance",
        chapters=[{"number": 1, "title": "The promise",
                   "text": "She found his letter and chose to ask him herself."},
                  {"number": 2, "title": "The choice",
                   "text": "His answer changed what a promise meant to them."}],
        classification=public, serialization=serialization)
    project = root / channel
    publication = project / "outputs/publication"
    deliverables = project / "outputs/deliverables"
    publication.mkdir(parents=True)
    deliverables.mkdir()
    write_json(publication / "novel-classification.json", public)
    write_json(publication / "novel-serialization.json", serialization)
    (deliverables / "book.epub").write_bytes(render_epub(book, StyleSheet()))
    package = build_delivery_package(project)
    shutil.copyfile(package.archive_path, target)
    return public


def replace_embedded_classification(raw, public):
    output = BytesIO()
    with ZipFile(BytesIO(raw)) as source, ZipFile(output, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename.endswith(".opf"):
                root = ElementTree.fromstring(content)
                found = [node for node in root.findall(".//{*}meta")
                         if node.get("property") == "novel-os:classification"]
                if len(found) != 1:
                    raise ValueError("Synthetic EPUB must contain exactly one classification block")
                found[0].text = encoded(public).decode()
                content = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(info, content)
    return output.getvalue()


def malformed_variant(source, target, *, public=None, synchronize_epub=False, break_hash=False):
    """Intentional fixture corruption, with outer integrity repaired unless requested otherwise."""
    with ZipFile(source) as archive:
        infos = archive.infolist()
        files = {info.filename: archive.read(info.filename) for info in infos}
    manifest = json.loads(files["package-manifest.json"])
    by_role = {entry["role"]: entry for entry in manifest["files"]}
    classification_path = by_role["novel_classification"]["path"]
    if break_hash:
        files[classification_path] += b"\n"
    else:
        files[classification_path] = encoded(public)
        epub_entry = next(entry for entry in manifest["files"] if entry["media_type"] == "application/epub+zip")
        if synchronize_epub:
            files[epub_entry["path"]] = replace_embedded_classification(files[epub_entry["path"]], public)
        h5_entry = by_role["h5_import"]
        h5 = json.loads(files[h5_entry["path"]])

        def refresh(entry):
            content = files[entry["path"]]
            entry.update(size=len(content), sha256=digest(content))
            return entry

        classification_entry = refresh(by_role["novel_classification"])
        refresh(epub_entry)
        for pointer in (manifest["classification"], h5["classification"]):
            pointer.update(size=classification_entry["size"], sha256=classification_entry["sha256"])
        manifest["classification"]["classification_id"] = public["classification_id"]
        h5["epub"].update(size=epub_entry["size"], sha256=epub_entry["sha256"])
        h5["versions"].update(classification_sha256=classification_entry["sha256"], epub_sha256=epub_entry["sha256"])
        h5["import_revision_sha256"] = object_digest({"book_id": h5["book_id"], "versions": h5["versions"]})
        files[h5_entry["path"]] = encoded(h5)
        refresh(h5_entry)
        manifest["h5_import"].update(size=h5_entry["size"], sha256=h5_entry["sha256"])
        manifest["package_revision_sha256"] = object_digest(manifest["files"])
        files["package-manifest.json"] = encoded(manifest)
    with ZipFile(target, "w") as archive:
        for info in infos:
            archive.writestr(info, files[info.filename])


def inspect_case(path, expected_error=None, expected_cause=None):
    result = {"file": path.name, "sha256": digest(path.read_bytes()), "bytes": path.stat().st_size,
              "expected": "rejected" if expected_error else "passed"}
    try:
        report = inspect_package(path)
    except ValueError as exc:
        cause = str(exc.__cause__ or exc)
        code = getattr(exc, "code", type(exc).__name__)
        if code != expected_error or expected_cause and expected_cause not in cause:
            raise AssertionError((path.name, code, cause)) from exc
        result.update(actual="rejected", error_code=code, cause=cause)
    else:
        if expected_error is not None:
            raise AssertionError(f"Invalid sample passed: {path.name}")
        result.update(actual=report["engine_contract_status"],
                      external_import_status=report["external_import_status"],
                      required_h5_capabilities=report["required_h5_capabilities"], chapter_count=report["chapter_count"])
    return result


def missing_channel_evidence():
    from api.workshop import SessionInput
    from pydantic import ValidationError
    payload = {"creation_flow": "genre_tags.v2", "premise": "Synthetic input contract test.",
        "creative_types": [{"id": item, "version": 1, "source_sha256": "a" * 64}
                           for item in ("genre.romance", "tag.betrayal")]}
    try:
        SessionInput.model_validate(payload)
    except ValidationError as exc:
        if "请选择男频或女频" not in str(exc):
            raise AssertionError("Missing-channel sample failed for an unrelated reason") from exc
        return {"expected": "rejected", "actual": "rejected", "boundary": "api.workshop.SessionInput",
                "input": payload, "errors": json.loads(exc.json(include_context=False, include_url=False)), "model_calls": 0}
    raise AssertionError("New v2 input unexpectedly accepted a missing audience channel")


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    public, results = {}, []
    with TemporaryDirectory(prefix="novelos-audience-acceptance-") as temporary:
        for channel in ("male", "female", "general"):
            path = output / f"valid-{channel}.zip"
            public[channel] = valid_package(Path(temporary), channel, path)
            results.append({"audience_channel": channel, **inspect_case(path)})
    source = output / "valid-female.zip"
    unknown = deepcopy(public["female"])
    unknown["audience"]["channel"] = "unknown_channel"
    # This negative fixture has a valid identity digest, isolating the channel enum error.
    identity_fields = ("taxonomy", "catalog_version", "primary_genre_id", "secondary_genre_ids",
                       "story_type_ids", "tone_ids", "setting_ids", "audience", "length")
    unknown["classification_id"] = "classification:" + object_digest({key: unknown[key] for key in identity_fields})
    for name, options, error, cause in (
        ("invalid-unknown_channel.zip", {"public": unknown, "synchronize_epub": True},
         "malformed_package_contract", "audience.channel is unknown"),
        ("invalid-mismatch.zip", {"public": public["male"]},
         "malformed_package_contract", "EPUB classification conflicts with package JSON"),
        ("invalid-hash_corrupt.zip", {"break_hash": True}, "file_digest", None),
    ):
        path = output / name
        malformed_variant(source, path, **options)
        results.append(inspect_case(path, error, cause))
    missing = missing_channel_evidence()
    write_json(output / "new-v2-missing-channel.json", missing)
    report = {"schema": "audience-delivery-acceptance.v1", "model_calls": 0,
              "external_h5_tested": False, "cases": results, "missing_channel_input": missing}
    write_json(output / "verification-report.json", report)
    rows = "\n".join(f"| `{row['file']}` | {row['expected']} | {row['actual']} | "
                     f"{row.get('error_code', 'engine_contract_status=passed')} |" for row in results)
    (output / "README.md").write_text(
        "# 男女频交付协议验收样包\n\n"
        "全部内容为合成两章，无真实小说、私有提示词、模型调用或外部 H5 写入。\n\n"
        "| 文件 | 预期 | 本地实测 | 结果代码 |\n| --- | --- | --- | --- |\n" + rows + "\n\n"
        "`valid-general.zip` 只代表冻结的历史 general 数据；新建 v2 必须选 male/female。\n"
        "`new-v2-missing-channel.json` 记录 API 请求模型在缺失频道时拒绝输入的实测证据，未发送 HTTP 或调用模型。\n\n"
        "unknown_channel：EPUB 与 JSON 一致写入非法频道，重算外层 SHA 与分类身份；应因频道枚举拒绝。\n"
        "mismatch：分类侧车 male、EPUB female，外层 SHA 均正确；应因跨容器不一致拒绝。\n"
        "hash_corrupt：仅增加分类 JSON 末尾换行，保留旧 SHA；应因文件完整性拒绝。\n\n"
        "实测来自生产 `core.h5_acceptance.inspect_package`，不代表外部 H5 已适配或导入成功。\n"
        "有效包使用 Manifest V4、novel-h5-import.v1、ready、EPUB 正文以及动态分类协议。\n"
        "外部 H5 需支持 novel-classification.v2 与 novel-taxonomy.v1 后独立运行这组样包，验证失败导入不会残留数据。\n\n"
        "重建：`python scripts/verify_audience_delivery.py --output-dir /private/tmp/novel-audience-acceptance`\n",
        encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.output_dir.resolve())
    print(json.dumps({"output_dir": str(args.output_dir.resolve()), "cases": len(report["cases"]),
                      "all_expected_results": True, "model_calls": 0, "external_h5_tested": False}))
