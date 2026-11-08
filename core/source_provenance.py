"""Small provenance projection; no analysis or chapter content in library cards."""

import json
import re
from functools import lru_cache


def source_summary(source):
    if not isinstance(source, dict) or not re.fullmatch(
        r"source-[0-9a-f]{64}", str(source.get("source_id", ""))
    ):
        return None
    return {
        key: source.get(key)
        for key in ("source_id", "title", "analysis_sha256", "mode")
    }


@lru_cache(maxsize=512)
def _read(path, _mtime, _size):
    try:
        return source_summary(json.loads(path.read_text("utf-8")))
    except (OSError, ValueError):
        return None


def project_source(project):
    path = project / "outputs/private/source-context.json"
    try:
        if path.resolve() != project.resolve() / "outputs/private/source-context.json":
            return None
        stat = path.stat()
        return _read(path, stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None
