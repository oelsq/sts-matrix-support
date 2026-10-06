import pytest
from llm_settings import api_key, model_for_domain

def test_independent_specialist_model_selection(monkeypatch):
    monkeypatch.setenv('LLM_MODEL','legal-chat')
    for x in ['LEGAL_LLM_MODEL','FINANCE_LLM_MODEL','GENERAL_LLM_MODEL']: monkeypatch.delenv(x,raising=False)
    monkeypatch.setenv('LEGAL_LLM_MODEL','legal-chat-gptoss')
    monkeypatch.setenv('FINANCE_LLM_MODEL','legal-chat-qwen38-int4')
    assert model_for_domain('legal')=='legal-chat-gptoss'
    assert model_for_domain('finance')=='legal-chat-qwen38-int4'
    assert model_for_domain('general')=='legal-chat'

def test_missing_model_rejected(monkeypatch):
    monkeypatch.delenv('LLM_MODEL',raising=False);monkeypatch.delenv('LEGAL_LLM_MODEL',raising=False)
    with pytest.raises(RuntimeError):model_for_domain('legal')

def test_key_file_precedes_legacy_provider_key_and_empty_file_fails(tmp_path,monkeypatch):
    p=tmp_path/'key';p.write_text('test-local-gateway-key\n')
    monkeypatch.setenv('LLM_API_KEY_FILE',str(p));monkeypatch.setenv('NITEC_API_KEY','different-provider-key')
    assert api_key()=='test-local-gateway-key'
    p.write_text('')
    with pytest.raises(RuntimeError):api_key()
