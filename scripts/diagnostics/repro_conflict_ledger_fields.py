"""Offline regression replay; the final corrected response is a test fixture, not an LLM call.

Reads the captured extraction responses and immutable Final sources. Never writes
to the project or resumes a run. Exit 1 reproduces the missing consolidation
repair; exit 0 means the real extraction service consumed the supplied repair.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
from publication_copy_service import PublicationCopyService
from publication_source import build_publication_source_set


class ReplayClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        if not self.responses:
            raise AssertionError("Unexpected call beyond captured responses and one repair fixture")
        return self.responses.pop(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("run_id")
    parser.add_argument("--chapters", type=int, required=True)
    parser.add_argument("--capture", default="publication-copy-attempt-01.raw")
    args = parser.parse_args()
    run = args.project / "outputs/runs" / args.run_id
    raw_path = run / "feedback" / args.capture
    body = raw_path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    assert digest == Path(str(raw_path) + ".sha256").read_text().strip()
    matches = list(re.finditer(r"\[response \d+ sha256=([0-9a-f]{64})\]\n", body.decode()))
    text = body.decode()
    raws = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() - 2 if index + 1 < len(matches) else len(text)
        raw = text[match.end():end]
        assert hashlib.sha256(raw.encode()).hexdigest() == match[1]
        raws.append(raw)
    assert raws, "Expected multi-response feedback capture"
    corrected = json.loads(raws[-1])
    # Fixture construction ONLY: production still rejects extra fields and asks
    # the model for a corrected response; it does not strip unknown fields.
    corrected["evidence"] = {
        bucket: [{"chapter": item["chapter"], "source_quote": item["source_quote"]}
                 for item in entries]
        for bucket, entries in corrected["evidence"].items()
    }
    client = ReplayClient([*raws, json.dumps(corrected, ensure_ascii=False)])
    source = build_publication_source_set(
        args.project, run_id=args.run_id, chapter_count=args.chapters,
        quality_policy="evidence_v1",
    )
    manifest = json.loads((run / "run.json").read_text())
    stage = next(s for s in manifest["stages"].values() if s["phase"] == "publication.copy")
    assert source.source_set_sha256 == stage["input_hashes"]["source_set_sha256"]
    result = {
        "run_id": args.run_id,
        "mode": "offline captured replay with one simulated repair response",
        "capture_sha256": digest,
        "source_set_sha256": source.source_set_sha256,
        "captured_responses": len(raws),
    }
    try:
        conflict, responses, groups = PublicationCopyService(client, None)._extract_conflict(
            source, {"run_id": args.run_id, "source_set_sha256": source.source_set_sha256},
        )
        assert not client.responses, "Expected bounded repair to consume fixture"
        assert len(responses) == len(raws) + 1
        assert conflict.to_dict() == corrected
        result.update(status="pass", source_groups=len(groups), responses=len(responses))
    except Exception as exc:
        result.update(status="fail", error=str(exc), error_type=type(exc).__name__)
    result["model_calls_replayed"] = client.calls
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
