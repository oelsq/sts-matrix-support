import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents import messages, route, route_for_room, source_cards, render


class DispatchTests(unittest.TestCase):
    def test_explicit_legal_request(self):
        self.assertEqual(route("Спроси ЮрАгента о правах потребителя"), ("legal",))

    def test_bank_credit_request(self):
        self.assertEqual(route("Какие шаги по кредиту Halyk Bank?"), ("finance",))

    def test_mixed_contract_and_credit_request(self):
        self.assertEqual(route("Кредитный договор нарушен, как подать претензию в банк?"), ("legal", "finance"))

    def test_dedicated_finance_room_ignores_legal_keywords(self):
        rooms = {"!finance:demo.local": "finance", "!legal:demo.local": "legal"}
        self.assertEqual(
            route_for_room("!finance:demo.local", "Что говорит закон о кредите?",
                           (), rooms), ("finance",)
        )
        self.assertEqual(
            route_for_room("!legal:demo.local", "Какой тариф у банка?",
                           (), rooms), ("legal",)
        )
        self.assertEqual(
            route_for_room("!other:demo.local", "Кредитный договор", (), rooms),
            ("legal", "finance"),
        )

    def test_short_followup_stays_with_specialist(self):
        self.assertEqual(route("А если завтра?", ("legal",)), ("legal",))

    def test_it_support_remains_general(self):
        self.assertEqual(route("Не работает Wi-Fi на ноутбуке"), ("general",))

    def test_rejects_invented_legal_deadline_and_article(self):
        answer = render("legal", "По ст. 30 банк обязан ответить за 10 дней.", "Претензия банку")
        self.assertNotIn("ст. 30", answer)
        self.assertNotIn("10 дней", answer)
        self.assertIn("не хватает текста", answer)

    def test_rejects_unverified_bank_rate(self):
        answer = render("finance", "Ставка Halyk составляет 15%.", "Ставка кредита Halyk")
        self.assertNotIn("15%", answer)
        self.assertIn("не подтверждены", answer)

    def test_specialist_prompt_contains_distinct_official_sources(self):
        legal = json.dumps(messages("legal", "Права потребителя"), ensure_ascii=False)
        finance = json.dumps(messages("finance", "Кредит"), ensure_ascii=False)
        self.assertIn("adilet.zan.kz", legal)
        self.assertNotIn("support.bankffin.kz", legal)
        self.assertIn("support.bankffin.kz", finance)
        self.assertIn("halykbank.kz", finance)
        self.assertTrue(all(c["url"].startswith("https://") for c in source_cards("finance")))


if __name__ == "__main__":
    unittest.main()
