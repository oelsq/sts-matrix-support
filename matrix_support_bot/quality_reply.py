"""Shared protected Agentflow execution for Matrix and offline/live evaluations."""
from dataclasses import dataclass,field
import logging
import asyncio,time
from agents import messages,review_messages,render
from answer_usefulness import usefulness_issues
from agentflow_graphs import MARKER
from evidence_research import research_evidence,fingerprint
from flowise_client import FlowiseReviewRejected

log=logging.getLogger('support_bot.research')

@dataclass
class QualityReply:
    answer:str
    evidence:list
    research_attempts:int=0
    workflow_metrics:list=field(default_factory=list)


class EvaluatedAnswer(str):
    def __new__(cls,result):
        value=super().__new__(cls,result.answer);value.evidence=result.evidence;value.research_attempts=result.research_attempts;value.workflow_metrics=result.workflow_metrics;return value


async def quality_reply(flow,http,domain,room,sender,event_id,question,previous,evidence,knowledge,*,search_question=None,budget_seconds=180):
    if not 1<=budget_seconds<=240:raise ValueError('Invalid total reply budget')
    deadline=time.monotonic()+budget_seconds
    search_question=search_question or question
    current=evidence;best=None;researches=0;measurements=[]
    for attempt in range(2):
        hints=();failure=None
        try:
            text=await asyncio.wait_for(flow.run_workflow(http,domain,room,sender,event_id+(':research' if attempt else ''),
                messages(domain,question,current,previous),review_messages(domain,search_question,MARKER,current),
                context={'question':question,'previous_question':previous or '', 'evidence':current}),timeout=max(.01,deadline-time.monotonic()))
            if getattr(text,'workflow_metrics',None):
                measurements.append(dict(text.workflow_metrics, attempt=attempt+1))
            review=getattr(text,'review',None)
            answer=render(domain,text.strip(),question,current,review=review)
            issues=usefulness_issues(question,answer)
            # A checked fragment is useful even when it is not yet the complete
            # requested letter. Never discard it merely for that missing artefact.
            if getattr(review,'partial',False):
                issues=[issue for issue in issues if issue!='requested_draft_missing']
            if 'Не удалось подготовить ответ с проверяемой' in answer or issues:
                raise FlowiseReviewRejected('Reviewed reply did not pass response checks',retrieval_hints=(question,),validation_errors=tuple('reply_'+issue for issue in issues) or ('render_rejected',))
            result=QualityReply(answer,current,researches,list(measurements))
            if not getattr(review,'partial',False):return result
            best=result;hints=getattr(review,'retrieval_hints',())
        except FlowiseReviewRejected as exc:
            if getattr(exc,'workflow_metrics',None):
                measurements.append(dict(exc.workflow_metrics, attempt=attempt+1))
            exc.workflow_metrics=list(measurements)
            hints=exc.retrieval_hints;failure=exc
        except Exception:
            if best:return QualityReply(best.answer,best.evidence,researches,list(measurements))
            raise
        if attempt==0 and deadline-time.monotonic()>5:
            try:fresh=research_evidence(knowledge,domain,search_question,previous,current,hints)
            except Exception as exc:
                log.warning('Evidence re-search unavailable (%s)',type(exc).__name__);fresh=current
            if fingerprint(fresh)!=fingerprint(current):
                current=fresh;researches+=1
                log.info('Evidence re-search: domain=%s passages=%d',domain,len(current));continue
        if best:return QualityReply(best.answer,best.evidence,researches,list(measurements))
        raise failure or FlowiseReviewRejected('No supported answer after bounded re-search')
