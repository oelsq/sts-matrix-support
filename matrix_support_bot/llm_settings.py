"""LLM gateway credentials and configurable models for specialist domains."""
import os
from pathlib import Path

def api_key():
    filename = os.getenv('LLM_API_KEY_FILE')
    if filename:
        value = Path(filename).read_text(encoding='utf-8').strip()
        if not value:
            raise RuntimeError('Empty LLM_API_KEY_FILE')
        return value
    value = os.getenv('LLM_API_KEY') or os.getenv('NITEC_API_KEY')
    if not value:
        raise RuntimeError('Missing LLM_API_KEY, LLM_API_KEY_FILE or NITEC_API_KEY')
    return value

def model_for_domain(domain):
    names = {'legal':'LEGAL_LLM_MODEL','finance':'FINANCE_LLM_MODEL','general':'GENERAL_LLM_MODEL'}
    value = (os.getenv(names.get(domain, 'GENERAL_LLM_MODEL')) or os.getenv('LLM_MODEL') or '').strip()
    if not value:
        raise RuntimeError('Missing LLM model')
    return value
