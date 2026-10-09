"""Check the real Flowise API/graph with a temporary fake model; no real LLM key."""
import argparse
import asyncio
import json
import secrets
import sys
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import aiohttp
from aiohttp import web

sys.path.insert(0, '/app')
from flowise_client import FlowiseClient
from setup_flowise import build_flow


async def check_prediction(base, flow_id, prediction_key):
    observed = []

    async def completion(request):
        body = await request.json()
        observed.append(body)
        return web.json_response({
            'id': 'chatcmpl-local-test', 'object': 'chat.completion', 'created': 1,
            'model': 'transport-test-model',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'TRANSPORT_OK [S1]'},
                         'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
        })

    app = web.Application()
    app.router.add_post('/v1/chat/completions', completion)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        # Container network only; this port is not published on the host.
        await web.TCPSite(runner, '0.0.0.0', 18089).start()
        async with aiohttp.ClientSession() as http:
            async with http.post(base + '/prediction/' + flow_id, json={'question': 'auth test'}) as response:
                # 3.1.4 wraps Unauthorized as HTTP 500; verify no model invocation occurred.
                assert response.status >= 400 and not observed, response.status
                print('UNAUTHENTICATED_BLOCKED', response.status)
            client = FlowiseClient('http://flowise:3000', {'flowise'},
                                   {'!pilot:demo.local': {'domain': 'legal', 'flow_id': flow_id}}, prediction_key)
            result = await client.predict(http, 'legal', '!pilot:demo.local', '@tester:demo.local', '$event',
                                          [{'role': 'user', 'content': 'Retain this citation [S1]'}])
            assert result == 'TRANSPORT_OK [S1]', repr(result)
            assert len(observed) == 1
            assert observed[0]['model'] == 'transport-test-model'
            assert '[S1]' in json.dumps(observed[0]['messages'])
            print('FLOWISE_TRANSPORT_PASS (mock model, real Flowise API and graph)')
    finally:
        await runner.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', default='/app/state')
    args = parser.parse_args()
    admin = json.loads((Path(args.state_dir) / 'flowise_admin.json').read_text())
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    base = 'http://flowise:3000/api/v1'

    def api(method, path, body=None):
        request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={'Content-Type': 'application/json', 'x-request-from': 'internal'}, method=method)
        try:
            with opener.open(request, timeout=30) as response:
                data = response.read()
                return json.loads(data) if data else None
        except HTTPError as error:
            raise RuntimeError('Flowise test API ' + path + ': HTTP ' + str(error.code)) from None

    api('POST', '/auth/login', admin)
    definitions = {name: api('GET', '/nodes/' + name) for name in ('promptTemplate', 'chatOpenAI', 'llmChain')}
    name = 'STS temporary transport test ' + secrets.token_hex(4)
    key = credential = flow = None
    try:
        keys = api('POST', '/apikey', {'keyName': name, 'permissions': ['chatflows:execute']})
        if isinstance(keys, dict):
            keys = keys['data']
        key = next(k for k in keys if k['keyName'] == name)
        credential = api('POST', '/credentials', {'name': name, 'credentialName': 'openAIApi',
                                                 'plainDataObj': {'openAIApiKey': 'not-a-real-key'}})
        graph = build_flow(definitions, credential['id'], 'transport-test-model', 'http://bot:18089/v1', 'legal')
        flow = api('POST', '/chatflows', {'name': name, 'flowData': json.dumps(graph), 'type': 'CHATFLOW',
                                         'deployed': True, 'isPublic': False, 'apikeyid': key['id']})
        asyncio.run(check_prediction(base, flow['id'], key['apiKey']))
    finally:
        # Attempt every cleanup even if a previous deletion fails.
        errors = []
        for resource, value in [('chatflows', flow), ('credentials', credential), ('apikey', key)]:
            if value:
                try:
                    api('DELETE', '/' + resource + '/' + value['id'])
                except RuntimeError as error:
                    errors.append(str(error))
        if errors:
            raise RuntimeError('Temporary object cleanup incomplete: ' + '; '.join(errors))
        print('Temporary flow, dummy credential and prediction key removed.')


if __name__ == '__main__':
    main()
