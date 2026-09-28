"""Allowlisted diagnostics: never log exception messages, bodies, tokens or IDs."""
import logging
import re
import traceback
from uuid import uuid4


def failure(error, *, area, stage):
    raw = str(getattr(error, 'code', ''))
    provider_code = raw if re.fullmatch(r'(?:[0-9A-Z]{5}|PGRST\d{3})', raw) else 'unknown'
    code = ('schema' if provider_code in {'42P01','42703','PGRST204','PGRST205','PGRST202'} else
            'permissions' if provider_code == '42501' else 'backend')
    reference = uuid4().hex[:12]
    frames = ','.join(f'{f.name}:{f.lineno}' for f in traceback.extract_tb(error.__traceback__)[-6:])
    logging.error('api_failure area=%s stage=%s code=%s provider=%s type=%s ref=%s frames=%s',
                  area, stage, code, provider_code, type(error).__name__, reference, frames)
    return code, reference
