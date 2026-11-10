"""Regressions from real primary-room synthetic questions, not answer templates."""
from pathlib import Path
import pytest
from offline_knowledge import KnowledgeStore
from local_reply import prepare_local_reply
from retrieval_intents import consumer_quality_return_question
from answer_focus import practical_response_focus
from response_goal import response_goal
from answer_usefulness import usefulness_issues

@pytest.mark.parametrize('q',[
 'Я носил купленную куртку неделю, она исправна. Магазин обязан безусловно вернуть деньги?',
 'Купил исправные ботинки, но размер не подошел. Можно обменять?',
 'Хочу вернуть продавцу товар без недостатков, которым уже пользовался',
 'Куртка разонравилась, возможен ли возврат в магазин?',
])
def test_sound_goods_intent_includes_paraphrases(q):assert consumer_quality_return_question(q)

@pytest.mark.parametrize('q',[
 'Неисправный телефон, хочу вернуть деньги',
 'Носил куртку неделю, она порвалась из-за брака. Требую возврат',
 'Арендодатель не возвращает залог за квартиру',
 'Как вернуть банковский перевод?',
])
def test_other_disputes_do_not_become_change_of_mind_return(q):assert not consumer_quality_return_question(q)

def test_real_corpus_retains_unused_condition_and_excludes_customs_and_health():
    k=KnowledgeStore(Path(__file__).parents[2]/'offline_knowledge')
    q='Я носил купленную куртку неделю, она исправна. Магазин обязан безусловно вернуть деньги?'
    p=prepare_local_reply('legal',q,k,allow_model_without_sources=True)
    assert p.answer is None and p.evidence
    assert all(x['document_id']=='consumer-law' for x in p.evidence)
    assert any('не был в употреблении' in ' '.join(x['text'].split()) for x in p.evidence)
    assert 'реальном недостатке' in practical_response_focus(q)

def test_obligation_question_cannot_be_satisfied_by_unrelated_action_list():
    q='Магазин обязан безусловно вернуть деньги за ношеную куртку?'
    assert response_goal(q)['kind']=='permission'
    assert 'direct_explanation_missing' in usefulness_issues(q,'Сохраните чек.\nЗапросите письменный ответ.')
    assert not usefulness_issues(q,'Правило возврата исправного товара имеет условия, включая отсутствие употребления.')

def test_document_task_keeps_priority_over_obligation_vocabulary():
    assert response_goal('Магазин обязан вернуть деньги? Напиши заявление.',format_type='document')['kind']=='document'


@pytest.mark.parametrize('domain',['legal','finance'])
def test_salary_input_checklist_is_direct_and_does_not_need_legal_search(domain):
    class NoSearch:
        def search(self,*args,**kwargs):raise AssertionError('Input collection is not a legal claim')
    p=prepare_local_reply(domain,'Начислено 500000 тенге зарплаты. Какие данные нужны для точного расчёта суммы на руки?',NoSearch())
    assert p.mechanism=='organizational_preparation'
    assert all(word in p.answer for word in ['месяц','резидентства','вычеты','удержания','Не присылайте ИИН'])
    assert 'если ещё не указали' in p.answer and '[S1]' not in p.answer

@pytest.mark.parametrize('q',[
    'Начислено 500000 зарплаты. Сколько точно я получу на руки?',
    'Какие документы по закону нужны для налогового вычета с зарплаты на руки?',
    'Какие данные нужны для кредита на автомобиль?',
])
def test_salary_input_template_does_not_replace_calculation_or_legal_procedure(q):
    from task_preparation import salary_input_request
    assert salary_input_request(q,q) is None


def test_insurance_cost_followup_keeps_apr_but_new_insurance_claim_does_not():
    from conversation import ConversationMemory
    m=ConversationMemory();prior='Чем ГЭСВ банковского займа отличается от номинальной ставки?'
    m.resolve('main','tester','finance',prior)
    search,previous=m.resolve('main','tester','finance','А страховка входит всегда?')
    assert 'ГЭСВ' in search and previous==prior
    m.resolve('other','tester','finance',prior)
    assert m.resolve('other','tester','finance','А страховая отказала в выплате после ДТП?')[1]==''
    m.resolve('third','tester','finance',prior)
    assert m.resolve('third','tester','finance','А комиссия Freedom за перевод?')[1]==''

def test_restructure_obligation_is_not_replaced_with_appeal_checklist():
    from checked_answers import checked_practical_guidance
    evidence=[{'document_id':'regulator-refusal','text':'В случае отказа можно обратиться к банковскому омбудсману или микрофинансовому омбудсману.'}]
    assert checked_practical_guidance('finance','Банк отказал в реструктуризации. Он обязан уменьшить платёж?',evidence) is None

@pytest.mark.parametrize('q',[
    'А страховка входит всегда?',
    'Учитывается ли страховка при расчёте?',
])
def test_apr_followup_gets_the_cost_clause_across_the_context_line(q):
    from conversation import ConversationMemory
    from checked_finance_terms import checked_gesv_answer
    k=KnowledgeStore(Path(__file__).parents[2]/'offline_knowledge');m=ConversationMemory()
    m.resolve('r','s','finance','Что такое ГЭСВ и чем она отличается от номинальной ставки?')
    search,previous=m.resolve('r','s','finance',q)
    assert previous and 'ГЭСВ' in search
    p=prepare_local_reply('finance',q,k,search_question=search,previous_question=previous)
    assert p.mechanism=='source_bound_gesv'
    assert p.answer.index('страхов') < p.answer.index('Для банковского займа')
    assert 'влияет на условия' in p.answer and 'автоматически' in p.answer
    assert checked_gesv_answer('finance',search,[x for x in p.evidence if x['document_id']!='bank-fees-2026']) is None

@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('q',[
    'Могу прислать сюда ИИН и скан удостоверения, чтобы ты разобрал мой трудовой договор?',
    'Хочу отправить тебе паспорт и договор аренды для проверки',
    'Пришлю сюда номер карты и CVV для расчёта комиссии',
])
def test_personal_documents_are_not_requested_for_chat_review(domain,q):
    class NoSearch:
        def search(self,*a,**kw):raise AssertionError('Safe input guidance needs no legal search')
    p=prepare_local_reply(domain,q,NoSearch())
    assert p.mechanism=='organizational_preparation'
    assert 'не присылайте ИИН' in p.answer and 'обезличьте' in p.answer
    assert 'нужного пункта договора' in p.answer and 'Пароли' in p.answer

@pytest.mark.parametrize('q',[
    'Обязан ли банк запрашивать ИИН и согласие?',
    'Можно ли работодателю хранить паспорт?',
    'Какие сведения закон требует в иске?',
])
def test_privacy_input_help_does_not_replace_a_legal_question(q):
    from task_preparation import personal_data_offer_reply
    assert personal_data_offer_reply(q) is None

@pytest.mark.parametrize('q,expected',[
    ('После вычета указанных 10% сколько получится?','2 160 000 тенге'),
    ('После прибавления 5% сколько получится?','2 520 000 тенге'),
])
def test_arithmetic_continuation_without_a_leading_conjunction(q,expected):
    from conversation import ConversationMemory
    m=ConversationMemory();m.resolve('r','s','finance','Сколько будет 13% от 2,4 миллиона тенге?')
    search,previous=m.resolve('r','s','finance',q)
    assert previous
    p=prepare_local_reply('finance',q,None,search_question=search,previous_question=previous)
    assert p.mechanism=='arithmetic' and expected in p.answer


def test_unused_goods_focus_does_not_invent_prior_use():
    q='Купил исправные ботинки, размер не подошёл. Ничего не носил, можно обменять?'
    focus=practical_response_focus(q)
    assert 'которой уже пользовались' not in focus
    assert 'если пользователь сообщил обратное' in focus

@pytest.mark.parametrize('word,number',[
    ('тринадцати',13),('четырнадцати',14),('шестнадцати',16),('семнадцати',17),('девятнадцати',19),
])
def test_renderer_recognizes_supported_written_periods_without_allowing_wrong_units(word,number):
    from agents import unsupported_quantities
    evidence=[{'id':'source','text':f'В течение {word} календарных дней при соблюдении условий.'}]
    assert not unsupported_quantities(f'В течение {number} календарных дней [source]','',evidence,{'source'})
    assert unsupported_quantities(f'В течение {number} рабочих дней [source]','',evidence,{'source'})
    assert unsupported_quantities(f'В течение {number+1} календарных дней [source]','',evidence,{'source'})
    assert unsupported_quantities(f'{number} процентов [source]','',evidence,{'source'})

@pytest.mark.parametrize('q',[
    'После реструктуризации платёж меньше, но срок кредита длиннее. Какие цифры сравнить, чтобы понять общую переплату, без выдуманных тарифов?',
    'Какие данные нужны для сравнения общей переплаты старого и нового графика кредита? Не придумывай комиссии и тарифы.',
])
@pytest.mark.parametrize('domain',['finance','legal'])
def test_credit_comparison_inputs_do_not_require_an_unspecified_bank_price(q,domain):
    class NoSearch:
        def search(self,*a,**kw):raise AssertionError('Comparison inputs are organizational guidance')
    from offline_knowledge import needs_bank
    assert not needs_bank(q)
    p=prepare_local_reply(domain,q,NoSearch())
    assert p.mechanism=='organizational_preparation'
    assert all(w in p.answer for w in ['старый и новый графики','одну и ту же дату','оставшихся платежей','дважды','весь кредит','обезличенные'])
    assert 'Уточните банк' not in p.answer

@pytest.mark.parametrize('q',[
    'Какая комиссия за перевод с карты, без выдуманных тарифов?',
    'Какая стоимость обслуживания кредитной карты? Не придумывай тарифы.',
    'Какая комиссия за перевод без комиссии?',
])
def test_real_price_requests_keep_the_need_for_a_bank(q):
    from offline_knowledge import needs_bank
    assert needs_bank(q)

@pytest.mark.parametrize('q',[
    'Банк обязан дать эти данные по закону для сравнения переплаты кредита?',
    'Какие данные нужны: какая комиссия за изменение графика кредита для расчёта переплаты?',
    'Какие данные нужны для расчёта налога от продажи квартиры?',
    'Какие данные для сравнения переплаты кредита? Напиши заявление банку.',
])
def test_credit_comparison_help_does_not_replace_legal_duties_or_product_prices(q):
    from task_preparation import credit_comparison_inputs
    assert credit_comparison_inputs(q,q) is None

@pytest.fixture(scope='module')
def final_knowledge():
    return KnowledgeStore(Path(__file__).parents[2]/'offline_knowledge')

@pytest.mark.parametrize('domain',['finance','legal'])
@pytest.mark.parametrize('q',[
    'Есть просрочка по ипотеке, банк прислал уведомление. Квартира уже автоматически стала собственностью банка или нужно проверить процедуру?',
    'Квартира в ипотеке. После уведомления о долге она сразу перешла в собственность банка?',
    'Дом в залоге по кредиту. Теперь он принадлежит банку из-за просрочки?',
])
def test_mortgage_notice_answer_preserves_all_statutory_methods(final_knowledge,domain,q):
    p=prepare_local_reply(domain,q,final_knowledge,allow_model_without_sources=True)
    assert p.mechanism=='source_bound_public_rights'
    assert 'По одному уведомлению' in p.answer
    assert 'собственностью банка' in p.answer
    assert 'внесудебный, если он предусмотрен' in p.answer
    assert 'торгов несостоявшимися' in p.answer and 'статье 32' in p.answer
    assert 'только в судебном порядке' not in p.answer
    assert 'Не удалось' not in p.answer and 'Локальные материалы' in p.answer


def test_mortgage_summary_requires_the_complete_actual_clause(final_knowledge):
    from checked_public_rights import checked_mortgage_status_answer
    q='Есть просрочка по ипотеке, банк прислал уведомление. Квартира уже автоматически стала собственностью банка?'
    p=prepare_local_reply('finance',q,final_knowledge)
    assert checked_mortgage_status_answer('finance',q,[]) is None
    modified=[{**r,'text':r['text'].replace('в случае объявления торгов несостоявшимися','по иному основанию')} for r in p.evidence]
    assert checked_mortgage_status_answer('finance',q,modified) is None

@pytest.mark.parametrize('q',[
    'В какой срок после уведомления квартира в ипотеке может перейти в собственность банка?',
    'Напиши иск о переходе заложенной квартиры в собственность банка.',
    'Как обжаловать торги, после которых заложенная квартира стала собственностью банка?',
    'Как продать квартиру в ипотеке, если она ещё моя собственность?',
])
def test_individual_court_or_deadline_tasks_do_not_get_only_the_stage_summary(final_knowledge,q):
    from checked_public_rights import checked_mortgage_status_answer
    assert checked_mortgage_status_answer('finance',q,final_knowledge.search('finance',q,8)) is None


def test_ownership_question_needs_an_explanation_and_draft_priority_is_retained():
    q='Квартира уже автоматически стала собственностью банка или нужно проверить процедуру?'
    assert response_goal(q)['requires_explanation'] is True
    assert 'direct_explanation_missing' in usefulness_issues(q,'Сохраните уведомление.\nЗапросите расчёт.')
    assert response_goal(q+' Напиши заявление.',format_type='document')['kind']=='document'

@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('q',[
    'Договор аренды закончился, собственник удержал весь обеспечительный платёж на всякий случай. Какие факты и документы влияют на спор об удержании?',
    'Хозяин квартиры не отдаёт депозит. Что собрать для проверки удержаний?',
])
def test_rental_input_packet_is_concrete_but_does_not_adjudicate_the_dispute(domain,q):
    class NoSearch:
        def search(self,*a,**kw):raise AssertionError('Input collection needs no legal conclusion')
    p=prepare_local_reply(domain,q,NoSearch())
    assert p.mechanism=='organizational_preparation'
    assert all(word.casefold() in p.answer.casefold() for word in ['каждому удержанию','Договор аренды','Акты передачи','расчёт удержаний','дат','обезличенный'])
    assert 'не устанавливает законность' in p.answer
    assert 'Не удалось' not in p.answer and '[S1]' not in p.answer

@pytest.mark.parametrize('q',[
    'Собственник квартиры в ипотеке, банк удержал платёж. Какие документы нужны?',
    'Законно ли хозяин удержал обеспечительный платёж за квартиру?',
    'Может ли собственник удержать арендный депозит и какие документы мне собрать?',
    'Какие документы по закону обязательны для иска о возврате арендного депозита?',
    'Напиши заявление арендодателю о возврате залога.',
])
def test_rental_input_help_does_not_replace_mortgage_or_legal_procedure(q):
    from task_preparation import rental_dispute_inputs
    assert rental_dispute_inputs(q,q) is None



def test_local_review_ownership_constraint_requires_the_actual_mortgage_clause(final_knowledge):
    from claim_validation import make_quality_packet
    from agents import messages,review_messages
    from review_packet import MARKER
    q='Есть просрочка по ипотеке, банк прислал уведомление. Квартира уже стала собственностью банка?'
    e=prepare_local_reply('finance',q,final_knowledge).evidence
    def packet(evidence):
        return make_quality_packet('finance',messages('finance',q,evidence,''),review_messages('finance',q,MARKER,evidence),{'question':q,'previous_question':'','evidence':evidence})
    assert 'mortgage-security-enforcement-title' in {c['id'] for c in packet(e)['quality']['constraints']}
    without=[p for p in e if not(p['document_id']=='mortgage' and p.get('section','').startswith('Статья 20.'))]
    assert 'mortgage-security-enforcement-title' not in {c['id'] for c in packet(without)['quality']['constraints']}


@pytest.mark.parametrize('q',[
    'Может ли собственник удержать арендный депозит и какие документы мне собрать?',
    'Могут ли меня уволить без объяснений?',
    'Должен ли банк сразу одобрить моё заявление?',
])
def test_other_actor_permission_questions_need_a_substantive_explanation(q):
    assert response_goal(q)['kind']=='permission'
    assert 'direct_explanation_missing' in usefulness_issues(q,'Сохраните документы.\nУточните условия.')


@pytest.mark.parametrize('q',[
    'Ботинки расклеились после прогулки. Возврата нет, потому что носили? Это тот же случай, что исправный товар не подошёл?',
    'Подошва отклеилась у купленных кроссовок. Продавец отказал в возврате, что делать?',
    'Купленный телефон перестал включаться через неделю. Продавец отказал в ремонте, что делать?',
])
def test_reported_fault_does_not_become_sound_goods_return(final_knowledge,q):
    from retrieval_intents import consumer_reported_defect
    assert consumer_reported_defect(q) and not consumer_quality_return_question(q)
    evidence=final_knowledge.search('legal',q,8)
    assert any(p['document_id']=='consumer-law' and p.get('section','').startswith('Статья 15.') for p in evidence)
    assert 'не доказанная причина' in practical_response_focus(q)

@pytest.mark.parametrize('domain',['legal','finance'])
@pytest.mark.parametrize('q,anchors',[
    ('Съёмка ценников в магазине и публикация крупного плана другого покупателя — это одно разрешение?', [('consumer-law','Статья 24.','фото'),('civil-general','Статья 145.','изображени')]),
    ('КАСКО отказало из-за нарушения условия хранения ключей. ОГПО у меня тоже есть. Нужно ли считать, что ОГПО покроет повреждение моей машины?', [('auto-insurance','Статья 3.','третьих лиц'),('civil-special','Статья 839.','')]),
])
def test_mixed_question_preserves_both_relevant_clauses(final_knowledge,domain,q,anchors):
    evidence=final_knowledge.search(domain,q,8)
    assert len(evidence)<=8 and len({p['id'] for p in evidence})==len(evidence)
    for doc,section,phrase in anchors:
        assert any(p['document_id']==doc and p.get('section','').startswith(section) and phrase in ' '.join(p['text'].split()).casefold() for p in evidence)

def test_coverage_never_injects_sources_outside_eligible_candidates(monkeypatch):
    import evidence_scope
    q='Съёмка ценников в магазине и публикация другого покупателя — это одно разрешение?'
    first={'id':'current','document_id':'civil-general','section':'Статья 145.','text':'изображение'}
    allowed={'id':'allowed','document_id':'consumer-law','section':'Статья 24.','text':'фото'}
    blocked={**allowed,'id':'blocked'}
    k=KnowledgeStore.__new__(KnowledgeStore);k._candidate_cache={('legal',(),False,None):([allowed,blocked],)}
    monkeypatch.setattr(evidence_scope,'scope_evidence',lambda d,q,e:[p for p in e if p['id']!='blocked'])
    result=k._ensure_source_coverage('legal',q,[first],2)
    assert {p['id'] for p in result}=={'current','allowed'}
    assert k._ensure_source_coverage('finance',q,[first],2)==[first]
    assert k._ensure_source_coverage('legal',q,[first],1)==[first]
    assert k._ensure_source_coverage('legal',q+' статья 145 Гражданского кодекса (общая часть)',[first],2)==[first]

def test_semantic_merge_cannot_drop_explicit_comparison_coverage(monkeypatch):
    import evidence_scope,semantic_search
    q='Съёмка ценников в магазине и публикация покупателя — это одно разрешение?'
    photo={'id':'photo','document_id':'consumer-law','section':'Статья 24.','text':'фото'}
    image={'id':'image','document_id':'civil-general','section':'Статья 145.','text':'изображение'}
    unrelated={'id':'other','document_id':'civil-general','section':'Статья 1.','text':'иной текст'}
    k=KnowledgeStore.__new__(KnowledgeStore);k._candidate_cache={('legal',(),False,None):([photo,image,unrelated],)}
    monkeypatch.setattr(k,'_lexical_search',lambda *a:[photo,image,unrelated])
    monkeypatch.setattr(evidence_scope,'scope_evidence',lambda d,q,e:e)
    monkeypatch.setattr(semantic_search,'merge_hits',lambda *a:[unrelated])
    class Dense:
        def search(self,*a,**kw):return []
    k.semantic=Dense()
    assert {p['id'] for p in k._search_uncached('legal',q,3)}=={'photo','image','other'}

@pytest.mark.parametrize('q',[
    'Съёмка и публикация в магазине — это одно разрешение?',
    'Товар расклеился: это тот же случай, что товар просто не подошёл?',
    'ОГПО и КАСКО — одно и то же?',
])
def test_colloquial_comparison_needs_substantive_explanation(q):
    assert response_goal(q)['kind']=='comparison'
    assert 'direct_explanation_missing' in usefulness_issues(q,'Сохраните документы.\nЗапросите ответ.')
    assert response_goal(q,format_type='document')['kind']=='document'

@pytest.mark.parametrize('domain',['legal','finance'])
def test_preliminary_auction_packet_does_not_pretend_to_be_an_appeal_procedure(domain):
    q='Как обжаловать торги, после которых заложенная квартира стала собственностью банка? Сначала объясни, какие документы и обстоятельства проверить, без выдуманных сроков.'
    class NoSearch:
        def search(self,*a,**kw):raise AssertionError('Requested preliminary packet needs no normative conclusion')
    p=prepare_local_reply(domain,q,NoSearch())
    assert p.mechanism=='organizational_preparation'
    assert all(word in p.answer for word in ['Протокол торгов','Отчёт об оценке','регистрации права','доказательства их вручения','Расчёт долга','не обязательный комплект','обезличенные'])
    assert '!человек' not in p.answer and '[S1]' not in p.answer

@pytest.mark.parametrize('q',[
    'Как обжаловать торги заложенной квартиры?',
    'Сначала скажи, в какой срок обжаловать торги заложенной квартиры и какие документы собрать?',
    'Сначала напиши жалобу на торги заложенной квартиры, какие документы нужны?',
    'Сначала объясни, какие документы по закону обязательны для суда по торгам заложенной квартиры?',
])
def test_auction_packet_does_not_replace_requested_legal_procedure(q):
    from task_preparation import auction_review_inputs
    assert auction_review_inputs(q,q) is None
