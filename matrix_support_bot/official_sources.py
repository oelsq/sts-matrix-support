"""Explicit, staged imports of full legislative texts from official Adilet.

No network is used by the answering bot. Downloading does not certify current
applicability: an operator checks status and commencement notes before promotion.
"""
from __future__ import annotations
from datetime import date
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import hashlib
import json
import re
import urllib.request

MAX_SOURCE_BYTES = 24 * 1024 * 1024
CODE = re.compile(r'[A-Z][0-9A-Z_]{5,30}\Z')
IDENTIFIER = re.compile(r'[a-z][a-z0-9-]{1,79}\Z')

class SourceError(ValueError):
    pass

class OfficialTextParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in {'script','style'}: self.skip += 1
        if tag in {'p','h1','h2','h3','h4','h5','h6','br','tr','li'}: self.parts.append('\n')
        if tag == 'td': self.parts.append(' | ')
    def handle_endtag(self, tag):
        if tag in {'script','style'}: self.skip = max(0, self.skip - 1)
        if tag in {'p','h1','h2','h3','h4','h5','h6','tr','li'}: self.parts.append('\n')
    def handle_data(self, value):
        if not self.skip: self.parts.append(value)
    def text(self):
        return '\n'.join(re.sub(r'\s+',' ',line).strip() for line in ''.join(self.parts).splitlines() if line.strip())

def parse_official_page(html: str, expected_title: str = '', document_kind: str = 'legislation') -> dict:
    title_match = re.search(r'<h1\b[^>]*>(.*?)</h1>', html, re.S | re.I)
    article = re.search(r'<article\b[^>]*>(.*?)</article>', html, re.S | re.I)
    if not title_match or not article: raise SourceError('Official title/full-text article not found')
    title = unescape(re.sub(r'<[^>]+>','',title_match[1])).strip()
    if expected_title and not re.search(expected_title, title, re.I):
        raise SourceError('Unexpected document title: ' + title)
    # Status is read from the header, never from mentions of repealed acts in a statute.
    header = re.sub(r'<[^>]+>',' ',html[title_match.end():article.start()])
    header = unescape(re.sub(r'\s+',' ',header))
    status_match = re.search(r'Утративший силу|Утратил силу|Обновленный|Обновлённый|Новый|Исправление ошибки', header, re.I)
    if not status_match: raise SourceError('Official document status not found')
    status = status_match[0]
    if 'утрати' in status.casefold(): raise SourceError('Repealed document cannot be activated')
    parser = OfficialTextParser(); parser.feed(article[1]); text = parser.text()
    if document_kind == 'regulatory_rules':
        complete = 'Постановление Правления' in header and len(re.findall(r'^\d+\.\s',text,re.M)) >= 5
    elif document_kind == 'supreme_court_resolution':
        complete = 'Нормативное постановление Верховного' in header and len(re.findall(r'^\d+[.-]?\d*\.\s',text,re.M)) >= 3
    else:
        complete = len(re.findall(r'^Статья\s+\d',text,re.M)) >= 2
    if len(text) < 2000 or not complete:
        raise SourceError('Full legislative text appears incomplete')
    future_notes = [line for line in text.splitlines() if re.search(r'202[7-9]|203\d',line) and re.search(r'действ|предусмат|приостанов',line,re.I)]
    return {'title':title,'status':status,'text':text,'future_note_count':len(future_notes)}

def fetch_one(spec: dict, destination: Path, fetched_on: str | None = None) -> dict:
    ident, code = spec['id'],spec['code']
    if not IDENTIFIER.fullmatch(ident) or not CODE.fullmatch(code): raise SourceError('Invalid document id/code')
    if spec.get('domain') not in {'legal','finance'}: raise SourceError('Invalid domain')
    url = 'https://old.adilet.zan.kz/rus/docs/' + code
    request = urllib.request.Request(url,headers={'User-Agent':'STS local legal corpus/1.0'})
    with urllib.request.urlopen(request, timeout=90) as response:
        if response.geturl().split('/')[2] not in {'old.adilet.zan.kz','adilet.zan.kz','www.adilet.zan.kz'}:
            raise SourceError('Unexpected redirect outside official source')
        data = response.read(MAX_SOURCE_BYTES+1)
    if len(data)>MAX_SOURCE_BYTES: raise SourceError('Official page exceeds size limit')
    parsed = parse_official_page(data.decode('utf-8'),spec.get('expected_title',''),spec.get('document_kind','legislation'))
    fetched_on = fetched_on or date.today().isoformat()
    # A source snapshot preserves footnotes verbatim; it is not an edited legal opinion.
    body = parsed.pop('text').encode('utf-8')
    destination.mkdir(parents=True,exist_ok=True)
    (destination/(ident+'.txt')).write_bytes(body)
    item = {**spec, **parsed, 'file':'documents/'+ident+'.txt','source':url,
            'retrieved_on':fetched_on,'reviewed_on':fetched_on,
            'version':'Официальный текст получен '+fetched_on+'; точная дата редакции отдельно не установлена',
            'artifact_kind':'official_text_snapshot','sha256':hashlib.sha256(body).hexdigest(),
            'source_html_sha256':hashlib.sha256(data).hexdigest(),
            'applicability_note':'Снимок официального текста на '+fetched_on+'. Дата получения не является датой редакции или юридической экспертизой. '
             'Проверяйте статью о введении в действие и сноски; будущие изменения, приостановленные положения и исключения нельзя применять автоматически. '
             'Период события важен: современная редакция не доказывает правила для прошлых лет.'}
    if spec['domain']=='finance': item['also_domains']=['legal']
    elif ident in {'social','entrepreneur','public-procurement','limited-liability'}: item['also_domains']=['finance']
    item.pop('expected_title',None)
    (destination/(ident+'.metadata.json')).write_text(json.dumps(item,ensure_ascii=False,indent=2)+'\n')
    return item


def main():
    import argparse,time
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',required=True,type=Path)
    parser.add_argument('--staging',required=True,type=Path)
    args=parser.parse_args(); specs=json.loads(args.catalog.read_text());args.staging.mkdir(parents=True,exist_ok=True)
    report={'retrieved_on':date.today().isoformat(),'automatic_activation':False,'documents':[],'errors':[]}
    for spec in specs:
        try:
            item=fetch_one(spec,args.staging);report['documents'].append(item)
            print(item['id'],item['title'],item['status'],flush=True)
        except Exception as exc:
            report['errors'].append({'id':spec.get('id'),'error':str(exc)})
            print('FAILED',spec.get('id'),str(exc),flush=True)
        (args.staging/'import-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        time.sleep(.2)
    if report['errors']: raise SystemExit(1)

if __name__=='__main__': main()
