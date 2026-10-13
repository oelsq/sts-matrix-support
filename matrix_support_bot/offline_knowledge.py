"""Read reviewed local text and PDF documents for the isolated Matrix bot."""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
import re
from pathlib import Path
from urllib.parse import urlparse

from pypdf import PdfReader

DOMAINS = {"legal", "finance"}
TOKEN = re.compile(r"[\w-]{3,}", re.UNICODE)
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 500
STOPWORDS = {
    "как", "что", "это", "для", "при", "или", "если", "мне", "меня",
    "мой", "моя", "моего", "вас", "ваша", "ваши", "есть", "нет",
    "the", "and", "with", "from",
    "какие", "какой", "какая", "какое", "когда", "делать", "можно", "нужно",
    "хочу", "такое", "помоги", "пожалуйста", "вопрос", "ответ", "предыдущий",
    "сравнить", "могу", "чтобы", "сейчас", "дальше", "подскажите", "подскажи",
}


class KnowledgeError(ValueError):
    pass


def validate_local_endpoint(endpoint: str, allowed_hosts: str, label: str) -> str:
    parsed = urlparse(endpoint)
    allowed = {item.strip() for item in allowed_hosts.split(",") if item.strip()}
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise KnowledgeError(f"Offline {label} endpoint must be an HTTP URL")
    if parsed.hostname not in allowed:
        raise KnowledgeError(f"Offline {label} host is not allowlisted")
    return parsed.hostname


def validate_llm_endpoint(endpoint: str, allowed_hosts: str) -> str:
    return validate_local_endpoint(endpoint, allowed_hosts, "LLM")


def _terms(text: str) -> set[str]:
    words = (word.casefold().replace("ё", "е") for word in TOKEN.findall(text))
    return {"счет" if word.startswith("счет") else word[:5] if len(word) >= 6 else word
            for word in words if word not in STOPWORDS}


BANK_NAMES = {"halyk": ("halyk", "халык"), "freedom": ("freedom", "фридом"), "kaspi": ("kaspi", "каспи")}
TARIFF_QUESTION = re.compile(r"комисси|тариф|сколько стоит|стоимость|обслуживан|ставк.*(?:вклад|депозит)|(?:вклад|депозит).*ставк", re.I)


def is_tariff_question(question: str) -> bool:
    if re.search(r"(?:как (?:понять|сравнить)|что (?:такое|значит)).*(?:стоимост.*кредит|ГЭСВ|эффективн.*ставк)", question, re.I):
        return False
    rights = re.search(r"незакон|правомер|оспор|закон|обязан|жалоб|имеет.*право", question, re.I)
    return bool(TARIFF_QUESTION.search(question)) and not rights


def needs_bank(question: str) -> bool:
    """Only product prices need a bank; general statutory rights do not."""
    return is_tariff_question(question) and not any(
        name in question.casefold() for names in BANK_NAMES.values() for name in names
    )


def _pdf_pages(path: Path, doc_id: str, expected_hash: object) -> list[tuple[int, str]]:
    if not isinstance(expected_hash, str) or not SHA256.fullmatch(expected_hash):
        raise KnowledgeError(f"PDF {doc_id} requires a lowercase SHA-256 in manifest")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise KnowledgeError(f"Cannot read document: {doc_id}") from exc
    if len(data) > MAX_PDF_BYTES:
        raise KnowledgeError(f"PDF is too large: {doc_id}")
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise KnowledgeError(f"PDF SHA-256 mismatch: {doc_id}")
    try:
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise KnowledgeError(f"Encrypted PDF is not supported: {doc_id}")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise KnowledgeError(f"PDF has too many pages: {doc_id}")
        pages = [(number, page.extract_text(extraction_mode="layout") or "")
                 for number, page in enumerate(reader.pages, start=1)]
    except KnowledgeError:
        raise
    except Exception as exc:
        raise KnowledgeError(f"Cannot extract text from PDF: {doc_id}") from exc
    if not any(body.strip() for _, body in pages):
        raise KnowledgeError(
            f"PDF has no extractable text (OCR is required): {doc_id}"
        )
    return pages


class KnowledgeStore:
    def __init__(self, directory: str | Path):
        self.root = Path(directory).resolve()
        manifest = self.root / "manifest.json"
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8"))["documents"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise KnowledgeError(f"Invalid knowledge manifest: {manifest}") from exc
        if not isinstance(entries, list):
            raise KnowledgeError("documents must be a list")
        self.passages: list[dict[str, str | int]] = []
        self.document_search_terms = {}
        seen_ids: set[str] = set()
        present_domains: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise KnowledgeError("Each document must be an object")
            try:
                doc_id = entry["id"]
                domain = entry["domain"]
                title = entry["title"]
                reviewed_on = entry["reviewed_on"]
                source = entry["source"]
                relative = Path(entry["file"])
            except (KeyError, TypeError) as exc:
                raise KnowledgeError("Document metadata is incomplete") from exc
            if not all(isinstance(v, str) and v.strip() for v in
                       (doc_id, domain, title, reviewed_on, source)):
                raise KnowledgeError("Document metadata must contain nonempty strings")
            if domain not in DOMAINS or doc_id in seen_ids:
                raise KnowledgeError(f"Invalid domain or duplicate document id: {doc_id}")
            if relative.suffix.lower() not in {".txt", ".pdf"} or relative.is_absolute():
                raise KnowledgeError(f"Expected a relative .txt or .pdf file for {doc_id}")
            path = (self.root / relative).resolve()
            if not path.is_relative_to(self.root):
                raise KnowledgeError(f"Document path escapes knowledge directory: {doc_id}")

            if relative.suffix.lower() == ".pdf":
                pages = _pdf_pages(path, doc_id, entry.get("sha256"))
            else:
                try:
                    body = path.read_text(encoding="utf-8")
                except OSError as exc:
                    raise KnowledgeError(f"Cannot read document: {doc_id}") from exc
                if entry.get("artifact_kind") == "official_text_snapshot" and not entry.get("sha256"):
                    raise KnowledgeError(f"Official text requires SHA-256: {doc_id}")
                expected = entry.get("sha256")
                if expected is not None and (not isinstance(expected,str) or not SHA256.fullmatch(expected)
                                             or hashlib.sha256(body.encode("utf-8")).hexdigest() != expected):
                    raise KnowledgeError(f"Text SHA-256 mismatch: {doc_id}")
                if not body.strip():
                    raise KnowledgeError(f"Empty document: {doc_id}")
                pages = [(0, body)]

            if "утрати" in str(entry.get("status", "")).casefold():
                raise KnowledgeError(f"Repealed document cannot be used as current law: {doc_id}")
            search_terms = entry.get("search_terms", [])
            if not isinstance(search_terms,list) or any(not isinstance(t,str) or len(t)>120 for t in search_terms):
                raise KnowledgeError(f"Invalid document search terms: {doc_id}")
            self.document_search_terms[doc_id] = search_terms
            seen_ids.add(doc_id)
            present_domains.add(domain)
            also_domains = entry.get("also_domains", [])
            if not isinstance(also_domains, list) or any(d not in DOMAINS for d in also_domains):
                raise KnowledgeError(f"Invalid additional domains: {doc_id}")
            if (not isinstance(entry.get("excluded_sections", []), list)
                    or any(not isinstance(section, str) for section in entry.get("excluded_sections", []))):
                raise KnowledgeError(f"Invalid excluded sections: {doc_id}")
            applicable_domains = sorted({domain, *also_domains})
            present_domains.update(applicable_domains)
            excluded_clauses = entry.get("excluded_clauses", {})
            if not isinstance(excluded_clauses, dict) or any(
                not isinstance(k, str) or not isinstance(v, list) or any(not isinstance(n, str) for n in v)
                for k, v in excluded_clauses.items()
            ):
                raise KnowledgeError(f"Invalid excluded clauses: {doc_id}")
            section = ""
            clause = ""
            for page_number, body in pages:
                # Keep table columns/line breaks and never cut a word mid-token.
                lines = [line.rstrip() for line in body.splitlines() if line.strip()
                         and not line.startswith(("PDF-снимок «Әділет» |", "Локальная копия |"))]
                chunks = []
                current = []
                size = 0
                chunk_section = section
                chunk_clause = clause
                for line in lines:
                    resolution_point = (re.match(r"^\s*(\d+(?:-\d+)?)\.\s",line)
                                        if entry.get("document_kind") == "supreme_court_resolution" else None)
                    if re.match(r"^\s*(Статья |Глава )", line) or resolution_point:
                        if current:
                            chunks.append((chunk_section, chunk_clause, "\n".join(current)))
                            current, size = [], 0
                        section = "Пункт " + resolution_point[1] + "." if resolution_point else line.strip()
                        clause = ""
                        chunk_section = section
                        chunk_clause = clause
                    article_number = re.match(r"^Статья ([\d-]+)", section)
                    restricted = article_number and article_number.group(1) in excluded_clauses
                    point = re.match(r"^\s*(\d+)\.\s", line) if restricted else None
                    if point and point.group(1) != clause:
                        if current:
                            chunks.append((chunk_section, chunk_clause, "\n".join(current)))
                            current, size = [], 0
                        clause = point.group(1)
                        chunk_clause = clause
                    if size + len(line) > 1800 and current:
                        chunks.append((chunk_section, chunk_clause, "\n".join(current)))
                        current = current[-2:]
                        size = sum(len(item) for item in current)
                        chunk_section = section
                    current.append(line)
                    size += len(line) + 1
                if current:
                    chunks.append((chunk_section, chunk_clause, "\n".join(current)))
                for index, (heading, point_number, text) in enumerate(chunks, 1):
                    article_number = re.match(r"^Статья ([\d-]+)", heading)
                    if article_number and point_number in excluded_clauses.get(article_number.group(1), []):
                        continue
                    passage = {
                        "id": (f"{doc_id}:p{page_number}:{index}:1"
                               if page_number else f"{doc_id}:{index}:1"),
                        "document_id": doc_id, "domain": domain, "title": title,
                        "domains": applicable_domains,
                        "artifact_kind": str(entry.get("artifact_kind", "")),
                        "document_kind": str(entry.get("document_kind", "legislation")),
                        "applicability_note": str(entry.get("applicability_note", "")),
                        "excluded_sections": entry.get("excluded_sections", []),
                        "reviewed_on": reviewed_on, "source": source,
                        "text": text, "section": heading,
                        "bank": str(entry.get("bank", "")),
                        "version": str(entry.get("version_date", entry.get("version", ""))),
                    }
                    if page_number:
                        passage["page"] = page_number
                    self.passages.append(passage)
        if present_domains != DOMAINS:
            raise KnowledgeError("Offline mode requires reviewed legal and finance documents")
        self._by_id = {str(p["id"]): p for p in self.passages}
        self._document_sizes = Counter(p["document_id"] for p in self.passages)
        self._counters = {str(p["id"]): Counter(_terms(str(p["text"]) + " " + str(p.get("section", ""))))
                          for p in self.passages}
        # Build postings once. Large text corpora should not scan every passage
        # and recompute corpus statistics on every user message.
        self._postings = {}
        self._lengths = {}
        self._candidate_cache = {}
        for identifier, counter in self._counters.items():
            self._lengths[identifier] = sum(counter.values())
            for term in counter:
                self._postings.setdefault(term,set()).add(identifier)

    def search(self, domain: str, question: str, limit: int = 6) -> list[dict[str, str | int]]:
        if limit <= 0:
            return []
        question=re.sub(r'[\u2010-\u2015\u2212]','-',question)
        normalized = question
        # Colloquial queries often omit the vocabulary used in legislation.
        # These expansions influence retrieval only; they never assert facts.
        everyday = [
            (r"начальник.*(?:ден[ье]г|плат)|ден[ье]г.*за работу", "работодатель заработная плата сроки выплаты"),
            (r"сломал.*(?:телефон|товар)|магазин.*(?:обратно|принима)", "потребитель недостаток товара возврат денег"),
            (r"хозяин.*квартир|(?:аренд|квартир).*(?:депозит|удержива)", "арендодатель гарантийный взнос возврат обеспечение обязательств"),
            (r"чуж(?:ому|ой|ого) дол[ггу]|звон.*долг", "коллекторское агентство взаимодействие третьи лица должник"),
            (r"настоящ.*стоимост.*кредит|полная.*стоимост.*кредит", "ГЭСВ годовая эффективная ставка вознаграждения"),
            (r"оплачиваем.*отпуск|не разрешают.*отпуск", "ежегодный оплачиваемый трудовой отпуск работодатель"),
            (r"нечем.*плат|нет денег.*кредит|снизить.*плат[её]ж|потерял.*работ.*кредит", "реструктуризация займа изменение условий задолженности финансовые трудности"),
            (r"(?:банк|мфо).*(?:отказ|не соглас)|отказ.*реструктур", "отказ в реструктуризации банковский микрофинансовый омбудсман"),
        ]
        everyday += [
            (r'стоп[ -]*кредит|запрет.*(?:кредит|за[её]м)|отказ.*получени.*за[её]м', 'добровольный отказ банковские займы микрокредиты'),
            (r'есутд|enbek|не внес.*договор|договор.*(?:нет|отсутств|не зарегистр)', 'единая система учета трудовых договоров работодатель сведения'),
            (r'без оформлен|без договора|не оформля|не трудоустро', 'трудовой договор фактический допуск к работе трудовые отношения'),
            (r'цифров.*тенге', 'цифровой тенге инвестиционный инструмент прибыль ложные инвестиции'),
            (r'статус.*за[её]м|смс.*(?:ссылк|кредит)|уведомлен.*за[её]м', 'статус займа мошенническое сообщение проверка кредитной истории'),
        ]
        for pattern, expansion in everyday:
            if re.search(pattern, question, re.I):
                normalized += " " + expansion
        synonyms = {"зарплат": "заработная плата оплата труда сроки выплаты", "увольн": "расторжение трудового договора",
                    "отпуск": "ежегодный оплачиваемый трудовой отпуск", "реструктур": "изменение условий задолженности заемщика",
                    "мошенн": "мошенничество скомпрометированная карта", "смс": "SMS", "нацбанк": "Национальный Банк Казахстана",
                    "купил": "покупатель товар недостаток возврат", "неисправ": "недостаток товара",
                    "вернуть": "возврат", "залог": "обеспечение обязательств",
                    "укра": "кража тайное хищение имущества", "похит": "кража хищение имущества",
                    "мфо": "микрофинансовая организация микрокредит", "коллектор": "коллекторское агентство взаимодействие должник",
                    "звон": "телефонные переговоры взаимодействие", "угрож": "угрозы насилие",
                    "банкрот": "банкротство граждан внесудебное судебное восстановление платежеспособности",
                    "алимент": "содержание несовершеннолетних детей",
                    "замуж": "брачный супружеский возраст заключение брака", "женить": "брачный супружеский возраст заключение брака",
                    "развел": "расторжение брака супруги родители место жительства ребенка",
                    "развод": "расторжение брака супруги", "ребенок": "ребенок родители права детей", "ребёнок": "ребенок родители права детей",
                    "пенси": "пенсионные выплаты возраст назначения", "декрет": "беременность роды уход за ребенком социальные выплаты",
                    "поликлиник": "пациент медицинская помощь", "ндс": "налог на добавленную стоимость ставка",
                    "ипн": "индивидуальный подоходный налог", "иск": "исковое заявление суд",
                    "допрос": "подозреваемый защитник адвокат", "задержали": "задержание подозреваемого права защитник",
                    "акимат": "административный орган жалоба административный акт",
                    "арми": "призыв срочная воинская служба возраст",
                    "военкомат": "призыв срочная воинская служба",
                    "кнб": "органы национальной безопасности задачи",
                    "вклад": "депозит гарантирование", "гэсв": "годовая эффективная ставка вознаграждения"}
        for stem, expansion in synonyms.items():
            if stem in question.casefold():
                normalized += " " + expansion
        if re.search(r"\bбрак\w*", question, re.I):
            normalized += " брачный супружеский возраст заключение брака"
        terms = _terms(normalized) - _terms("Halyk халык Freedom фридом ФинАгент ЮрАгент")
        if domain not in DOMAINS or not terms:
            return []
        selected = {bank for bank, names in BANK_NAMES.items()
                    if any(name in question.casefold() for name in names)}
        if domain == "finance" and needs_bank(question):
            return []
        # Scope an explicitly named product before truncating the retrieval
        # budget, otherwise unrelated tariff tables can crowd out its rows.
        product = "halyk-superapp" if (domain == "finance" and "halyk" in selected
            and re.search(r"super\s*app|супер\s*апп", question, re.I)) else None
        key = (domain, tuple(sorted(selected)), is_tariff_question(question), product)
        if key not in self._candidate_cache:
            # Specialist labels are not access controls for reviewed public law.
            # Both rooms need labour, consumer and banking legislation. Private
            # policies and bank product documents retain their declared domains.
            candidates = [p for p in self.passages if (
                              domain in p.get("domains", [p["domain"]]) or
                              (p.get("artifact_kind") == "official_text_snapshot"
                               and p.get("document_kind") in {"legislation", "supreme_court_resolution"}
                               and not p.get("bank")))
                          and ((p.get("bank") in selected) if key[2]
                               else (not p.get("bank") or p.get("bank") in selected))
                          and (not product or p["document_id"] == product)
                          and not any(re.match(r"^Статья " + re.escape(str(section)) + r"(?:\.|\s|$)", str(p.get("section", "")))
                                      for section in p.get("excluded_sections", []))]
            identifiers = {str(p["id"]) for p in candidates}
            counts = Counter(term for identifier in identifiers for term in self._counters[identifier])
            average = sum(self._lengths[i] for i in identifiers) / len(identifiers) if identifiers else 1
            average = average or 1
            self._candidate_cache[key] = (candidates,identifiers,counts,average)
        candidates, identifiers, counts, average = self._candidate_cache[key]
        if not candidates:
            return []
        matching = set().union(*(self._postings.get(term,set()) for term in terms)) & identifiers
        scored = []
        # Topic routing only ranks existing passages; it never adds legal facts.
        topics = [
            (r"арми|военкомат|призыв|воинск", {"military-service"}),
            (r"\bкнб\b|национальн.*безопасност", {"national-security"}),
            (r"зарплат|заработн|работодател|увольн|отпуск|трудов", {"labor"}),
            (r"купил|покуп|неисправ|товар|продав|гаранти.*товар", {"consumer-law", "civil-special"}),
            (r"укра|краж|похит", {"criminal"}),
            (r"коллектор", {"collectors"}),
            (r"банкрот|платежеспособност", {"bankruptcy"}),
            (r"(?:гарант|застрах).*(?:вклад|депозит)|(?:вклад|депозит).*гарант", {"deposit-guarantee"}),
            (r"мфо|микрокредит|микрофинанс", {"microfinance", "arrears"}),
            (r"аренд|квартир|найм|нанимател", {"housing", "civil-special", "civil-general"}),
            (r"гэ[сc]в|эффективн.*ставк", {"banks", "microfinance"}),
            (r"судебн.*исполнител|исполнительн.*производств|арест.*счет", {"enforcement"}),
            (r"исков.*давност|доверенност|неустойк", {"civil-general"}),
            (r"\bбрак|замуж|женить", {"family"}),
            (r"начальник.*(?:ден[ье]г|плат)|ден[ье]г.*за работу", {"labor"}),
            (r"сломал.*телефон|магазин.*(?:обратно|принима)", {"consumer-law"}),
            (r"хозяин.*квартир", {"civil-general", "civil-special"}),
            (r"чуж(?:ому|ой|ого) дол[ггу]|звон.*долг", {"collectors"}),
            (r"настоящ.*стоимост.*кредит|полная.*стоимост.*кредит", {"banks"}),
            (r"нечем.*плат|нет денег.*кредит|снизить.*плат[её]ж|реструктур", {"regulator-restructure", "arrears", "microfinance"}),
            (r"(?:банк|мфо).*(?:отказ|не соглас)|отказ.*реструктур", {"regulator-refusal"}),
            (r"(?:претензи|жалоб).*(?:магазин|продав|товар)|(?:магазин|продав|товар).*претензи", {"consumer-claim-guide"}),
            (r"(?:куда|инспекц|жалоб).*(?:зарплат|начальник|работодател)|(?:зарплат|начальник|работодател).*(?:куда|инспекц|жалоб)", {"labor-wage-guide"}),
        ]
        topics += [
            (r'стоп[ -]*кредит|запрет.*(?:кредит|за[её]м)|отказ.*получени.*за[её]м', {'stop-credit-guide'}),
            (r'есутд|enbek|не внес.*договор|договор.*(?:нет|отсутств|не зарегистр)', {'labor-esutd-guide'}),
            (r'без оформлен|без договора|не оформля|не трудоустро', {'labor'}),
            (r'цифров.*тенге', {'nb-digital-tenge-fraud'}),
            (r'статус.*за[её]м|смс.*(?:ссылк|кредит)|уведомлен.*за[её]м', {'nb-phishing-loan'}),
            (r'безопасн.*сч[её]т|заблокир.*карт|блокировк.*карт', {'nb-card-safe-account'}),
        ]
        preferred = set().union(*(docs for pattern, docs in topics if re.search(pattern, question, re.I)))
        preferred.update(doc for doc, words in self.document_search_terms.items()
                         if any(word.casefold() in question.casefold() for word in words))
        for identifier in matching:
            passage, counter = self._by_id[identifier], self._counters[identifier]
            score = 0.0
            for term in terms & counter.keys():
                idf = math.log(1 + (len(candidates) - counts[term] + 0.5) / (counts[term] + 0.5))
                frequency = counter[term]
                score += idf * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * self._lengths[identifier] / average))
            if score and len(str(passage["text"]).strip()) > 10:
                score += 3 * len(terms & counter.keys())
                score += 5 * len(terms & _terms(str(passage.get("section", ""))))
                if str(passage["text"]).lstrip().startswith(("Сноска.", "Примечание")):
                    score -= 500
                if len(str(passage["text"]).strip()) < 120 and self._document_sizes[passage["document_id"]] > 1:
                    score -= 500
                if passage["document_id"] in preferred:
                    score += 100
                section = str(passage.get("section", ""))
                guide_intents = [
                    ('consumer-claim-guide', r'(?:состав|напиш|напис|подготов).*претензи.*(?:продав|магазин|товар)|претензи.*(?:продав|магазин|товар)'),
                    ('labor-wage-guide', r'(?:куда|жалоб|жалова|инспекц).*(?:зарплат|работодател|начальник)|(?:зарплат|работодател|начальник).*(?:куда|жалоб|жалова|инспекц)'),
                ]
                if any(passage['document_id'] == doc and re.search(pattern, question, re.I) for doc, pattern in guide_intents):
                    score += 250
                if (product == "halyk-superapp" and "коммунальн" in question.casefold()
                        and "оплата коммунальных услуг" in " ".join(str(passage["text"]).casefold().split())):
                    score += 100
                if (re.search(r"зарплат|заработн|начальник.*(?:ден[ье]г|плат)|ден[ье]г.*за работу", question, re.I) and passage["document_id"] == "labor"
                        and section.startswith("Статья 113.")):
                    score += 100
                if re.search(r"укра|краж|похит", question, re.I) and passage["document_id"] == "criminal" and section.startswith("Статья 188."):
                    score += 100
                if passage['document_id']=='labor' and section.startswith('Статья 33.') and re.search(r'без оформлен|без договора|не оформля|не трудоустро',question,re.I):
                    score+=250
                focused_sections = [
                    (r"потер[еи].*работ|безработ", "social", ("Статья 113.", "Статья 114.", "Статья 118.")),
                    (r"ндс|добавленн.*стоимост", "tax", ("Статья 503.",)),
                    (r"пациент", "health", ("Статья 134.",)),
                    (r"алимент", "family", ("Статья 138.", "Статья 139.")),
                    (r"(?:лет|возраст).*(?:брак|замуж|женить)|(?:брак|замуж|женить).*(?:лет|возраст)", "family", ("Статья 10.",)),
                    (r"реб[её]нок|дет[ьи]|развел|развод", "family", ("Статья 73.",)),
                    (r"подать.*иск|исковое заявлен", "civil-procedure", ("Статья 148.", "Статья 149.")),
                    (r"задержали|задержан|допрос", "criminal-procedure", ("Статья 64.", "Статья 67.")),
                    (r"арми|военкомат|призыв", "military-service", ("Статья 31.",)),
                    (r"\bкнб\b|национальн.*безопасност", "national-security", ("Статья 1.", "Статья 2.")),
                    (r"купил|покуп|неисправ|товар|продав|сломал.*телефон|магазин.*обратно", "consumer-law", ("Статья 15.", "Статья 30.")),
                    (r"коллектор|чуж(?:ому|ой|ого) дол[ггу]|звон.*долг", "collectors", ("Статья 5.",)),
                    (r"банкрот|платежеспособност", "bankruptcy", ("Статья 5.", "Статья 6.")),
                    (r"аренд|квартир|хозяин.*квартир", "civil-general", ("Статья 338-3.", "Статья 338-4.")),
                    (r"гэ[сc]в|эффективн.*ставк|настоящ.*стоимост.*кредит|полная.*стоимост.*кредит", "banks", ("Статья 57.",)),
                ]
                for pattern, doc, headings in focused_sections:
                    if passage["document_id"] == doc and section.startswith(headings) and re.search(pattern, question, re.I):
                        score += 100
                if ("юриди" in terms and "помощ" in terms and ("прав" in question.casefold() or "кто" in question.casefold())
                        and passage["document_id"] == "legal-aid" and str(passage["section"]).startswith("Статья 26.")):
                    score += 2000
                if ("омбуд" in question.casefold() and passage["document_id"] == "banks"
                        and str(passage["section"]).startswith("Статья 135.")
                        and "статьи 65" in " ".join(str(passage["text"]).split()) and "2027" in str(passage["text"])):
                    score += 1000
                if score > 0:
                    scored.append((score, passage))
        scored.sort(key=lambda item: (-item[0], str(item[1]["id"])))
        # Prevent one long article from using the entire evidence budget. Keep
        # three pieces per section, then fill with further pieces if necessary.
        chosen, deferred, per_section = [], [], Counter()
        for _, passage in scored:
            key = (passage["document_id"], passage.get("section") or passage.get("page"))
            if per_section[key] >= 3:
                deferred.append(passage)
                continue
            chosen.append(passage); per_section[key] += 1
            if len(chosen) >= limit:
                break
        if len(chosen) < limit:
            chosen.extend(deferred[:limit - len(chosen)])
        # Retrieve definitions and exceptions even when their wording differs
        # from the user's everyday question. They prevent overbroad conclusions.
        supplements = []
        leading_docs = {str(p["document_id"]) for p in chosen[:2]}
        for doc_id in leading_docs:
            rules = [p for p in candidates if p["document_id"] == doc_id
                     and re.search(r"^Статья.*(?:введения в действие|введение в действие)",str(p.get("section","")),re.I)
                     and str(p["text"]).lstrip().startswith("Статья")]
            if rules:
                # Keep relevant provisions ahead of the temporal supplement.
                for rule in rules[:1]:
                    if rule not in chosen:
                        chosen.insert(min(5,len(chosen)),rule)
        if re.search(r"купил|покуп|продав|неисправ|товар|сломал.*телефон|магазин.*обратно", question, re.I) and not selected:
            supplements += [p for p in candidates if p["document_id"] == "consumer-law"
                            and "относятся исключительно транспортные средства" in " ".join(str(p["text"]).split())]
        if re.search(r"банкрот", question, re.I):
            supplements += [p for p in candidates if p["document_id"] == "bankruptcy"
                            and str(p["section"]).startswith("Статья 5.")
                            and any(s in str(p["text"]) for s in ("1600-кратного", "3) кредитором", "2. Несмотря"))]
        if re.search(r"с кем.*реб|мест.*жительств.*реб|мнени.*реб|реб.*мнени", question, re.I):
            supplements += [p for p in candidates if p["document_id"] == "family"
                            and str(p["section"]).startswith("Статья 62.")]
        if supplements:
            existing = {str(p["id"]) for p in supplements}
            chosen = supplements + [p for p in chosen if str(p["id"]) not in existing]
        return chosen[:limit]


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python offline_knowledge.py KNOWLEDGE_DIR")
    store = KnowledgeStore(sys.argv[1])
    print(f"Validated {len(store.passages)} local passages")
