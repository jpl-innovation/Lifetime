"""Optional local/server identity; existing department text is never split or guessed."""
import datetime as dt
from copy import deepcopy

FIELDS = ('display_name', 'asset_tag', 'department', 'deployment_date', 'location')
LABELS = {'display_name': 'Display name', 'asset_tag': 'Asset tag',
          'department': 'Department / location', 'deployment_date': 'Deployment date',
          'location': 'Location'}


def iso_date(value):
    if not isinstance(value, str):
        raise ValueError('Date must be YYYY-MM-DD')
    parsed = dt.date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('Date must be YYYY-MM-DD')
    return parsed


def normalize(value, network=False):
    if not isinstance(value, dict) or (network and set(value) - set(FIELDS)):
        raise ValueError('Invalid identity fields')
    result = dict.fromkeys(FIELDS, '')
    result.update(deepcopy(value))
    # Location used to be an unrestricted extension key. Preserve such a value
    # rather than failing closed and hiding an existing installation's consent.
    if not network and not isinstance(result['location'], str):
        key = '_legacy_location'
        suffix = 1
        while key in result:
            suffix += 1
            key = '_legacy_location_' + str(suffix)
        result[key] = result['location']
        result['location'] = ''
    for key in FIELDS:
        text = result[key]
        if not isinstance(text, str) or (network and (len(text) > 4096 or '\x00' in text)):
            raise ValueError('Invalid identity text')
    if result['deployment_date']:
        iso_date(result['deployment_date'])
    return result
