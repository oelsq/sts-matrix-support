"""Source-anchored explanation plans, never additional citation evidence."""
import json,re
from pathlib import Path

GUIDES=Path(__file__).with_name('agents')/'explanation_guides.json'

def explanation_guides(question, previous, evidence):
    query=question+'\n'+(previous or '')
    result=[]
    for guide in json.loads(GUIDES.read_text()):
        if not re.search(guide['question_pattern'],query,re.I):continue
        anchors=[]
        for anchor in guide['anchors']:
            matches=[('S'+str(i),p) for i,p in enumerate(evidence,1)
                     if p.get('document_id')==anchor['document_id']
                     and re.search(anchor['text_pattern'],p.get('text',''),re.I)]
            if not matches:break
            anchors.append({'source_id':matches[0][0],'point':anchor['point']})
        else:
            result.append({'id':guide['id'],'status':'editorial_plan_not_expert_approved',
                           'primary_anchors':anchors,'explain':guide['explain'],
                           'boundaries':guide['boundaries']})
    return result[:2]
