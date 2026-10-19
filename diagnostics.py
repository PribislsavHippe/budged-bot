"""Allowlisted diagnostics: never log exception messages, bodies, tokens or IDs."""
import logging
import re
import traceback
from uuid import uuid4
from pathlib import Path
from httpx import TransportError

_PROJECT = Path(__file__).resolve().parent


def failure(error, *, area, stage):
    raw = str(getattr(error, 'code', ''))
    provider_code = raw if re.fullmatch(r'(?:[0-9A-Z]{5}|PGRST\d{3})', raw) else 'unknown'
    code = ('schema' if provider_code in {'42P01','42703','PGRST204','PGRST205','PGRST202'} else
            'permissions' if provider_code == '42501' else 'backend')
    if isinstance(error, TransportError):
        code = 'network'
        origin = getattr(error, '_diagnostic_provider', None)
        provider_code = origin if origin in {'supabase'} else 'unknown'
    reference = uuid4().hex[:12]
    stack = traceback.extract_tb(error.__traceback__)
    # Keep the calling application path; library-only tails cannot locate a fault.
    local = [f for f in stack if Path(f.filename).resolve().parent == _PROJECT]
    frames = ','.join(f'{Path(f.filename).name}:{f.name}:{f.lineno}'
                      for f in (local[-4:] + stack[-3:]))
    logging.error('api_failure area=%s stage=%s code=%s provider=%s type=%s ref=%s frames=%s',
                  area, stage, code, provider_code, type(error).__name__, reference, frames)
    return code, reference
