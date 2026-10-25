// Flowise custom-function body; no network, filesystem or tool access.
const norm = v => v.split(/\s+/).join(' ').trim();
const headings = new Set(['коротко','что сделать сейчас','что можно сделать','важно','черновик','уточните','расчёт','расчет','необходимые данные','вывод','как использовать','юридический агент','финансовый агент']);
const unitize = answer => answer.split('\n').flatMap(line=>line.split(/(?<=[.!?])(?<!\d[.])\s+(?=[А-ЯЁA-Z])/)).map(t=>t.trim()).filter(t=>t && !headings.has(t.replace(/^#{1,6}\s*|[*:]/g,'').trim().toLowerCase())).map((text,i)=>({id:'C'+(i+1),text}));
const NUMBER_WORDS={"ноль": 0, "нуль": 0, "один": 1, "одного": 1, "одной": 1, "одну": 1, "два": 2, "две": 2, "двух": 2, "три": 3, "трех": 3, "трёх": 3, "четыре": 4, "четырех": 4, "четырёх": 4, "пять": 5, "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7, "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9, "десять": 10, "десяти": 10, "одиннадцать": 11, "одиннадцати": 11, "двенадцать": 12, "двенадцати": 12, "тринадцать": 13, "тринадцати": 13, "четырнадцать": 14, "четырнадцати": 14, "пятнадцать": 15, "пятнадцати": 15, "шестнадцать": 16, "шестнадцати": 16, "семнадцать": 17, "семнадцати": 17, "восемнадцать": 18, "восемнадцати": 18, "девятнадцать": 19, "девятнадцати": 19, "двадцать": 20, "двадцати": 20, "тридцать": 30, "тридцати": 30, "сорок": 40, "сорока": 40, "пятьдесят": 50, "пятидесяти": 50, "шестьдесят": 60, "шестидесяти": 60, "семьдесят": 70, "семидесяти": 70, "восемьдесят": 80, "восьмидесяти": 80, "девяносто": 90, "девяноста": 90, "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12};
const AGE_WORDS={"двенадцати":12,"тринадцати":13,"четырнадцати":14,"пятнадцати":15,"шестнадцати":16,"семнадцати":17,"восемнадцати":18};
const normalizeAgeWords = input => input.replace(/(?<![\p{L}\p{N}_])(двенадцати|тринадцати|четырнадцати|пятнадцати|шестнадцати|семнадцати|восемнадцати)летн(?:его|ему|ими|ий|яя|ее|ие|им|ем|ей|ую|юю|их|ым|ом|ых)(?![\p{L}\p{N}_])/giu,(_,word)=>String(AGE_WORDS[word.toLowerCase()])+' лет');
const quantities = input => {
 let text=normalizeAgeWords(input).toLowerCase().replace(/^\s*(?:\d+[.)]|[-*])\s+/,'').replace(/\[S\d+\]/gi,'').replace(/(^|[^0-9])(\d{1,2})[.](\d{1,2})[.](\d{4})(?![0-9])/g,'$1 $2 $3 $4 ');
 const tens=Object.keys(NUMBER_WORDS).filter(k=>[20,30,40,50,60,70,80,90].includes(NUMBER_WORDS[k])).join('|');
 const months=['января','февраля','марта','апреля','мая','июня','июля','августа','сентября'];
 const ones=Object.keys(NUMBER_WORDS).filter(k=>NUMBER_WORDS[k]>=1&&NUMBER_WORDS[k]<=9&&!months.includes(k)).join('|');
 text=text.replace(new RegExp('(^|[^а-яё])('+tens+')\\s+('+ones+')(?![а-яё])','g'),(_,pre,a,b)=>pre+(NUMBER_WORDS[a]+NUMBER_WORDS[b]));
 text=text.replace(/[а-яё]+/g,w=>Object.prototype.hasOwnProperty.call(NUMBER_WORDS,w)?NUMBER_WORDS[w]:w);
 text=text.replace(/(?<=\d)[ \u00a0\u202f](?=\d{3}(?:\D|$))/g,'').replace(/,/g,'.');
 return new Set((text.match(/\d+(?:[.]\d+)?/g)||[]).map(v=>String(Number(v))));
};
const advicePattern = /^(?:обеспечьте|заблокируйте|защитите|смените|выйдите|отключите|ограничьте|свяжитесь|не умножайте|не переходите|не открывайте|не передавайте|не сообщайте|не переводите|пришлите|расскажите|поясните|определите|зафиксируйте|убедитесь|сохраните|запишите|опишите|уточните|попросите|запросите|сравните|проверьте|подготовьте|напишите|приложите|составьте|укажите|сообщите|заполните|направьте|отправьте|обратитесь|прошу|кому:|адресат:|отправитель:|от:|дата:|подпись:|тема:|требование|заявление|претензия|\[)/i;
const fact = /\d|(?:^|[^а-яёa-z])(?:обязан|вправе|запрещ|неусто|пен[яию]|процент|должен|гарантир)|не позднее|в течение|рабочих дней|так как|поскольку|согласно|в соответствии|не мо(?:жет|гут)|не допуска|ограничива|регулирует|устанавливает|влеч[её]т|призна[её]тся|имеет право|подлежит/i;
const safeAdvice = (input,document=false) => {
 const text=input.replace(/^\s*(?:\d+[.)]|[-*])\s+/,'').replace(/\*\*/g,'').trim();
 const field=document && /^(?:кредитное бюро|кредитор|банк|фио|адрес|телефон|контакт|электронная почта|номер договора|договор|спорная запись|описание ошибки|подтверждения|приложения|приложение)\s*:\s*(?:\[[^\]\n]{2,100}\][\s,;—–.-]*)+$/i.test(text);
 const template=document && (/\[[^\]\n]{2,100}\]/.test(text) && /^(?:я,?\s|за период|работодателем допущена|сумма задолженности)/i.test(text) || text.toLowerCase()==='на основании изложенного, прошу:');
 return (field||template||advicePattern.test(text)||(text.endsWith('?')&&text.length<=240)) && !fact.test(text.replace(/\[S\d+\]/gi,''));
};
// Metric/unit relation guards complement semantic review; unrecognized prose is not certified.
const NUMERIC_METRICS=[["effective_rate", "гэ[сc]в|эффективн[а-яё ]{0,18}ставк"], ["fee", "комисси[а-яё]*|тариф[а-яё]*|сбор[а-яё]*"], ["limit", "лимит[а-яё]*"], ["penalty", "пен[яию]|пени|неустойк[а-яё]*|штраф[а-яё]*"], ["debt", "задолженност[а-яё]*|долг[а-яё]*"], ["salary", "зарплат[а-яё]*|заработн[а-яё ]{0,15}плат[а-яё]*"], ["interest_rate", "процентн[а-яё ]{0,15}ставк[а-яё]*|номинальн[а-яё ]{0,15}ставк[а-яё]*"], ["appeal_term", "обжалов[а-яё]*|жалоб[а-яё]*|апелляц[а-яё]*"], ["response_term", "рассмотр[а-яё]*|рассматр[а-яё]*|ответ[а-яё]*"]];
const canonicalNumber = value => {const [a,b='']=value.replace(',','.').split('.');const f=b.replace(/0+$/,'');return (a.replace(/^0+/,'')||'0')+(f?'.'+f:'');};
const numericNormalize = input => {
 let text=normalizeAgeWords(input).toLowerCase().replace(/\[S\d+\]/gi,'').replace(/(?<!\d)\d{1,2}[.]\d{1,2}[.]\d{4}(?!\d)/g,' DATE ').replace(/(?:статья|статьи|пункт|подпункт|ст[.])\s+\d+(?:[-.]\d+)*/g,' REFERENCE ');
 const months=new Set(['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']);
 const tens=Object.keys(NUMBER_WORDS).filter(k=>[20,30,40,50,60,70,80,90].includes(NUMBER_WORDS[k])).join('|');
 const ones=Object.keys(NUMBER_WORDS).filter(k=>NUMBER_WORDS[k]>=1&&NUMBER_WORDS[k]<=9&&!months.has(k)).join('|');
 text=text.replace(new RegExp('(?<![а-яё])('+tens+')\\s+('+ones+')(?![а-яё])','g'),(_,a,b)=>String(NUMBER_WORDS[a]+NUMBER_WORDS[b]));
 text=text.replace(/[а-яё]+/g,w=>Object.prototype.hasOwnProperty.call(NUMBER_WORDS,w)&&!months.has(w)?String(NUMBER_WORDS[w]):w);
 return text.replace(/(?<=\d)[ \u00a0\u202f](?=\d{3}(?:\D|$))/g,'');
};
const numericUnit = t => {
 if(/^(%|процент)/.test(t))return 'percent';if(t.includes('крат'))return 'multiplier';
 if(/^(тенге|теңге|тг|₸|kzt)/.test(t))return 'KZT';if(/^(доллар|usd|\$)/.test(t))return 'USD';if(/^(евро|eur|€)/.test(t))return 'EUR';if(/^(рубл|rub)/.test(t))return 'RUB';
 if(t.startsWith('рабоч'))return 'workday';if(t.startsWith('календар'))return 'calendar_day';if(/^(дн|день)/.test(t))return 'day';if(t.startsWith('месяц'))return 'month';return 'year';
};
const numericFacts = input => {
 const result=[];
 for(const clause of numericNormalize(input).split(/[;\n]|(?<!\d)[.!?]|[.!?](?!\d)|,(?!\d)/)) {
  const labels=[];
  for(const [metric,pattern] of NUMERIC_METRICS) for(const m of clause.matchAll(new RegExp(pattern,'gi'))) labels.push([m.index,m.index+m[0].length,metric]);
  for(const n of clause.matchAll(/(?<![a-zа-яё0-9_])\d+(?:[.,]\d+)?/g)) {
   const end=n.index+n[0].length;
   const u=clause.slice(end).match(/^\s*(%|процент[а-яё]*|[-–]?крат[а-яё]*|тенге|теңге|тг(?![а-яёa-z])|₸|kzt\b|доллар[а-яё]*|usd\b|\$|евро|eur\b|€|рубл[а-яё]*|rub\b|рабочи[а-яё]*\s+дн[а-яё]*|календарн[а-яё]*\s+дн[а-яё]*|дн[а-яё]*|день|месяц[а-яё]*|год[а-яё]*|лет(?![а-яёa-z]))/i);
   if(!u)continue;
   const before=labels.filter(x=>x[1]<=n.index&&n.index-x[1]<=100).sort((a,b)=>b[1]-a[1]);
   const after=labels.filter(x=>x[0]>=end&&x[0]-end<=65).sort((a,b)=>a[0]-b[0]);
   const label=before[0]||after[0];if(!label)continue;
   const prefix=clause.slice(Math.max(0,n.index-35),n.index);
   const bound=/не более|не превыш[а-яё]*|до\s*$|максимальн|предельн/.test(prefix)?'max':(/не менее|от\s*$|минимальн/.test(prefix)?'min':'exact');
   result.push([label[2],canonicalNumber(n[0]),numericUnit(u[1]),bound]);
  }
 }
 return result;
};
const numericRelationErrors = (answer,proofs) => {
 const supported=proofs.flatMap(numericFacts), keys=new Set(supported.map(x=>JSON.stringify(x))), errors=[];
 for(const [m,v,u,b] of numericFacts(answer)) {
  const exact=keys.has(JSON.stringify([m,v,u,b]))||(b!=='exact'&&keys.has(JSON.stringify([m,v,u,'exact'])));
  if(supported.some(x=>x[0]===m||x[1]===v)&&!exact)errors.push('unproven_numeric_relation:'+m+':'+v+':'+u+':'+b);
 }
 return errors;
};
const answer = $flow.state.draft;
const quality = JSON.parse($flow.state.quality_json);
const units = unitize(answer);
let audit, errors = [];
try { audit = typeof $audit === 'object' ? $audit : JSON.parse($audit.replace(/^```(?:json)?\s*|\s*```$/g,'')); } catch(e) { errors.push('invalid_audit_json'); }
if (audit && !Object.prototype.hasOwnProperty.call(audit,'verdict') && Array.isArray(audit.checks)) audit.verdict=audit.checks.every(r=>r && ['supported','advice','user_context'].includes(r.status))?'approve':'revise';
// Resolve model-selected proof IDs only against this request's exact source slices.
if(audit && Array.isArray(audit.checks)) for(const row of audit.checks) {
 if(row && Array.isArray(row.proofs)) for(const p of row.proofs) {
  if(p && typeof p.quote_id==='string') {
   const source=quality.sources.find(s=>s.id===p.source_id);
   const excerpt=source && Array.isArray(source.excerpts) && source.excerpts.find(x=>x.id===p.quote_id);
   p.quote=excerpt?excerpt.quote:'';
  }
 }
}
if (!answer || answer.length>14000 || !units.length || units.length>32) errors.push('invalid_answer_size');
if (!audit || !['approve','revise'].includes(audit.verdict) || !Array.isArray(audit.checks) || audit.checks.length!==units.length) errors.push('incomplete_audit');
const rows = new Map((audit && Array.isArray(audit.checks) ? audit.checks : []).filter(x=>x && typeof x==='object').map(r=>[r.unit_id,r]));
if (rows.size!==units.length || [...rows.keys()].some(k=>!units.some(u=>u.id===k))) errors.push('missing_or_duplicate_claims');
const sources = new Map(quality.sources.map(s=>[s.id,s.text]));
for (const unit of units) {
  const row=rows.get(unit.id); if (!row) { errors.push(unit.id+':missing'); continue; }
  const refs=[...unit.text.matchAll(/\[(S\d+)\]/g)].map(m=>m[1]);
  if (row.status==='supported' && !refs.length && safeAdvice(unit.text,quality.question_type==='document')) { row.status='advice';row.proofs=[]; }
  if (row.status==='supported') {
    if (!Array.isArray(row.proofs) || !row.proofs.length) { errors.push(unit.id+':missing_proof'); continue; }
    const proven = new Set(), proofTexts=[];
    for (const p of row.proofs) {
      if (!p || !sources.has(p.source_id) || typeof p.quote!=='string' || p.quote.length<16 || p.quote.length>650 || !norm(sources.get(p.source_id)).includes(norm(p.quote))) errors.push(unit.id+':invalid_quote');
      else { proven.add(p.source_id); proofTexts.push(p.quote); }
    }
    errors.push(...numericRelationErrors(unit.text,proofTexts).map(e=>unit.id+':'+e));
    const provenNumbers=quantities(proofTexts.join(' '));
    if ([...quantities(unit.text)].some(n=>!provenNumbers.has(n))) errors.push(unit.id+':unproven_quantity');
    if (!refs.length || refs.some(id=>!proven.has(id)) || [...proven].some(id=>!refs.includes(id))) errors.push(unit.id+':citation_mismatch');
  } else if (row.status==='advice') {
    if (refs.length || !safeAdvice(unit.text,quality.question_type==='document')) errors.push(unit.id+':unproven_fact_disguised_as_advice');
  } else if (row.status==='user_context') {
    if (refs.length || typeof row.user_quote!=='string' || row.user_quote.length<8 || !norm(quality.question+'\n'+quality.previous_question).includes(norm(row.user_quote)) || /обязан|вправе|запрещ|неусто|закон|гарантир/i.test(unit.text)) errors.push(unit.id+':invalid_user_context');
    if (typeof row.user_quote==='string' && [...quantities(unit.text)].some(n=>!quantities(row.user_quote).has(n))) errors.push(unit.id+':invented_user_quantity');
  } else errors.push(unit.id+':unsupported');
}
const constraints=quality.constraints||[];
const scopeErrors=text=>constraints.filter(c=>c.patterns.some(p=>!(new RegExp(p,'i')).test(norm(text.replace(/[*_\u0060]+/g,''))))).map(c=>'missing_qualification:'+c.id);
errors.push(...scopeErrors(answer));
if (audit && audit.verdict!=='approve') errors.push('review_requested_revision');
let ok=errors.length===0, finalAnswer=answer, finalAudit=audit, partial=false;
const canFilter=typeof $final!=='undefined' && $final==='yes' && quality.question_type!=='document' && audit && Array.isArray(audit.checks) && rows.size===units.length && audit.checks.length===units.length && !errors.some(e=>e.startsWith('missing_qualification:')) && !errors.some(e=>['invalid_audit_json','incomplete_audit','missing_or_duplicate_claims','invalid_answer_size'].includes(e));
if(!ok && canFilter) {
 const kept=units.filter(u=>rows.get(u.id).standalone===true && !errors.some(e=>e.startsWith(u.id+':')));
 if(kept.length && (kept.some(u=>rows.get(u.id).status==='supported') || (quality.question_type==='calculation' && kept.length>=2 && kept.every(u=>rows.get(u.id).status==='advice')))) {
  finalAnswer=kept.map(u=>u.text).join('\n\n');
  finalAudit={verdict:'approve',feedback:'Unsupported or dependent units removed after one repair.',checks:kept.map((u,i)=>({...rows.get(u.id),unit_id:'C'+(i+1)}))};
  ok=true;partial=true;
 }
}
if(ok && scopeErrors(finalAnswer).length){errors.push(...scopeErrors(finalAnswer));ok=false;}
return {audit_ok:ok?'yes':'no',audit_feedback:JSON.stringify({errors,feedback:(audit && audit.feedback || '')+' '+constraints.map(c=>c.guidance).join(' ')}),
 response:JSON.stringify({contract:'sts.claims.v1',question_type:quality.question_type,status:ok?'approved':'needs_revision',answer:ok?finalAnswer:'',audit:finalAudit || null,partial,validation_errors:errors.slice(0,16).map(e=>e.split(':').slice(0,2).join(':')),retrieval_hints:(!ok||partial)?units.filter(u=>errors.some(e=>e.startsWith(u.id+':'))).slice(0,4).map(u=>u.text.slice(0,400)):[]})};
