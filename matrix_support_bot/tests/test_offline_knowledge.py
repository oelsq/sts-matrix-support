import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents import messages, render
from offline_knowledge import KnowledgeError, KnowledgeStore, validate_llm_endpoint, validate_local_endpoint


def write_test_pdf(path: Path, text: str) -> None:
    """Build a tiny text PDF without adding a fixture-generation dependency."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(output)
    output += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    for offset in offsets[1:]:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
               f"startxref\n{xref_offset}\n%%EOF\n").encode()
    path.write_bytes(output)


class OfflineKnowledgeTests(unittest.TestCase):
    def make_store(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "documents").mkdir()
        (root / "documents" / "legal.txt").write_text(
            "Покупатель обнаружил недостаток товара.\n\n"
            "Обращение продавцу следует описать письменно.", encoding="utf-8")
        (root / "documents" / "finance.txt").write_text(
            "График погашения кредита указан в разделе Мой банк.", encoding="utf-8")
        documents = [
            {"id": "law-1", "domain": "legal", "title": "Права потребителя",
             "reviewed_on": "2026-09-28", "source": "официальный документ",
             "file": "documents/legal.txt"},
            {"id": "bank-1", "domain": "finance", "title": "Справка банка",
             "reviewed_on": "2026-09-28", "source": "официальная справка",
             "file": "documents/finance.txt"},
        ]
        (root / "manifest.json").write_text(
            json.dumps({"documents": documents}, ensure_ascii=False), encoding="utf-8")
        return temp, root, documents

    def test_separate_domains_and_no_match(self):
        temp, root, _ = self.make_store()
        try:
            store = KnowledgeStore(root)
            legal = store.search("legal", "недостаток товара")
            finance = store.search("finance", "график погашения кредита")
            self.assertTrue(legal)
            self.assertTrue(finance)
            self.assertTrue(all(p["domain"] == "legal" for p in legal))
            self.assertTrue(all(p["domain"] == "finance" for p in finance))
            self.assertEqual(store.search("legal", "ксилофон"), [])
            self.assertTrue(store.search("finance", "кредит"))
        finally:
            temp.cleanup()

    def test_prompt_uses_local_passages_and_citations(self):
        temp, root, _ = self.make_store()
        try:
            passages = KnowledgeStore(root).search("finance", "график погашения")
            prompt = json.dumps(messages("finance", "график погашения", passages),
                                ensure_ascii=False)
            self.assertIn("График погашения", prompt)
            self.assertNotIn("support.bankffin.kz", prompt)
            answer = render("finance", f"Смотрите раздел Мой банк. [{passages[0]['id']}]",
                            "график погашения", passages)
            self.assertIn("[bank-1:", answer)
            self.assertIn("Локальные материалы", answer)
            uncited = render("finance", "Смотрите раздел Мой банк.",
                             "график погашения", passages)
            self.assertIn("Не удалось подготовить ответ", uncited)
        finally:
            temp.cleanup()

    def test_missing_domain_fails_closed(self):
        temp, root, documents = self.make_store()
        try:
            (root / "manifest.json").write_text(
                json.dumps({"documents": documents[:1]}), encoding="utf-8")
            with self.assertRaises(KnowledgeError):
                KnowledgeStore(root)
        finally:
            temp.cleanup()

    def test_path_escape_is_rejected(self):
        temp, root, documents = self.make_store()
        try:
            documents[0]["file"] = "../outside.txt"
            (root / "manifest.json").write_text(
                json.dumps({"documents": documents}), encoding="utf-8")
            with self.assertRaises(KnowledgeError):
                KnowledgeStore(root)
        finally:
            temp.cleanup()

    def test_offline_endpoint_allowlist(self):
        self.assertEqual(validate_llm_endpoint("http://local-llm:11434/v1",
                                               "local-llm"), "local-llm")
        with self.assertRaises(KnowledgeError):
            validate_llm_endpoint("https://llm.nitec.kz/v1", "local-llm")
        self.assertEqual(validate_local_endpoint("http://homeserver:8008",
                                                 "homeserver", "Matrix"), "homeserver")
        with self.assertRaises(KnowledgeError):
            validate_local_endpoint("https://outside.example", "homeserver", "Matrix")


    def test_pdf_page_search_and_rendered_citation(self):
        temp, root, documents = self.make_store()
        try:
            pdf = root / "documents" / "finance.pdf"
            write_test_pdf(pdf, "Credit schedule is available in My Bank.")
            documents[1]["file"] = "documents/finance.pdf"
            documents[1]["sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
            (root / "manifest.json").write_text(
                json.dumps({"documents": documents}), encoding="utf-8")
            passages = KnowledgeStore(root).search("finance", "credit schedule")
            self.assertTrue(passages)
            self.assertEqual(passages[0]["page"], 1)
            self.assertIn(":p1:", passages[0]["id"])
            answer = render("finance", f"See the credit schedule. [{passages[0]['id']}]",
                            "credit schedule", passages)
            self.assertIn("стр. 1", answer)
            self.assertIn(passages[0]["id"], answer)
        finally:
            temp.cleanup()

    def test_pdf_hash_mismatch_and_image_only_are_rejected(self):
        temp, root, documents = self.make_store()
        try:
            pdf = root / "documents" / "finance.pdf"
            write_test_pdf(pdf, "Credit schedule")
            documents[1]["file"] = "documents/finance.pdf"
            documents[1]["sha256"] = "0" * 64
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"documents": documents}), encoding="utf-8")
            with self.assertRaisesRegex(KnowledgeError, "SHA-256 mismatch"):
                KnowledgeStore(root)
            write_test_pdf(pdf, "")
            documents[1]["sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
            manifest.write_text(json.dumps({"documents": documents}), encoding="utf-8")
            with self.assertRaisesRegex(KnowledgeError, "OCR is required"):
                KnowledgeStore(root)
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()


def test_only_reviewed_public_legislation_is_shared_across_specialists(tmp_path):
    entries=[]
    for identifier, domain, artifact, kind, bank in [
        ('labor','legal','official_text_snapshot','legislation',''),
        ('court','legal','official_text_snapshot','supreme_court_resolution',''),
        ('private','legal','','legislation',''),
        ('bank','legal','official_text_snapshot','bank_tariff','halyk'),
        ('finance','finance','','legislation','')]:
        body='Работодатель выплачивает заработную плату. '+identifier
        filename=identifier+'.txt'
        (tmp_path/filename).write_text(body)
        entries.append({'id':identifier,'domain':domain,'file':filename,'title':identifier,
                        'source':'https://example.org/'+identifier,'reviewed_on':'2026-10-12',
                        'artifact_kind':artifact,'document_kind':kind,'bank':bank,
                        'sha256':hashlib.sha256(body.encode()).hexdigest()})
    (tmp_path/'manifest.json').write_text(json.dumps({'documents':entries}))
    store=KnowledgeStore(tmp_path)
    found={p['document_id'] for p in store.search('finance','Работодатель заработная плата Halyk',20)}
    assert {'labor','court','finance'} <= found
    assert not {'private','bank'} & found
