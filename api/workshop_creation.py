"""Resolve creation preferences without changing persisted discussion inputs.

Defaults belong to a new creation operation, not the shared input model: the
same model also describes edits and immutable accepted route handoffs.
"""
from copy import deepcopy


DEFAULTS = {'language': 'English', 'audience_channel': 'female',
            'audience': '欧美女性读者', 'market': '欧美'}


def initial_input(body, *, existing=None):
    """Apply defaults only to omitted fields on new creation.

    A route attached to an existing discussion is an explicit partial update.
    Empty strings and nulls are meaningful author edits, never missing values.
    """
    supplied = body.model_fields_set
    if existing is not None:
        value = deepcopy(existing)
        # Access attributes rather than the serializer, which intentionally
        # omits target_total_words=None for old request fingerprints.
        value.update({key: deepcopy(getattr(body, key)) for key in supplied})
        return value
    value = body.model_dump()
    for key, default in DEFAULTS.items():
        if key not in supplied:
            value[key] = default
    if 'audience' not in supplied and value['audience_channel'] != 'female':
        value['audience'] = ''
    return value
