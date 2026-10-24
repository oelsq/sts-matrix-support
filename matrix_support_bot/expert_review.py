"""Private, version-bound human review packets. No automatic specialist approval."""
import html,json
from evaluation_core import digest,private_json,record_review


def export_packet(cases,run,output,*,corpus,knowledge_root=None,include_answers=True):
    from pathlib import Path
    if run.get('status')!='complete' or run.get('corpus_sha256')!=corpus:raise ValueError('A complete run on the current corpus is required')
    output=Path(output)
    if any((output/name).exists() for name in ['review.html','review-template.json']):raise ValueError('Use a new review folder; never overwrite specialist work')
    output.mkdir(parents=True,exist_ok=True);output.chmod(0o700)
    local_sources={}
    if knowledge_root is not None:
        import hashlib,shutil,re
        root=Path(knowledge_root).resolve();manifest=json.loads((root/'manifest.json').read_text())
        wanted={p['document'] for row in run['rows'] for p in row.get('evidence',[])}
        for item in manifest['documents']:
            if item['id'] not in wanted:continue
            source=(root/item['file']).resolve()
            if not re.fullmatch(r'[a-z][a-z0-9-]{1,79}',item['id']) or not source.is_relative_to(root) or hashlib.sha256(source.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Full source changed or has an unsafe path')
            name=item['id']+source.suffix;folder=output/'sources';folder.mkdir(mode=0o700,exist_ok=True);dest=folder/name;shutil.copyfile(source,dest);dest.chmod(0o600);local_sources[item['id']]='sources/'+name
    by_id={c['id']:c for c in cases};rows=[];cards=[];answer_count=0
    if len(by_id)!=len(run['rows']) or set(by_id)!={r['id'] for r in run['rows']}:raise ValueError('Complete unique case coverage is required')
    for row in run['rows']:
        case=by_id[row['id']]
        if row['case_sha256']!=digest(case):raise ValueError('Case changed since run')
        common={'case_id':case['id'],'case_sha256':digest(case),'corpus_sha256':corpus,'answer_sha256':None,
                'reviewer':'','role':'legal_specialist' if case['domain']=='legal' else 'finance_specialist','reviewed_by_human':False,'decision':None,'notes':''}
        rows.append(dict(common,kind='reference',scores=None))
        if row.get('answer'):
            rows.append(dict(common,kind='answer',answer_sha256=digest(row['answer']),scores={'accuracy':None,'usefulness':None,'exceptions':None}));answer_count+=1
        esc=lambda v:html.escape(str(v))
        materials=''.join('<details><summary>'+esc(p['document'])+' — '+esc(p.get('section',''))+'</summary>'+('<p><a href="'+html.escape(local_sources[p['document']],quote=True)+'">Полный документ из проверенной базы</a></p>' if p['document'] in local_sources else '')+'<p>'+esc(p['source'])+'</p><pre>'+esc(p['text'])+'</pre></details>' for p in row.get('evidence',[]))
        shown_answer=(row.get('answer') or 'Проверенный ответ не получен') if include_answers else ('Текст не скопирован: проверяйте исходный закрытый отчёт. SHA-256 ответа: '+digest(row['answer']) if row.get('answer') else 'Проверенный ответ не получен; эталон всё равно требует проверки.')
        cards.append('<article><h2>'+esc(case['id'])+'</h2><p><b>Вопрос:</b> '+esc(case['question'])+'</p><p><b>Предыдущий контекст:</b> '+esc(case.get('previous_questions',[]))+'</p><h3>Фактический ответ</h3><pre>'+esc(shown_answer)+'</pre><h3>Кандидатный эталон — пока не одобрен</h3><p>'+esc(case['candidate_reference'])+'</p><p>Механизм: '+esc(row['mechanism'])+'; автоматические проверки: '+esc(row.get('checks'))+'</p>'+materials+'</article>')
    template={'schema_version':1,'corpus_sha256':corpus,'review_status':'awaiting_real_specialists','identity_assurance':'operator_supplied_not_independently_authenticated','answer_text_copied':include_answers,'reviews':rows}
    private_json(output/'review-template.json',template)
    page='<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Проверка ответов специалистов</title><style>body{font:16px/1.6 system-ui;max-width:1050px;margin:40px auto;padding:0 24px;color:#172033}article{border-top:2px solid #cdd6e4;padding:24px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f9;padding:18px;border-radius:8px}details{margin:14px 0}h2{color:#234e85}aside{background:#fff1ce;padding:18px}</style><h1>Проверка ответов юристом и финансистом</h1><aside>Экспертное подтверждение ещё не получено. Это закрытый пакет для проверки ответов и источников. Сопоставьте полный текст применимой нормы и исключения, а не только совпадение цитаты. Сначала отдельно проверьте эталон, затем фактический ответ. Заполните review-template.json: имя специалиста, роль, решение, баллы 0–4 и замечания. Установить reviewed_by_human=true может только оператор после реальной проверки. Импорт не удостоверяет личность проверяющего.</aside>'+''.join(cards)+'</html>'
    path=output/'review.html';path.write_text(page);path.chmod(0o600)
    return {'answers_for_review':answer_count,'references_for_review':len(cards),'answer_text_copied':include_answers,'review_records':len(rows),'specialist_approvals_created':0,'full_sources_copied':len(local_sources)}


def import_reviews(cases,run,template,*,corpus):
    if run.get('status')!='complete' or run.get('corpus_sha256')!=corpus or template.get('corpus_sha256')!=corpus:raise ValueError('Review corpus changed')
    by_id={c['id']:c for c in cases};actual={r['id']:r for r in run['rows']};reviews=[];seen=set()
    for row in template.get('reviews',[]):
        if row.get('decision') is None:continue
        if row.get('reviewed_by_human') is not True:raise ValueError('Actual human review must be explicitly confirmed')
        case=by_id[row['case_id']];answer=actual[case['id']]['answer'];key=(case['id'],row['kind'])
        if key in seen:raise ValueError('Duplicate review');
        seen.add(key)
        if row['case_sha256']!=digest(case) or row['corpus_sha256']!=corpus or (row['kind']=='answer' and (not answer or row['answer_sha256']!=digest(answer))) or (row['kind']=='reference' and row.get('answer_sha256') is not None):raise ValueError('Case, answer or source corpus changed')
        if row['decision']=='rejected' and not row.get('notes','').strip():raise ValueError('Explain the substantive error and proposed correction')
        reviews.append(record_review(case,corpus,reviewer=row['reviewer'],role=row['role'],kind=row['kind'],decision=row['decision'],answer=answer if row['kind']=='answer' else None,scores=row.get('scores'),notes=row.get('notes','')))
    return reviews
