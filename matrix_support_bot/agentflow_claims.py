"""Source-bound claim contracts for the opt-in Agentflows V2 quality pipeline.

A literal source match verifies provenance, not logical entailment. The model
still reviews meaning; deterministic checks refuse missing/forged audit rows.
"""
import json
import re
from pathlib import Path
from agentflow_graphs import workflow_packet
from response_guidance import is_document_request, is_reference_question

CONTRACT = 'sts.claims.v1'
TYPES = {'definition', 'situation', 'calculation', 'document'}
HEADINGS = {'коротко','что сделать сейчас','что можно сделать','важно','черновик','уточните','расчёт','расчет','необходимые данные','вывод','как использовать','юридический агент','финансовый агент'}
ADVICE = r'^(?:не умножайте|не передавайте|не сообщайте|не переводите|пришлите|расскажите|поясните|определите|зафиксируйте|уточните|убедитесь|сохраните|запишите|опишите|уточните|попросите|запросите|сравните|проверьте|подготовьте|напишите|приложите|составьте|укажите|сообщите|заполните|направьте|отправьте|обратитесь|прошу|кому:|адресат:|отправитель:|от:|дата:|подпись:|тема:|требование|заявление|претензия|\[)'
FACT = r'\d|(?:^|[^а-яёa-z])(?:обязан|вправе|запрещ|неусто|пен[яию]|процент|должен|гарантир)|не позднее|в течение|рабочих дней|так как|поскольку|согласно|в соответствии'


def question_type(question):
    if is_document_request(question):
        return 'document'
    if re.search(r'рассчита|посчита|вычисли|сколько.*(?:перепла|платить|пен[яию])|какая.*(?:переплата|сумма пени)', question, re.I):
        return 'calculation'
    return 'definition' if is_reference_question(question) or (len(question.split())<=28 and re.match(r'^(?:включаются ли|учитываются ли|что входит|какие платежи)',question,re.I)) else 'situation'


def normalized(value):
    return ' '.join(value.split())


def units(answer):
    result = []
    for line in answer.splitlines():
        line = line.strip()
        heading = re.sub(r'^#{1,6}\s*|[*:]', '', line).strip().casefold()
        if not line or heading in HEADINGS:
            continue
        for sentence in re.split(r'(?<=[.!?])(?<!\d[.])\s+(?=[А-ЯЁA-Z])',line):
            result.append({'id': 'C'+str(len(result)+1), 'text': sentence})
    if not 1 <= len(result) <= 32 or len(answer) > 14000:
        raise ValueError('Answer has an invalid number/size of claim units')
    return result


def safe_advice(text, *, document=False):
    text = re.sub(r'^\s*(?:\d+[.)]|[-*])\s+', '', text).replace('**','').strip()
    if document and re.search(r'\[[^\]\n]{2,100}\]',text) and re.match(r'^(?:я,?\s|за период|работодателем допущена|сумма задолженности)',text,re.I):
        return not re.search(FACT,text,re.I)
    if document and text.casefold()=='на основании изложенного, прошу:':return True
    question = text.endswith('?') and len(text)<=240
    return (question or bool(re.search(ADVICE, text, re.I))) and not re.search(FACT, re.sub(r'\[S\d+\]', '', text), re.I)


def source_excerpts(text):
    """Exact bounded slices: the model selects IDs instead of recreating quotations."""
    chunks=[]
    for line in text.splitlines():
        line=line.strip()
        while line:
            end=min(len(line),550)
            if len(line)>550:
                boundary=line.rfind(' ',16,551)
                if boundary>16:end=boundary
            quote=line[:end].strip()
            if len(quote)>=16:chunks.append({'id':'Q'+str(len(chunks)+1),'quote':quote})
            line=line[end:].strip()
    return chunks


def make_quality_packet(domain, draft_messages, review_messages, context):
    if not isinstance(context, dict) or not isinstance(context.get('question'), str):
        raise ValueError('Explicit scoped question and sources required')
    q = context['question']
    previous = context.get('previous_question', '')
    if not q.strip() or len(q)>12000 or not isinstance(previous,str) or len(previous)>3000:
        raise ValueError('Invalid scoped conversation')
    evidence = context.get('evidence')
    if not isinstance(evidence,list) or not 1<=len(evidence)<=8:
        raise ValueError('Quality workflow requires 1–8 source fragments')
    sources=[]
    for i,p in enumerate(evidence,1):
        if not isinstance(p.get('text'),str) or not p['text'].strip() or len(p['text'])>20000:
            raise ValueError('Invalid source text')
        metadata='\n'.join(label+': '+str(p[key]) for key,label in [('title','Название документа'),('section','Раздел документа'),('version','Редакция или версия документа')] if p.get(key))
        sources.append({'id':'S'+str(i),'text':p['text']+('\nМетаданные проверенной карточки документа:\n'+metadata if metadata else ''),
                        'source_kind':p.get('document_kind','legislation'),'applicability_note':p.get('applicability_note','')})
    for source in sources:source['excerpts']=source_excerpts(source['text'])
    constraints=[]
    if re.search(r'отозв|отзыв',q,re.I) and re.search(r'согласи|персональн',q,re.I) and any('при наличии неисполненного' in normalized(p['text']).casefold() for p in sources):
        constraints.append({'id':'consent-withdrawal-exceptions','patterns':['неисполненн|невыполненн|непогашенн','противореч|вопреки'],
          'guidance':'По переданной статье укажи оба исключения: отзыв не допускается, если противоречит законам или имеется неисполненное обязательство. Не обещай безусловное прекращение обработки.'})
    packet=workflow_packet(domain,draft_messages,review_messages)
    for unused in ('review_system','review_prefix','review_suffix'):packet[unused]=''
    packet['quality']={'contract':CONTRACT,'question':q,'previous_question':previous,
                       'question_type':question_type(q),'sources':sources,'constraints':constraints}
    if len(json.dumps(packet,ensure_ascii=False).encode())>256000:
        raise ValueError('Quality packet too large')
    return packet


NUMBER_WORDS=json.loads((Path(__file__).with_name('agents')/'quality/number_words.json').read_text())


def quantities(text):
    text=re.sub(r'^\s*(?:\d+[.)]|[-*])\s+', '', text)
    text=re.sub(r'\[S\d+\]', '', text)
    text=text.casefold()
    text=re.sub(r'(?<!\d)(\d{1,2})[.](\d{1,2})[.](\d{4})(?!\d)',r' \1 \2 \3 ',text)
    tens='|'.join(k for k,v in NUMBER_WORDS.items() if v in range(20,100,10))
    ones='|'.join(k for k,v in NUMBER_WORDS.items() if 1<=v<=9 and k not in {'января','февраля','марта','апреля','мая','июня','июля','августа','сентября'})
    text=re.sub(r'(?<![а-яё])('+tens+r')\s+('+ones+r')(?![а-яё])',lambda m:str(NUMBER_WORDS[m[1]]+NUMBER_WORDS[m[2]]),text)
    text=re.sub(r'[а-яё]+',lambda m:str(NUMBER_WORDS[m[0]]) if m[0] in NUMBER_WORDS else m[0],text)
    text=re.sub(r'(?<=\d)[ \u00a0\u202f](?=\d{3}(?:\D|$))','',text)
    return {str(float(v)) for v in re.findall(r'\d+(?:[.,]\d+)?',text.replace(',', '.'))}


def validate_audit(answer, audit, sources, question='', previous_question='', *, format_type=None):
    errors=[]
    expected=units(answer)
    if not isinstance(audit,dict) or audit.get('verdict') not in {'approve','revise'}:
        return ['invalid_audit']
    checks=audit.get('checks')
    if not isinstance(checks,list) or len(checks)!=len(expected):
        return ['incomplete_audit']
    by_id={r.get('unit_id'):r for r in checks if isinstance(r,dict)}
    if len(by_id)!=len(checks) or set(by_id)!={r['id'] for r in expected}:
        return ['missing_or_duplicate_claims']
    source_map={p['id']:p['text'] for p in sources}
    for unit in expected:
        row=by_id[unit['id']];kind=row.get('status');proofs=row.get('proofs')
        refs=set(re.findall(r'\[(S\d+)\]',unit['text']))
        if kind=='supported':
            if not isinstance(proofs,list) or not proofs:
                errors.append(unit['id']+':missing_proof');continue
            proven=set();proof_texts=[]
            for proof in proofs:
                if not isinstance(proof,dict):errors.append(unit['id']+':invalid_proof');continue
                ident=proof.get('source_id');quote=proof.get('quote')
                if ident not in source_map or not isinstance(quote,str) or not 16<=len(quote)<=650 or normalized(quote) not in normalized(source_map[ident]):
                    errors.append(unit['id']+':invalid_quote')
                else:
                    proven.add(ident);proof_texts.append(quote)
            if not quantities(unit['text']).issubset(quantities(' '.join(proof_texts))):
                errors.append(unit['id']+':unproven_quantity')
            if not refs or not refs.issubset(proven) or not proven.issubset(refs):
                errors.append(unit['id']+':citation_mismatch')
        elif kind=='advice':
            if refs or not safe_advice(unit['text'],document=format_type=='document'):errors.append(unit['id']+':unproven_fact_disguised_as_advice')
        elif kind=='user_context':
            quote=row.get('user_quote','')
            if refs or not isinstance(quote,str) or len(quote)<8 or normalized(quote) not in normalized(question+'\n'+previous_question):
                errors.append(unit['id']+':invalid_user_context')
            if not quantities(unit['text']).issubset(quantities(quote)):
                errors.append(unit['id']+':invented_user_quantity')
            if re.search(r'обязан|вправе|запрещ|неусто|закон|гарантир',unit['text'],re.I):
                errors.append(unit['id']+':user_text_is_not_law')
        else:errors.append(unit['id']+':unsupported')
    if audit['verdict']!='approve':errors.append('review_requested_revision')
    return errors


def accept_quality_response(text, packet):
    value=json.loads(text)
    if not isinstance(value,dict) or value.get('contract')!=CONTRACT or value.get('status')!='approved':
        raise ValueError('Claim review did not approve an answer')
    answer=value.get('answer');audit=value.get('audit')
    if not isinstance(answer,str) or not answer.strip():raise ValueError('Empty reviewed answer')
    quality=packet['quality']
    if value.get('question_type')!=quality['question_type']:raise ValueError('Question type mismatch')
    errors=validate_audit(answer,audit,quality['sources'],quality['question'],quality['previous_question'],format_type=quality['question_type'])
    for constraint in quality.get('constraints',[]):
        if any(not re.search(pattern,answer,re.I) for pattern in constraint['patterns']):errors.append('missing_qualification:'+constraint['id'])
    if errors:raise ValueError('Claim evidence validation failed: '+','.join(errors[:4]))
    return answer
