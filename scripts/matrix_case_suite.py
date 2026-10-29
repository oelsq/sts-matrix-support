"""Exercise real Matrix replies in private, synthetic-only rooms.

Runs against an isolated copy of the production bot and its protected Agentflows.
No real-user timeline or credentials are exported. Criteria are not expert scores.
"""
import asyncio
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import quote
import uuid

import aiohttp

sys.path.insert(0, '/app')
from evaluation_core import automated_checks, private_json


def final_key(bot_user, request_id):
    identity = hashlib.sha256(('http://homeserver:8008\0' + bot_user).encode()).hexdigest()
    return hashlib.sha256((identity + '\0' + request_id + '\0final').encode()).hexdigest()


def summarize(rows, planned):
    answered = [r for r in rows if r.get('answer') is not None]
    timings = sorted(r['seconds'] for r in answered)
    return {
        'planned': planned, 'completed': len(rows), 'answered': len(answered),
        'automatic_passed': sum(r.get('checks', {}).get('passed', False) for r in rows),
        'generation_unavailable': sum(r.get('generation_unavailable', False) for r in rows),
        'validation_fallback': sum(r.get('validation_fallback', False) for r in rows),
        'duplicates': sum(max(0, r.get('reply_count', 0) - 1) for r in rows),
        'errors': dict(Counter(r['error'] for r in rows if r.get('error'))),
        'domains': dict(Counter(r['domain'] for r in rows)),
        'p50_seconds': timings[max(0, math.ceil(len(timings)*.5)-1)] if timings else None,
        'p95_seconds': timings[max(0, math.ceil(len(timings)*.95)-1)] if timings else None,
        'expert_accuracy': None, 'human_specialist_approvals': 0,
    }


async def run(stage):
    credentials = json.loads((stage/'load-credentials.json').read_text())
    generator, bot = credentials['generator'], credentials['bot']
    assert generator['user_id'].startswith('@sts-cases-generator-')
    cases = json.loads((stage/'cases.json').read_text())['cases']
    rows = []
    replies = {}
    pending = {}
    since = None
    rooms = {}
    report_path = stage/'matrix-case-results.json'
    halted = asyncio.Event()
    fatal = []
    total_messages = 0

    def checkpoint(status='in_progress'):
        private_json(report_path, {'schema_version': 1, 'status': status,
            'mode': 'real_matrix_isolated_production_image_and_workflows',
            'summary': summarize(rows, len(cases)), 'question_messages': total_messages,
            'rows': rows, 'criteria_frozen_before_execution': True})

    async with aiohttp.ClientSession() as http:
        async def api(method, path, body=None, account=generator):
            for _ in range(20):
                async with http.request(method, 'http://homeserver:8008'+path, json=body,
                        headers={'Authorization': 'Bearer '+account['access_token']},
                        timeout=aiohttp.ClientTimeout(total=35)) as response:
                    data = await response.json()
                    if response.status == 429:
                        await asyncio.sleep(min(20, max(.1, data.get('retry_after_ms', 1000)/1000))+.05)
                        continue
                    if response.status >= 400:
                        raise RuntimeError('matrix_http_'+str(response.status))
                    return data
            raise RuntimeError('matrix_rate_limit_budget')

        async def observe():
            nonlocal since
            try:
                while not halted.is_set():
                    path = '/_matrix/client/v3/sync?timeout=1000&filter='+quote(json.dumps(
                        {'room': {'rooms': list(rooms.values()), 'timeline': {'limit': 100}}}))
                    if since: path += '&since='+quote(since)
                    data = await api('GET', path)
                    since = data['next_batch']
                    for room, value in data.get('rooms', {}).get('join', {}).items():
                        if room not in rooms.values(): continue
                        for event in value.get('timeline', {}).get('events', []):
                            content = event.get('content', {})
                            request_id = content.get('org.sts.request_id')
                            if (event.get('sender') != bot['user_id'] or not isinstance(request_id, str)
                                    or content.get('org.sts.reply_key') != final_key(bot['user_id'], request_id)):
                                continue
                            replies.setdefault(request_id, {})[event['event_id']] = content
                            if request_id in pending: pending[request_id].set()
            except Exception as exc:
                fatal.append(type(exc).__name__)
                for event in pending.values(): event.set()
                halted.set()

        async def send(room, account, text):
            nonlocal total_messages
            start = time.monotonic()
            result = await api('PUT', '/_matrix/client/v3/rooms/'+quote(room, safe='')+
                '/send/m.room.message/'+uuid.uuid4().hex, {'msgtype':'m.text', 'body':text}, account)
            request_id = result['event_id']
            pending[request_id] = asyncio.Event()
            if request_id in replies: pending[request_id].set()
            total_messages += 1
            try:
                await asyncio.wait_for(pending[request_id].wait(), timeout=300)
                if fatal: raise RuntimeError('observer_failed')
                values = replies.get(request_id, {})
                if not values: raise RuntimeError('no_correlated_reply')
                return request_id, next(iter(values.values())), round(time.monotonic()-start, 3)
            except asyncio.TimeoutError:
                raise RuntimeError('reply_timeout') from None

        # Dedicated synthetic rooms: never join or inspect a real user's room here.
        for domain in ['legal', 'finance']:
            result = await api('POST', '/_matrix/client/v3/createRoom',
                {'name':'STS — '+domain+' — synthetic case campaign', 'preset':'private_chat'})
            rooms[domain] = result['room_id']
            await api('POST', '/_matrix/client/v3/rooms/'+quote(rooms[domain], safe='')+'/invite',
                {'user_id':bot['user_id']})
        for i in range(4):
            account = credentials['user'+str(i)]
            for room in rooms.values():
                await api('POST', '/_matrix/client/v3/rooms/'+quote(room, safe='')+'/invite',
                    {'user_id':account['user_id']})
                await api('POST', '/_matrix/client/v3/join/'+quote(room, safe=''), {}, account)
        template = json.loads((stage/'flow-routes-template.json').read_text())
        domain_flows = {v['domain']:v for v in template.values()}
        private_json(Path('/loadstate/flowise_rooms.json'),
            {room:domain_flows[domain] for domain,room in rooms.items()})
        private_json(stage/'suite-ready.json', {'rooms_ready':True})
        deadline = time.monotonic()+180
        for room in rooms.values():
            while bot['user_id'] not in (await api('GET', '/_matrix/client/v3/rooms/'+
                    quote(room, safe='')+'/joined_members'))['joined']:
                if time.monotonic()>deadline: raise RuntimeError('bot_join_timeout')
                await asyncio.sleep(.5)
        observer = asyncio.create_task(observe())
        queue = asyncio.Queue()
        for index, case in enumerate(cases): queue.put_nowait((index,case))

        async def worker(number):
            account = credentials['user'+str(number)]
            while not queue.empty() and not halted.is_set():
                index, case = queue.get_nowait()
                room = rooms[case['domain']]
                row = {'index':index, 'id':case['id'], 'domain':case['domain'],
                    'question':case['question'], 'answer':None, 'error':None}
                try:
                    await send(room, account, '!новый')
                    history = []
                    for previous in case.get('previous_questions', []):
                        _, content, seconds = await send(room, account, previous)
                        history.append({'question':previous, 'answer':content['body'], 'seconds':seconds})
                    request_id, content, seconds = await send(room, account, case['question'])
                    answer = content.get('body', '')
                    row.update(answer=answer, formatted_body=content.get('formatted_body'), seconds=seconds,
                        history=history, request_id=request_id, reply_count=len(replies[request_id]),
                        generation_unavailable='Сервис генерации временно недоступен' in answer,
                        validation_fallback='ответ не прошёл проверку подтверждений' in answer,
                        checks=automated_checks(case, answer, []))
                except Exception as exc:
                    row['error'] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                    if row['error'] in {'reply_timeout','observer_failed'}: halted.set()
                rows.append(row)
                checkpoint()
                print(json.dumps({'completed':len(rows), 'total':len(cases), 'id':case['id'],
                    'passed':row.get('checks',{}).get('passed'), 'error':row['error'],
                    'validation_fallback':row.get('validation_fallback'), 'seconds':row.get('seconds')}), flush=True)
                queue.task_done()

        try:
            await asyncio.gather(worker(0),worker(1))
            # Let sync collect immediate duplicates after the final reply.
            await asyncio.sleep(2)
            for row in rows:
                if row.get('request_id'): row['reply_count']=len(replies[row['request_id']])
            checkpoint('complete' if len(rows)==len(cases) else 'incomplete')
        finally:
            halted.set(); observer.cancel()
            try: await observer
            except asyncio.CancelledError: pass
        print(json.dumps(summarize(rows,len(cases))), flush=True)


if __name__ == '__main__':
    asyncio.run(run(Path('/stage')))
