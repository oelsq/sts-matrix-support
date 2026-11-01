"""Offline freshness inventory and staged official-source change checks.

A successful HTTP request is not specialist review, legal currency or activation.
"""
from datetime import date,datetime,timezone
import hashlib,json,re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote
from urllib.request import Request,build_opener,HTTPRedirectHandler
from official_sources import parse_official_page,SourceError,MAX_SOURCE_BYTES
from evaluation_core import private_json,digest

HOSTS={'adilet.zan.kz','old.adilet.zan.kz','www.adilet.zan.kz','nationalbank.kz','www.nationalbank.kz',
       'halykbank.kz','bankffin.kz','fingramota.kz','live.fingramota.kz','www.gov.kz','gov.kz','egov.kz','www.enpf.kz','enpf.kz','stat.gov.kz'}


def validate_url(url):
    u=urlsplit(url)
    if u.scheme!='https' or u.hostname not in HOSTS or u.username or u.password or u.port not in {None,443}:raise SourceError('Unapproved official source URL')
    return url

def request_url(url):
    # Official PDF links may contain spaces/Cyrillic; preserve existing escapes.
    u = urlsplit(validate_url(url))
    return urlunsplit((u.scheme, u.netloc, quote(u.path, safe="/%:@!$&'()*+,;=-._~"),
                       quote(u.query, safe="=&%:@/?+;,-._~"), ''))


class OfficialRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,request_url(newurl))


def download(url,timeout=20):
    req=Request(request_url(url),headers={'User-Agent':'STS knowledge monitor/1.0'})
    with build_opener(OfficialRedirect()).open(req,timeout=timeout) as r:
        validate_url(r.geturl());body=r.read(MAX_SOURCE_BYTES+1)
        if len(body)>MAX_SOURCE_BYTES:raise SourceError('Source exceeds size limit')
        return body


def inventory(root,*,today=None):
    root=Path(root).resolve();today=today or date.today();rows=[]
    manifest=json.loads((root/'manifest.json').read_text())
    for d in manifest['documents']:
        path=(root/d['file']).resolve();integrity=path.is_relative_to(root) and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==d['sha256']
        days=7 if d.get('bank') else 30
        try:age=(today-date.fromisoformat(d['reviewed_on'])).days
        except (ValueError,TypeError,KeyError):age=None
        state='integrity_error' if not integrity else ('invalid_review_date' if age is None or age<0 else ('review_due' if age>days else 'within_review_interval'))
        rows.append({'id':d['id'],'source':d['source'],'integrity':integrity,'status':state,'days_since_review':age,'review_interval_days':days,
          'legal_currency_certified':False,'catalog_review_required':bool(d.get('catalog_url')),'applicability_note_present':bool(d.get('applicability_note'))})
    return {'as_of':today.isoformat(),'documents':rows,'counts':{k:sum(r['status']==k for r in rows) for k in {r['status'] for r in rows}},'automatic_activation':False}


def check_source(item,staging,*,fetch=download):
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,79}',item['id']):raise ValueError('Invalid source identifier')
    staging=Path(staging)
    if any((staging/(item['id']+suffix)).exists() for suffix in ['.pdf','.txt','.html','.check.json']):
        raise ValueError('Use a fresh staging directory; previous source checks are immutable')
    url=item['source'];row={'id':item['id'],'checked_at':datetime.now(timezone.utc).isoformat(),'url':url,
        'active_sha256':item['sha256'],'specialist_review':'not_performed','automatic_activation':False}
    try:
        body=fetch(validate_url(url))
        if urlsplit(url).hostname=='adilet.zan.kz' and not body.startswith(b'%PDF-') and b'<article' not in body.lower():
            alternate=url.replace('https://adilet.zan.kz/','https://old.adilet.zan.kz/',1)
            body=fetch(validate_url(alternate));row['fetched_url']=alternate
        html_sha=hashlib.sha256(body).hexdigest();row['download_sha256']=html_sha
        if body.startswith(b'%PDF-'):
            row['status']='snapshot_unchanged' if html_sha==item['sha256'] else 'candidate_changed'
            artifact=body;suffix='.pdf'
        else:
            text=body.decode('utf-8')
            if urlsplit(url).hostname not in {'old.adilet.zan.kz','adilet.zan.kz','www.adilet.zan.kz'}:
                # No full-text extraction contract for a generic website: HTML hashes
                # vary with navigation/widgets and cannot certify the active snapshot.
                row['status']='page_unchanged' if html_sha==item.get('source_html_sha256') else 'manual_page_comparison_required'
                artifact=body;suffix='.html'
            else:
                parsed=parse_official_page(text,document_kind=item.get('document_kind','legislation'))
                local_title=item.get('title','').casefold();remote_title=parsed['title'].casefold()
                if len(set(re.findall(r'[а-яё]{4,}',local_title))&set(re.findall(r'[а-яё]{4,}',remote_title)))<2:raise SourceError('Official title mismatch')
                artifact=parsed['text'].encode();suffix='.txt';row.update(official_status=parsed['status'],future_note_count=parsed['future_note_count'])
                row['status']='snapshot_unchanged' if hashlib.sha256(artifact).hexdigest()==item['sha256'] else ('page_unchanged' if html_sha==item.get('source_html_sha256') else 'candidate_changed')
        staging.mkdir(parents=True,exist_ok=True)
        path=staging/(item['id']+suffix)
        # Immutable approval binding uses the staged artifact hash, not HTTP timestamp.
        row['artifact_file']=path.name;row['artifact_sha256']=hashlib.sha256(artifact).hexdigest();row['active_entry_sha256']=digest(item)
        fd=__import__('os').open(path,__import__('os').O_CREAT|__import__('os').O_TRUNC|__import__('os').O_WRONLY,0o600)
        with __import__('os').fdopen(fd,'wb') as f:f.write(artifact)
        row['legal_currency_certified']=False
        row['comparison_note']='Snapshot comparison only. A full text may differ from an intentionally curated excerpt; changed bytes do not prove a new legal edition.'
    except Exception as exc:
        row.update(status='check_unavailable',error_type=type(exc).__name__,legal_currency_certified=False)
        status=getattr(exc,'code',None)
        if type(status) is int and 400<=status<=599:row['http_status']=status
    private_json(staging/(item['id']+'.check.json'),row)
    return row


def approve_candidate(item,receipt,artifact,*,reviewer,version,applicability_note):
    if not reviewer.strip() or not version.strip() or not applicability_note.strip():raise ValueError('Reviewer, version and applicability assessment required')
    if receipt.get('status') not in {'candidate_changed','snapshot_unchanged','page_unchanged'} or receipt.get('artifact_file','').endswith('.html'):raise ValueError('No complete reviewed text/PDF candidate')
    if receipt.get('active_entry_sha256')!=digest(item) or hashlib.sha256(Path(artifact).read_bytes()).hexdigest()!=receipt.get('artifact_sha256'):raise ValueError('Candidate or active entry changed since check')
    return {'document':item['id'],'candidate_sha256':receipt['artifact_sha256'],'active_entry_sha256':digest(item),
            'reviewer':reviewer,'version':version,'applicability_note':applicability_note,'decision':'approved_for_promotion',
            'identity_assurance':'operator_supplied_identity','reviewed_at':datetime.now(timezone.utc).isoformat()}


def promote(root,receipt_path,approval_path,*,apply=False,scope_reviewed=False,validator=None):
    """Validate a specialist-approved candidate, then atomically replace its manifest.

    Old artifacts remain for rollback. The approval identity is operator supplied.
    """
    import fcntl,os,tempfile,shutil
    root=Path(root).resolve();receipt_path=Path(receipt_path).resolve()
    receipt=json.loads(receipt_path.read_text());approval=json.loads(Path(approval_path).read_text())
    artifact=(receipt_path.parent/receipt['artifact_file']).resolve()
    if not artifact.is_relative_to(receipt_path.parent):raise ValueError('Candidate path escapes staging')
    with (root/'.promotion.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        manifest=json.loads((root/'manifest.json').read_text());item=next(d for d in manifest['documents'] if d['id']==receipt['id'])
        expected=approve_candidate(item,receipt,artifact,reviewer=approval['reviewer'],version=approval['version'],applicability_note=approval['applicability_note'])
        if any(approval.get(k)!=v for k,v in expected.items() if k not in {'reviewed_at'}):raise ValueError('Approval does not match candidate and active entry')
        if any(item.get(k) for k in ['excluded_sections','excluded_clauses']) and not scope_reviewed:raise ValueError('Existing temporal/exclusion scope must be reviewed explicitly')
        newfile='documents/'+item['id']+'-snapshot-'+receipt['artifact_sha256'][:16]+artifact.suffix
        updated=dict(item,file=newfile,sha256=receipt['artifact_sha256'],version=approval['version'],reviewed_on=approval['reviewed_at'][:10],applicability_note=approval['applicability_note'],specialist_review={'reviewer':approval['reviewer'],'record_sha256':digest(approval)})
        updated.pop('version_date',None)
        if artifact.suffix=='.txt':updated['source_html_sha256']=receipt['download_sha256']
        proposed=dict(manifest,documents=[updated if d['id']==item['id'] else d for d in manifest['documents']])
        with tempfile.TemporaryDirectory(prefix='sts-corpus-preview-') as temp:
            preview=Path(temp)
            for d in manifest['documents']:
                src=root/d['file'];dest=preview/d['file'];dest.parent.mkdir(parents=True,exist_ok=True)
                try:os.link(src,dest)
                except OSError:shutil.copyfile(src,dest)
            dest=preview/newfile;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(artifact,dest)
            private_json(preview/'manifest.json',proposed)
            if validator is None:
                from offline_knowledge import KnowledgeStore
                validator=KnowledgeStore
            validator(preview)
        if apply:
            dest=root/newfile
            if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest()!=receipt['artifact_sha256']:raise ValueError('Immutable snapshot collision')
            if not dest.exists():shutil.copyfile(artifact,dest)
            private_json(root/('manifest-before-'+digest(manifest)[:16]+'.json'),manifest)
            private_json(root/'manifest.json',proposed)
        return {'document':item['id'],'candidate_sha256':receipt['artifact_sha256'],'applied':apply,'previous_artifact_retained':True}
