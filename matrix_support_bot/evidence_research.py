"""One bounded local re-search; review feedback is query data, never authority."""
import re
from evidence_scope import scope_evidence
from offline_knowledge import BANK_NAMES


def fingerprint(passages):
    return tuple((p['id'],p['text']) for p in passages or [])


def research_evidence(store,domain,question,previous,existing,hints=(),*,limit=8):
    if store is None or domain not in {'legal','finance'}:return existing
    query=question+(' Подробности предыдущего вопроса: '+previous[:1500] if previous else '')
    # Never follow reviewer URLs, execute code, or change the original product scope.
    clean=[]
    for hint in hints[:4]:
        if not isinstance(hint,str):continue
        hint=re.sub(r'https?://\S+|\[S\d+\]',' ',hint)
        clean.append(hint[:400])
    found=store.search(domain,query+' '+ ' '.join(clean),limit=16)
    # Resolve references only inside a document already selected by scoped search.
    for original in (existing or [])[:3]:
        numbers=re.findall(r'стать[еёиьяю]{1,2}\s+(\d+(?:-\d+)?)\s+(?:настоящего|данного)\s+(?:закона|кодекса)',original['text'],re.I)[:3]
        for number in numbers:
            prefix=re.compile(r'^Статья '+re.escape(number)+r'(?:\.|\s|$)')
            if hasattr(store,'passages'):
                referenced=[p for p in store.passages if p['document_id']==original['document_id']
                    and prefix.match(p.get('section','')) and p.get('domains')==original.get('domains')
                    and not any(re.match(r'^Статья '+re.escape(str(denied))+r'(?:\.|\s|$)',p.get('section','')) for denied in p.get('excluded_sections',[]))]
            else:referenced=store.search(domain,original['title']+' статья '+number,limit=8)
            found.extend(p for p in referenced if p['document_id']==original['document_id'] and prefix.match(p.get('section','')))
    banks={bank for bank,names in BANK_NAMES.items() if any(name in question.casefold() for name in names)}
    found=[p for p in found if not p.get('bank') or p['bank'] in banks]
    scoped=scope_evidence(domain,question,found)
    original=scope_evidence(domain,question,list(existing or []))
    seen={p['id'] for p in original};new=[]
    for p in scoped:
        if p['id'] not in seen:new.append(p);seen.add(p['id'])
    if not new:return existing
    result=(original[:3]+new[:4]+original[3:])[:limit]
    if getattr(store,'qualifications',None):result=store.qualifications.augment(result,question,limit=limit)
    # Re-apply original scope after qualification expansion.
    return scope_evidence(domain,question,result)
