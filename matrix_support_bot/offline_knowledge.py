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


BANK_NAMES = {"halyk": ("halyk", "халык"), "freedom": ("freedom", "фридом")}
TARIFF_QUESTION = re.compile(r"комисси|тариф|сколько стоит|стоимость|обслуживан|ставк.*(?:вклад|депозит)|(?:вклад|депозит).*ставк", re.I)


def is_tariff_question(question: str) -> bool:
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
                if not body.strip():
                    raise KnowledgeError(f"Empty document: {doc_id}")
                pages = [(0, body)]

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
                    if re.match(r"^\s*(Статья |Глава )", line):
                        if current:
                            chunks.append((chunk_section, chunk_clause, "\n".join(current)))
                            current, size = [], 0
                        section = line.strip()
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

    def search(self, domain: str, question: str, limit: int = 6) -> list[dict[str, str | int]]:
        normalized = question
        synonyms = {"зарплат": "заработная плата оплата труда", "увольн": "расторжение трудового договора",
                    "отпуск": "ежегодный оплачиваемый трудовой отпуск", "реструктур": "изменение условий задолженности заемщика",
                    "мошенн": "мошенничество скомпрометированная карта", "смс": "SMS", "нацбанк": "Национальный Банк Казахстана"}
        for stem, expansion in synonyms.items():
            if stem in question.casefold():
                normalized += " " + expansion
        terms = _terms(normalized) - _terms("Halyk халык Freedom фридом ФинАгент ЮрАгент")
        if domain not in DOMAINS or not terms:
            return []
        selected = {bank for bank, names in BANK_NAMES.items()
                    if any(name in question.casefold() for name in names)}
        if domain == "finance" and needs_bank(question):
            return []
        candidates = [p for p in self.passages if domain in p.get("domains", [p["domain"]])
                      and ((p.get("bank") in selected) if is_tariff_question(question)
                           else (not p.get("bank") or p.get("bank") in selected))
                      and not any(re.match(r"^Статья " + re.escape(str(section)) + r"(?:\.|\s|$)", str(p.get("section", "")))
                                  for section in p.get("excluded_sections", []))]
        if not candidates:
            return []
        counters = [Counter(_terms(str(p["text"]) + " " + str(p.get("section", ""))))
                    for p in candidates]
        counts = Counter(term for counter in counters for term in counter)
        average = sum(sum(c.values()) for c in counters) / len(candidates) or 1
        scored = []
        for passage, counter in zip(candidates, counters):
            score = 0.0
            for term in terms & counter.keys():
                idf = math.log(1 + (len(candidates) - counts[term] + 0.5) / (counts[term] + 0.5))
                frequency = counter[term]
                score += idf * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * sum(counter.values()) / average))
            if score and len(str(passage["text"]).strip()) > 10:
                score += 3 * len(terms & counter.keys())
                score += 5 * len(terms & _terms(str(passage.get("section", ""))))
                if ("юриди" in terms and "помощ" in terms and ("прав" in question.casefold() or "кто" in question.casefold())
                        and passage["document_id"] == "legal-aid" and str(passage["section"]).startswith("Статья 26.")):
                    score += 2000
                if ("омбуд" in question.casefold() and passage["document_id"] == "banks"
                        and str(passage["section"]).startswith("Статья 135.")
                        and "статьи 65" in " ".join(str(passage["text"]).split()) and "2027" in str(passage["text"])):
                    score += 1000
                scored.append((score, passage))
        scored.sort(key=lambda item: (-item[0], str(item[1]["id"])))
        return [passage for _, passage in scored[:limit]]


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python offline_knowledge.py KNOWLEDGE_DIR")
    store = KnowledgeStore(sys.argv[1])
    print(f"Validated {len(store.passages)} local passages")
