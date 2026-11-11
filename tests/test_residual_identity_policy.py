"""The new capability cannot mutate a completed round's paid input contract."""
from copy import deepcopy
import json

import pytest

from core.planning_coordinator import _identity_policy
from core.outline_planning import content_hash
from test_outline_planning import make_planner, review
from test_outline_coordinated_repair import CoordinatedModel


def test_new_identity_capability_is_bound_to_round_inputs_and_reused(tmp_path):
    planner = make_planner(tmp_path, CoordinatedModel(), count=8)
    candidate = {'characters': [{'id': 'char_a', 'name': 'A'}]}
    verdict = review()
    assert _identity_policy(planner, 'sample', candidate, verdict)
    path = planner.cache / 'sample-identity-policy.json'
    before = path.read_bytes()
    assert _identity_policy(planner, 'sample', candidate, verdict)
    assert path.read_bytes() == before
    altered = deepcopy(candidate)
    altered['characters'][0]['name'] = 'B'
    with pytest.raises(ValueError, match='dependencies|inputs|checkpoint|Checkpoint'):
        _identity_policy(planner, 'sample', altered, verdict)


def test_completed_historical_candidate_retains_original_capability_boundary(tmp_path):
    planner = make_planner(tmp_path, CoordinatedModel(), count=8)
    planner.cache.mkdir(parents=True, exist_ok=True)
    # Existence only suppresses the new stage. Native _record_candidate still
    # verifies the saved candidate/audit before any review can be reused.
    path = planner.cache / 'sample-candidate.json'
    path.write_text('historical candidate must be verified by its existing reader')
    assert not _identity_policy(planner, 'sample', {}, review())
    assert not (planner.cache / 'sample-identity-policy.json').exists()


def test_unknown_identity_policy_never_silently_restarts_the_stage(tmp_path):
    planner = make_planner(tmp_path, CoordinatedModel(), count=8)
    assert _identity_policy(planner, 'sample', {}, review())
    path = planner.cache / 'sample-identity-policy.json'
    value = json.loads(path.read_text())
    value['payload']['version'] = 'unsupported'
    value['payload_hash'] = content_hash(value['payload'])
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='Unsupported residual identity'):
        _identity_policy(planner, 'sample', {}, review())
