"""Presentation examples travel with prompts but can never become evidence."""
import pytest

from agents import messages, review_messages
from answer_style import style_guidance
from review_packet import MARKER
from claim_validation import make_quality_packet, validate_audit


@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('question,needed,unneeded',[
    ('Хочу сравнить два кредита, какой выбрать?', 'Сравни одинаковые параметры', 'Тема: [спорная операция]'),
    ('Напиши нейтральное письмо в банк', 'Кому:', 'Пример допустим'),
])
def test_each_task_gets_a_small_scoped_example_not_the_whole_catalog(domain,question,needed,unneeded):
    text=style_guidance(domain,question)
    assert needed in text and unneeded not in text
    assert 'НЕ источник права' in text and len(text)<2400


@pytest.mark.parametrize('domain',['legal','finance'])
def test_direct_and_protected_model_paths_keep_style_separate_from_sources(domain):
    question='Сравни два предложения: на что обратить внимание?'
    evidence=[{'id':'original-source','text':'Этот локальный фрагмент описывает только представленный документ.'}]
    draft=messages(domain,question,evidence)
    packet=make_quality_packet(domain,draft,review_messages(domain,question,MARKER,evidence),
                               {'question':question,'previous_question':'','evidence':evidence})
    assert style_guidance(domain,question) in packet['draft_system']
    assert len(packet['quality']['sources'])==1
    assert 'Этот локальный фрагмент' in packet['quality']['sources'][0]['text']
    assert 'Пример только организационной части' not in packet['quality']['sources'][0]['text']
    # Even a verbatim style example cannot be forged into proof from S1.
    quote=style_guidance(domain,question).split('организационной части (не готовый ответ на текущий вопрос):\n')[-1]
    audit={'verdict':'approve','checks':[{'unit_id':'C1','status':'supported','proofs':[{'source_id':'S1','quote':quote}]}]}
    assert validate_audit('Комиссия составляет 99 тенге. [S1]',audit,packet['quality']['sources'])


def test_generic_routing_does_not_load_specialist_templates():
    assert style_guidance('general','Что ты умеешь?')==''


@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('q',['Рассчитай переплату по кредиту','Что такое ГЭСВ?','Арендодатель удержал залог. Что делать?'])
def test_unproven_presentations_keep_the_original_prompt(domain,q):
    assert style_guidance(domain,q)==''
    prompt=messages(domain,q,[])[0]['content']
    assert 'СТАНДАРТ ПОДАЧИ' not in prompt
    assert 'Не рассчитывай неизвестную' in prompt or 'не выдумывай' in prompt
