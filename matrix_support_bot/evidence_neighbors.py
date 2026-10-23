"""Add bounded, same-article qualifications without crossing acts or access scope."""
import re
from collections import defaultdict

EXCEPTION=re.compile(r'за исключением|исключени[ея]|кроме случаев|не допускается|не может|при условии|несмотря на',re.I)

class QualificationIndex:
    def __init__(self,passages):
        self.sections=defaultdict(list)
        for p in passages:
            if p.get('section'):self.sections[(p['document_id'],p['section'])].append(p)

    def augment(self,chosen,question,*,limit=8):
        if limit<4 or not chosen:return chosen[:limit]
        ids={p['id'] for p in chosen};peers=[]
        # Only retrieve companions from articles already selected in this scope.
        # Do not promote private-policy documents into a public-law answer.
        for p in chosen[:3]:
            key=(p['document_id'],p.get('section'))
            for other in self.sections.get(key,[]):
                if other['id'] in ids or other.get('domains')!=p.get('domains') or not EXCEPTION.search(other['text']):continue
                ids.add(other['id']);peers.append(other)
        if not peers:return chosen[:limit]
        # Preserve the first three direct hits, reserve at most two companion slots.
        inserted=peers[:2]
        return (chosen[:3]+inserted+chosen[3:])[:limit]
