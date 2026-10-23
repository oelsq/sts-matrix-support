"""Check only the isolated synthetic load rooms, without exporting answer text."""
import asyncio,json,time,sys,hashlib
from pathlib import Path
from urllib.parse import quote
import aiohttp
sys.path.insert(0,'/app')
from evaluation_core import private_json,automated_checks
async def main():
 stage=Path('/stage');gen=json.loads((stage/'load-credentials.json').read_text())['generator'];assert gen['user_id'].startswith('@sts-load-generator-')
 cases={c['id']:c for c in json.loads(Path('/app/config/golden-cases.json').read_text())['cases']}
 mixed=['Начальник не отдаёт деньги за работу','Какая комиссия в Halyk SuperApp за оплату коммунальных услуг?','Можно ли отозвать согласие на обработку персональных данных?','Что такое ГЭСВ и чем отличается от номинальной ставки?','Что регулирует брачный договор?','Что страхуется по обязательному автострахованию: машина или ответственность?','Работодатель заставляет уйти в отпуск без содержания. Обязан соглашаться?','Какие комиссии и страховки включаются в ГЭСВ?']
 ids=['wages','halyk','consent','gesv','marriage','auto-insurance','forced-leave','gesv-costs'];lookup=dict(zip(mixed,ids));lookup.update({c['question']:c['id'] for c in cases.values()})
 requests={};replies={};rows={};deadline=time.monotonic()+900
 async with aiohttp.ClientSession(headers={'Authorization':'Bearer '+gen['access_token']}) as http:
  async def get(path):
   async with http.get('http://homeserver:8008'+path,timeout=aiohttp.ClientTimeout(total=20)) as r:
    if r.status in {401,403}:raise PermissionError('Synthetic observer was deactivated')
    r.raise_for_status();return await r.json()
  while time.monotonic()<deadline:
   try:
    rooms=(await get('/_matrix/client/v3/joined_rooms'))['joined_rooms']
    for room in rooms:
     data=await get('/_matrix/client/v3/rooms/'+quote(room,safe='')+'/messages?dir=b&limit=100')
     for e in data['chunk']:
      c=e.get('content',{});body=c.get('body','');request=c.get('org.sts.request_id')
      if request and c.get('org.sts.reply_key') and body and not body.startswith('Готовлю ответ'):replies[request]=body
      elif body in lookup:requests[e['event_id']]=lookup[body]
   except PermissionError:break
   for request,case in requests.items():
    if request in replies:rows[hashlib.sha256(request.encode()).hexdigest()]={'case_id':case,'checks':automated_checks(cases[case],replies[request],[])}
   result={'answered_and_checked':len(rows),'automatic_passed':sum(x['checks']['passed'] for x in rows.values()),'rows':list(rows.values()),'expert_accuracy':None}
   private_json(stage/'matrix-answer-checks.json',result)
   if len(rows)>=42:break
   await asyncio.sleep(3)
 print(json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True)
 if len(rows)!=42 or any(not x['checks']['passed'] for x in rows.values()):raise RuntimeError('Synthetic answer checks incomplete or failed')
if __name__=='__main__':asyncio.run(main())
