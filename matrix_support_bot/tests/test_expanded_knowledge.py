import json,os,re,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from offline_knowledge import KnowledgeStore,needs_bank
from agents import messages,route,render,legal_aid_eligibility_answer
from urgent_help import urgent_finance_answer,legal_urgent_notice

class ExpandedKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(os.getenv('TEST_KNOWLEDGE_DIR',str(Path(__file__).resolve().parents[2]/'offline_knowledge')))
        cls.store=KnowledgeStore(root)
        cls.entries=json.loads((root/'manifest.json').read_text())['documents']

    def test_general_finance_does_not_require_bank(self):
        q='Не могу платить кредит. Как подать заявление о реструктуризации при просрочке?'
        self.assertFalse(needs_bank(q))
        found=self.store.search('finance',q)
        self.assertTrue(any(p['document_id'] in {'banks','arrears'} for p in found))
        self.assertTrue(all(not p.get('bank') for p in found))

    def test_tariffs_still_require_bank_and_keep_isolation(self):
        self.assertTrue(needs_bank('Какая комиссия за оплату коммунальных услуг?'))
        self.assertEqual(self.store.search('finance','Какая комиссия за оплату коммунальных услуг?'),[])
        evidence=self.store.search('finance','Halyk SuperApp комиссия за оплату коммунальных услуг')
        self.assertTrue(any(p['document_id']=='halyk-superapp' for p in evidence))
        self.assertTrue(all(p['bank']=='halyk' for p in evidence))

    def test_financial_rights_are_shared_with_legal_profile(self):
        found=self.store.search('legal','Урегулирование задолженности заемщика и банковский омбудсман')
        self.assertTrue(any(p['document_id'] in {'banks','banks-transition'} for p in found))

    def test_wage_rights_find_labor_article(self):
        found=self.store.search('legal','Работодатель не выплачивает зарплату. Какой срок выплаты заработной платы установлен?')
        self.assertTrue(any(p['document_id']=='labor' and p['section'].startswith('Статья 113.') for p in found))
        self.assertEqual(route('Работодатель не выплачивает зарплату'),('legal',))

    def test_legal_aid_has_eligibility_article(self):
        found=self.store.search('legal','Как получить гарантированную государством бесплатную юридическую помощь?')
        self.assertTrue(any(p['document_id']=='legal-aid' and p['section'].startswith('Статья 26.') for p in found))

    def test_everyday_free_lawyer_question_preserves_distinct_forms(self):
        from local_reply import prepare_local_reply
        result=prepare_local_reply('legal','Всем ли бесплатно положен адвокат в Казахстане?',self.store)
        self.assertEqual(result.mechanism,'source_bound_legal_aid')
        self.assertIn('информирование',result.answer)
        self.assertIn('консультирования',result.answer)
        self.assertIn('представительство',result.answer)
        self.assertIn('не связан с предпринимательской деятельностью',result.answer)
        self.assertTrue(any(p['document_id']=='legal-aid' and p['section'].startswith('Статья 26.') for p in result.evidence))

    def test_free_criminal_defence_keeps_procedural_route(self):
        from retrieval_intents import general_legal_aid_question
        self.assertFalse(general_legal_aid_question('Меня задержали, положен ли бесплатный адвокат?'))

    def test_future_ombudsman_article_and_clauses_not_retrieved(self):
        found=self.store.search('finance','Финансовый омбудсман досудебное урегулирование задолженности',10000)
        self.assertFalse(any(p['document_id']=='banks' and p['section'].startswith('Статья 65') for p in found))
        all61=[p for p in self.store.passages if p['document_id']=='banks' and p['section'].startswith('Статья 61.')]
        self.assertFalse(any('9. При наличии просроченной задолженности' in p['text'] for p in all61))
        self.assertFalse(any('7. Заемщик вправе обратиться к финансовому омбудсману' in p['text'] for p in all61))
        self.assertTrue(all('01.01.2027' in p['applicability_note'] for p in all61))

    def test_only_current_constitution_loaded(self):
        entry=next(e for e in self.entries if e['id']=='constitution')
        self.assertIn('K2600000000',entry['source'])
        self.assertIn('01.07.2026',entry['applicability_note'])
        self.assertFalse(any('K950001000_' in e['source'] for e in self.entries))

    def test_reported_fraud_gets_cited_immediate_help(self):
        answer=urgent_finance_answer('Мне позвонили мошенники, я сообщил SMS код от карты. Что делать срочно?',self.store)
        self.assertIsNotNone(answer)
        self.assertIn('заблокировать',answer)
        self.assertIn('правоохранительные',answer)
        self.assertIn('!финдеп',answer)
        self.assertRegex(answer,r'\[nb-faq:p\d+:')
        self.assertIn('Бот не блокирует',answer)
        self.assertIsNone(urgent_finance_answer('Как открыть депозит?',self.store))

    def test_urgent_legal_notice_does_not_invent_deadline(self):
        notice=legal_urgent_notice('Завтра суд, срочно нужна помощь')
        self.assertIn('!юрдеп',notice)
        self.assertIn('не приостанавливает сроки',notice)
        self.assertIsNone(legal_urgent_notice('Какие права у покупателя?'))

    def test_prompt_contains_transition_rules_and_source_type(self):
        evidence=self.store.search('finance','просрочка кредита заявление о реструктуризации')
        prompt=json.dumps(messages('finance','просрочка',evidence),ensure_ascii=False)
        self.assertIn('applicability_note',prompt)
        self.assertIn('2027',prompt)
        self.assertIn('Информационная памятка регулятора не равна закону',prompt)

    def test_legal_aid_selection_preserves_distinct_forms(self):
        found=self.store.search('legal','Кто имеет право на бесплатную юридическую помощь?')
        self.assertTrue(any('3. Гарантированная' in p['text'] and 'защиты и представительства' in p['text'] for p in found))
        self.assertTrue(any('правового информирования имеют все' in p['text'] for p in found))

    def test_generated_quote_cannot_drop_eligibility_condition(self):
        text='Право на бесплатное получение гарантированной государством квалифицированной юридической помощи в виде правового информирования имеют все физические и (или) юридические лица.'
        evidence=[{'id':'aid:p9:1:1','text':text,'title':'Юридическая помощь','page':9,'source':'official','reviewed_on':'2026-10-05'}]
        wrong='Цитата: "Право на бесплатное получение гарантированной государством квалифицированной юридической помощи имеют все физические и (или) юридические лица" [S1].'
        self.assertIn('Не удалось',render('legal',wrong,'',evidence))
        correct='Цитата: "'+text+'" [S1].'
        self.assertNotIn('Не удалось',render('legal',correct,'',evidence))

    def test_legal_aid_summary_preserves_limits(self):
        q='Кто имеет право на бесплатную юридическую помощь?'
        evidence=self.store.search('legal',q)
        answer=legal_aid_eligibility_answer(q,evidence)
        self.assertIn('Правовое информирование доступно всем',answer)
        self.assertIn('не связан с предпринимательской',answer)
        self.assertIn('дохода ниже',answer)
        self.assertIn('процессуального законодательства',answer)
        self.assertNotIn('Не удалось',answer)
        self.assertIsNone(legal_aid_eligibility_answer('Как выплатить зарплату?',evidence))

    def test_unsupported_topic_returns_no_match(self):
        self.assertEqual(self.store.search('legal','ксилофон абракадабра'),[])

if __name__=='__main__':unittest.main()
