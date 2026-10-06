import json
import tempfile
import unittest
from pathlib import Path
from agents import render
from offline_knowledge import KnowledgeStore

class PdfAnswerTests(unittest.TestCase):
    def test_supported_numbers_survive_and_only_used_references_are_listed(self):
        evidence=[{'id':'bank:p1:1:1','text':'Оплата коммунальных услуг 0 теңге',
                   'title':'Halyk SuperApp','page':1,'source':'https://halykbank.kz/tariffs',
                   'reviewed_on':'2026-10-01'},
                  {'id':'bank:p2:1:1','text':'Иные услуги','title':'Unused',
                   'page':2,'source':'https://halykbank.kz/tariffs','reviewed_on':'2026-10-01'}]
        answer=render('finance','Комиссия 0 теңге. [bank:p1:1:1]','',evidence)
        self.assertIn('Комиссия 0 теңге',answer)
        self.assertNotIn('Unused',answer)
        legal=render('legal','Ответ в течение десяти календарных дней. [bank:p1:1:1]','',evidence)
        self.assertIn('десяти календарных дней',legal)

    def test_invented_citation_is_rejected_even_with_valid_citation(self):
        evidence=[{'id':'bank:p1:1:1'}]
        answer=render('finance','Ответ [bank:p1:1:1] и [fake:p10:1:1]','',evidence)
        self.assertIn('Не удалось подготовить ответ',answer)
        self.assertNotIn('fake',answer)

    def test_exact_id_with_model_wrapper_is_normalized(self):
        evidence=[{'id':'law:p34:2:1','text':'десяти календарных дней',
                   'title':'Закон РК','page':34,'source':'official','reviewed_on':'2026-10-01'}]
        answer=render('legal','Срок - десять дней. [ID "law:p34:2:1"]','',evidence)
        self.assertIn('[law:p34:2:1]',answer)
        self.assertNotIn('Не удалось',answer)
        colon=render('legal','Срок - десять дней. [ID: law:p34:2:1]','',evidence)
        self.assertIn('[law:p34:2:1]',colon)
        self.assertIn('Цитата из документа',colon)
        unknown=render('legal','Ответ [ID "fake:p2:1:1"]','',evidence)
        self.assertIn('Не удалось',unknown)

    def test_short_source_alias_is_resolved_and_unknown_alias_refused(self):
        evidence=[{'id':'bank:p1:1:1','text':'Оплата коммунальных услуг 0 теңге',
                   'title':'Halyk','page':1,'source':'official','reviewed_on':'2026-10-01'}]
        answer=render('finance','Комиссия 0 теңге. [S1]','',evidence)
        self.assertIn('[bank:p1:1:1]',answer)
        self.assertNotIn('[S1]',answer)
        self.assertIn('Не удалось',render('finance','Ответ [S1, S9]','',evidence))

    def test_quote_selects_tariff_row_instead_of_document_history(self):
        evidence=[{'id':'bank:p1:1:1','text':'История тарифов '+('изменения ' * 50)+'\n7 Оплата коммунальных услуг 0 теңге 0 теңге\n8 Прочие платежи',
                   'title':'Halyk','page':1,'source':'official','reviewed_on':'2026-10-01'}]
        answer=render('finance','Комиссия 0 теңге. [S1]','Halyk комиссия за оплату коммунальных услуг',evidence)
        quote=answer.split('Цитата из документа:')[1]
        self.assertIn('коммунальных услуг 0 теңге',quote)
        self.assertNotIn('История тарифов',quote)

    def test_bank_isolation_and_missing_bank(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            entries=[]
            for name,domain,bank,text in [('law','legal','','Покупатель обнаружил недостаток товара.'),
                    ('h','finance','halyk','Оплата коммунальных услуг 0 теңге.'),
                    ('f','finance','freedom','Оплата коммунальных услуг 100 теңге.')]:
                (root/(name+'.txt')).write_text(text)
                entries.append({'id':name,'domain':domain,'bank':bank,'title':name,
                    'file':name+'.txt','source':'official','reviewed_on':'2026-10-01'})
            (root/'manifest.json').write_text(json.dumps({'documents':entries}))
            store=KnowledgeStore(root)
            self.assertEqual(store.search('finance','коммунальные услуги'),[])
            result=store.search('finance','Halyk коммунальные услуги')
            self.assertTrue(result)
            self.assertTrue(all(p['bank']=='halyk' for p in result))
            self.assertEqual(store.search('finance','Halyk ксилофон'),[])

if __name__=='__main__':unittest.main()
