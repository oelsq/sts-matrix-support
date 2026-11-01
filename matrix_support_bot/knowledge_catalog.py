"""Observe official tariff catalogs without inferring that HTML changes are new terms."""
import hashlib
from html.parser import HTMLParser
import os
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from knowledge_updates import request_url, validate_url


class PDFLinks(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base, self.links = base, set()

    def handle_starttag(self, tag, attrs):
        if tag != 'a' or len(self.links) >= 500:
            return
        href = dict(attrs).get('href', '')
        if not isinstance(href, str) or len(href) > 4096:
            return
        url = urljoin(self.base, href)
        if urlsplit(url).path.casefold().endswith('.pdf'):
            try:
                self.links.add(request_url(url))
            except ValueError:
                pass  # Observe only links on the same explicit official-host allowlist.


def check_catalog(item, archive, *, fetch, previous=None, cache=None):
    url = item.get('catalog_url')
    if not url:
        return {}
    cache = cache if cache is not None else {}
    previous = previous or {}
    if url not in cache:
        record = {'catalog_url': url}
        try:
            validate_url(url)
            body = fetch(url)
            sha = hashlib.sha256(body).hexdigest()
            destination = Path(archive) / (sha + '.html')
            if destination.exists():
                if hashlib.sha256(destination.read_bytes()).hexdigest() != sha:
                    raise ValueError('Catalog archive hash mismatch')
            else:
                fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(body); stream.flush(); os.fsync(stream.fileno())
            parser = PDFLinks(url); parser.feed(body.decode('utf-8'))
            record.update(catalog_sha256=sha, catalog_archive_file=str(Path('artifacts')/destination.name),
                          catalog_pdf_links=sorted(parser.links))
        except Exception as exc:
            record.update(catalog_error_type=type(exc).__name__)
        cache[url] = record
    result = dict(cache[url])
    old = previous.get('last_successful_catalog_sha256') or previous.get('catalog_sha256')
    result['last_successful_catalog_sha256'] = old
    if result.get('catalog_error_type'):
        result['catalog_status'] = 'catalog_unavailable'
    else:
        result['catalog_status'] = ('catalog_baseline_requires_review' if not old else
            'catalog_page_unchanged' if old == result['catalog_sha256'] else 'catalog_changed_review_required')
        result['previous_catalog_sha256'] = old
        result['last_successful_catalog_sha256'] = result['catalog_sha256']
        links = result['catalog_pdf_links']
        result['source_pdf_link_observed'] = request_url(item['source']) in links if links else None
        result['catalog_comparison_note'] = ('HTML changes can be navigation/widgets. Missing PDF link is not proof of repeal. '
                                             'Review product, edition, commencement and footnotes before promotion.')
    return result
