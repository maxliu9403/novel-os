"""Keep the author's project-state source distinct from canon installed by planning."""
import hashlib
import json
from pathlib import Path


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _project_design(state_bytes, state):
    data = (json.loads(state_bytes) if state_bytes is not None else
            {"metadata": state.metadata, "story_bible": state.story_bible})
    for field in ("last_saved", "session_log"):
        data.pop(field, None)
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2)


class ProjectDesignSource:
    def __init__(self, directory: Path, state):
        from core.outline_planning import atomic_write
        self.directory = directory
        self.state = state
        self.marker = directory / "applied-project-source.json"
        path = state.state_file
        raw = path.read_bytes() if path.is_file() else None
        self.state_sha256 = hashlib.sha256(raw).hexdigest() if raw is not None else None
        design = _project_design(raw, state)
        origin = json.loads(self.marker.read_bytes()) if self.marker.is_file() else {}
        if origin.get("applied_state_sha256") == _digest(design):
            digest = origin["source_sha256"]
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Invalid standalone planning source identity")
            self.text = (directory / "sources" / f"{digest}.md").read_text(encoding="utf-8")
            if digest != _digest(self.text):
                raise ValueError("Standalone project source checksum mismatch")
        else:
            self.text = ("Original project design. Preserve the supplied facts and author decisions. "
                         "Develop open story choices from this starting point.\n" + design)
            snapshot = directory / "sources" / f"{_digest(self.text)}.md"
            if snapshot.exists():
                if snapshot.read_bytes() != self.text.encode():
                    raise ValueError("Standalone project source checksum mismatch")
            else:
                atomic_write(snapshot, self.text)

    def record_application(self):
        from core.outline_planning import atomic_write
        design = _project_design(self.state.state_file.read_bytes(), self.state)
        atomic_write(self.marker, json.dumps({"source_sha256": _digest(self.text),
                     "applied_state_sha256": _digest(design)}, sort_keys=True))
