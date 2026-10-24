"""Technical bank-PDF reconciliation; does not approve legal interpretations."""
from pathlib import Path
from urllib.parse import urlsplit,unquote,urljoin
from html.parser import HTMLParser
from datetime import date
import hashlib,json,os,tempfile,re
from pypdf import PdfReader
from knowledge_updates import validate_url
from evaluation_core import digest

class PDFLinks(HTMLParser):
    def __init__(self,base):super().__init__();self.base=base;self.urls=set()
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag=='a' and '.pdf' in values.get('href','').casefold():self.urls.add(unquote(urljoin(self.base,values['href'])))


def reconcile_tariff(root,document_id,pdf,source_url,catalog_html,*,verified_on,version,applicability_note):
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,79}',document_id):raise ValueError('Unsafe tariff document ID')
    root=Path(root);manifest=json.loads((root/'manifest.json').read_text());entry=next(d for d in manifest['documents'] if d['id']==document_id)
    if not entry.get('bank') or entry.get('document_kind') not in {None,'bank_tariff'}:raise ValueError('This workflow is restricted to declared bank tariffs')
    if date.fromisoformat(verified_on)>date.today():raise ValueError('Future verification date')
    validate_url(source_url);validate_url(entry['catalog_url'])
    if urlsplit(source_url).hostname!=urlsplit(entry['catalog_url']).hostname:raise ValueError('Catalog and PDF bank differ')
    body=Path(pdf).read_bytes();catalog=Path(catalog_html).read_bytes();links=PDFLinks(entry['catalog_url']);links.feed(catalog.decode('utf-8'))
    if unquote(source_url) not in links.urls:raise ValueError('PDF is not linked by the official catalog')
    if not body.startswith(b'%PDF-') or len(body)>50*1024*1024:raise ValueError('Invalid PDF')
    reader=PdfReader(pdf,strict=True)
    if reader.is_encrypted or not 1<=len(reader.pages)<=100:raise ValueError('Unsupported tariff PDF')
    text=' '.join((page.extract_text() or '') for page in reader.pages)
    if len(text)<500 or not any(v in text.casefold() for v in ['тариф','комиссионного вознаграждения']):raise ValueError('Tariff content could not be verified')
    sha=hashlib.sha256(body).hexdigest();before=digest(entry);previous_sha=entry['sha256']
    target='documents/'+document_id+'-'+sha[:16]+'.pdf'
    if sha==entry['sha256']:target=entry['file']
    path=root/target
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('Existing snapshot differs')
    if not path.exists():path.write_bytes(body)
    entry.update(file=target,sha256=sha,source=source_url,reviewed_on=verified_on,version=version,applicability_note=applicability_note,
        source_verification={'kind':'official_catalog_and_pdf_technical_verification','catalog_sha256':hashlib.sha256(catalog).hexdigest(),'pdf_sha256':sha,'verified_on':verified_on,'previous_entry_sha256':before,'specialist_approval':False})
    fd,name=tempfile.mkstemp(dir=root,prefix='.tariff-manifest-')
    with os.fdopen(fd,'w') as f:json.dump(manifest,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(name,root/'manifest.json')
    return {'id':document_id,'snapshot_changed':sha!=previous_sha,'active_sha256':sha,'verification_kind':entry['source_verification']['kind'],'specialist_approval':False,'previous_entry_sha256':before}
