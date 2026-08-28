"""Public quality evidence contracts."""

import importlib
import sys


_PACKAGE_ALIASES = {
    "quality": "core.quality",
    "core.quality": "quality",
}
_alternate_package_name = _PACKAGE_ALIASES.get(__name__)
if _alternate_package_name is not None:
    sys.modules[_alternate_package_name] = sys.modules[__name__]
    if "." in _alternate_package_name:
        _parent_name, _child_name = _alternate_package_name.rsplit(".", 1)
        try:
            _parent_package = importlib.import_module(_parent_name)
        except ModuleNotFoundError as exc:
            if exc.name != _parent_name:
                raise
        else:
            setattr(_parent_package, _child_name, sys.modules[__name__])

from .evidence import verify_evidence
from .lab import QualityLab
from .models import (
    DEFAULT_RUBRIC_VERSION,
    EvidenceSpan,
    EvaluationReport,
    EvaluationRequest,
    QualityFinding,
)

if _alternate_package_name is not None:
    sys.modules[f"{_alternate_package_name}.models"] = sys.modules[
        f"{__name__}.models"
    ]
    sys.modules[f"{_alternate_package_name}.evidence"] = sys.modules[
        f"{__name__}.evidence"
    ]
    sys.modules[f"{_alternate_package_name}.lab"] = sys.modules[f"{__name__}.lab"]

__all__ = [
    "DEFAULT_RUBRIC_VERSION",
    "EvidenceSpan",
    "EvaluationReport",
    "EvaluationRequest",
    "QualityFinding",
    "QualityLab",
    "verify_evidence",
]
