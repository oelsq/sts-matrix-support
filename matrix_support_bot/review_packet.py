"""Bounded local source-review packets; no external orchestrator."""
import json
from urllib.parse import urlsplit

PROTOCOL = 'sts.review.v1'
MARKER = '__STS_DRAFT_TO_REVIEW__'
FIELDS = ('draft_system','draft_user','review_system','review_prefix','review_suffix')


def workflow_packet(domain, draft_messages, review_messages):
    if domain not in {'legal','finance'}:
        raise ValueError('Unknown specialist')
    if (len(draft_messages)<2 or draft_messages[0].get('role')!='system'
            or draft_messages[1].get('role')!='user' or len(review_messages)!=2
            or review_messages[0].get('role')!='system' or review_messages[1].get('role')!='user'):
        raise ValueError('Expected scoped system/user messages')
    prefix,found,suffix=review_messages[-1]['content'].rpartition(MARKER)
    if not found:raise ValueError('Review placeholder missing')
    # Retry turns belong only to this request. Serialize them as data, never merge user text into a system role.
    draft_user=draft_messages[1]['content']
    if len(draft_messages)>2:
        draft_user+='\n\nПредыдущая попытка и запрос исправления:\n'+json.dumps(draft_messages[2:],ensure_ascii=False)
    packet={'protocol':PROTOCOL,'domain':domain,'draft_system':draft_messages[0]['content'],
            'draft_user':draft_user,'review_system':review_messages[0]['content'],
            'review_prefix':prefix,'review_suffix':suffix}
    if any(not isinstance(packet[k],str) or len(packet[k])>100000 for k in FIELDS):
        raise ValueError('Invalid or oversized workflow field')
    if len(json.dumps(packet,ensure_ascii=False).encode())>256000:raise ValueError('Workflow packet too large')
    return packet
