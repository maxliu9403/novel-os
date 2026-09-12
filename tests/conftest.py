"""Shared pytest setup: put the `core` package dir on sys.path.

The core modules import each other by top-level name (e.g. `from llm_client import ...`),
so we add `core/` to the path rather than importing it as a package.
"""

import sys
import pytest
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))


@pytest.fixture(autouse=True)
def block_live_method_review_client(monkeypatch, request):
    """New advisory hooks must never resolve a developer's billable model in tests."""
    from narrative_methods import runtime

    def missing_fake():
        raise RuntimeError("method review tests must inject a fake client")

    monkeypatch.setattr(runtime, "_default_client", missing_fake)
    from model_router import ModelRouter
    if "use_frozen_router" not in request.fixturenames:
        monkeypatch.setattr(ModelRouter, "client_from_snapshot", staticmethod(lambda _snapshot: missing_fake()))


@pytest.fixture
def use_frozen_router():
    """Opt-in only for snapshot adapter tests that replace the SDK constructor."""
