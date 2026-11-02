"""Validate preference patches before any file or runtime state is changed."""

from pathlib import Path
import tomllib

from openrelife.retention import validate_days


def app_version():
    """Use the shipped project manifest before potentially stale venv metadata."""
    manifest = Path(__file__).resolve().parent.parent / 'pyproject.toml'
    if manifest.is_file():
        with manifest.open('rb') as stream:
            return tomllib.load(stream)['project']['version']
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version('openrelife')
    except PackageNotFoundError:
        return 'Development'


def validate_patch(data):
    if not isinstance(data, dict):
        raise ValueError('Settings must be a JSON object')
    allowed = {'interval', 'retention_days', 'quality', 'ocr_cooldown',
               'ocr_compute_mode', 'use_apple_vision', 'skip_incognito', 'port'}
    if set(data) - allowed:
        raise ValueError('Unknown settings field')
    result = dict(data)
    for key, minimum, maximum in [('interval', 1, 86400), ('ocr_cooldown', 10, 86400),
                                   ('port', 1024, 65535)]:
        if key not in result:
            continue
        value = result[key]
        try:
            number = int(value)
            if isinstance(value, bool) or float(value) != number or not minimum <= number <= maximum:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise ValueError(f'{key} must be an integer between {minimum} and {maximum}') from None
        result[key] = number
    if 'retention_days' in result:
        result['retention_days'] = validate_days(result['retention_days'])
    if 'quality' in result and result['quality'] not in ('low', 'medium', 'high'):
        raise ValueError('Choose a valid screenshot quality')
    if 'ocr_compute_mode' in result and result['ocr_compute_mode'] not in ('aggressive', 'smart', 'on_charge_only', 'eco', 'disabled'):
        raise ValueError('Choose a valid OCR processing mode')
    for key in ('use_apple_vision', 'skip_incognito'):
        if key in result and not isinstance(result[key], bool):
            raise ValueError(f'{key} must be true or false')
    return result
