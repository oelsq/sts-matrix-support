#!/usr/bin/env python3
"""Opt-in underlying model comparison; deliberately bypasses checked answer templates."""
import asyncio,json,os,time,argparse,sys
from pathlib import Path
import aiohttp
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'matrix_support_bot'))
parser=argparse.ArgumentParser(description="Compare explicitly selected LiteLLM aliases on synthetic questions. No Matrix messages.")
parser.add_argument('--models',required=True,help='Comma-separated permitted LiteLLM aliases; maximum 8')
parser.add_argument('--output',required=True)
parser.add_argument('--knowledge',default=str(ROOT/'offline_knowledge'))
args=parser.parse_args()
models=list(dict.fromkeys(m.strip() for m in args.models.split(',') if m.strip()))
if not models or len(models)>8: parser.error('Choose 1–8 model aliases')
from agents import messages,review_messages,render
from evidence_scope import scope_evidence
from offline_knowledge import KnowledgeStore
from llm_settings import api_key
store=KnowledgeStore(args.knowledge)
cases=[('finance','Какая комиссия в Halyk SuperApp за оплату коммунальных услуг?', ['0 тенге','Halyk']),
       ('legal','Мне не платят зарплату. С чего начать?', ['работодател','Трудовой кодекс']),
       ('legal','Купил неисправный телефон. Могу ли попросить возврат денег?', ['возврат','потребител'])]
results=[]
async def main():
 sem=asyncio.Semaphore(2)
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as http:
  async def test(model):
   for domain,q,required in cases:
    async with sem:
     started=time.monotonic()
     ev=scope_evidence(domain,q,store.search(domain,q,8))
     row={'model':model,'domain':domain,'question':q}
     async def complete(msgs):
      async with http.post(os.environ['LLM_ENDPOINT'].rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+api_key()},json={'model':model,'messages':msgs,'temperature':0.1,'max_tokens':1500},allow_redirects=False) as response:
       if response.status!=200:raise RuntimeError('HTTP_'+str(response.status))
       data=await response.json()
      if data['choices'][0].get('finish_reason')=='length':raise RuntimeError('truncated')
      return data['choices'][0]['message']['content']
     try:
      draft=await complete(messages(domain,q,ev))
      answer=render(domain,await complete(review_messages(domain,q,draft,ev)),q,ev)
      row.update(answer=answer,passed='Не удалось подготовить ответ с проверяемой' not in answer and all(w.casefold() in answer.casefold() for w in required))
     except Exception as e:row.update(passed=False,error=str(e) if isinstance(e,RuntimeError) else type(e).__name__)
     row['elapsed_ms']=round((time.monotonic()-started)*1000)
     results.append(row)
     print(json.dumps({k:v for k,v in row.items() if k not in {'question','answer'}},ensure_ascii=False),flush=True)
  await asyncio.gather(*(test(m) for m in models))
asyncio.run(main())
Path(args.output).write_text(json.dumps({'synthetic_only':True,'matrix_messages_sent':False,'cases':results,'limits':'Bounded automated checks plus manual answer review required; not general legal certification.'},ensure_ascii=False,indent=2))
