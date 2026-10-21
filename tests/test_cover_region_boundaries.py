"""Normalized canvas edges tolerate arithmetic roundoff, not visible overflow."""
import pytest

from core.cover_campaign import _region as campaign_region
from core.cover_typography import _region as typography_region


@pytest.mark.parametrize('validate', [campaign_region, typography_region])
@pytest.mark.parametrize('region', [
    [.56, .26, .44, .39], [.55, .63, .45, .37],
    [.1, .1, .9000000000000001, .9000000000000001],
    [0, 0, 1, 1],
])
def test_legitimate_edge_aligned_regions_accept_binary_roundoff(validate, region):
    validate(region, 'fixture region')


@pytest.mark.parametrize('validate', [campaign_region, typography_region])
@pytest.mark.parametrize('region', [
    [.56, .26, .44000001, .39], [.55, .63, .45, .37000001],
    [-.00000001, 0, .5, .5], [0, -.00000001, .5, .5],
    [0, 0, 0, .5], [0, 0, .5, 0], [0, 0, float('nan'), .5],
    [0, 0, .5, float('inf')],
    [1, 0, 1e-14, .5], [0, 1, .5, 1e-14],
])
def test_true_overflow_negative_empty_or_nonfinite_regions_still_fail(validate, region):
    with pytest.raises(ValueError):
        validate(region, 'fixture region')
