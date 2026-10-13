#!/usr/bin/env python3
"""Run inside the bot container; bootstrap an isolated Flowise pilot, no secret output.

Uses a dedicated Flowise LiteLLM key file supplied explicitly by the operator.
Never reads the bot/provider/master key. Does not enable any room.
"""
import argparse
import json
import os
import secrets
import sys
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as file:
        json.dump(value, file, ensure_ascii=False, indent=2)


def build_flow(definitions, credential_id, model, base_url, domain):
    """Construct the same stateless Prompt -> ChatOpenAI -> LLM Chain as in the UI."""
    inputs = {
        'promptTemplate': {'template': '{request}', 'promptValues': '{"request":"{{question}}"}'},
        'chatOpenAI': {'modelName': model, 'basepath': base_url, 'temperature': 0.1,
                       'maxTokens': 2300, 'timeout': 65000, 'streaming': False},
        'llmChain': {'model': '{{chatOpenAI_0.data.instance}}',
                     'prompt': '{{promptTemplate_0.data.instance}}',
                     'outputParser': '', 'chainName': domain},
    }
    nodes = []
    for i, (name, values) in enumerate(inputs.items()):
        node_id = name + '_0'
        definition = definitions[name]
        schema = definition.get('inputs', [])
        primitive = {'string', 'number', 'boolean', 'options', 'asyncOptions', 'json', 'code', 'password'}
        data = dict(definition, id=node_id, inputs=values, outputs={})
        data['inputParams'] = [dict(item) for item in schema if item['type'] in primitive]
        data['inputAnchors'] = [dict(item) for item in schema if item['type'] not in primitive]
        output_type = ' | '.join(definition['baseClasses'])
        data['outputAnchors'] = [{'name': name, 'label': definition['label'], 'type': output_type}]
        if name == 'chatOpenAI':
            data['credential'] = credential_id
        if name == 'llmChain':
            data['outputs'] = {'output': 'llmChain'}
        for key in ('inputParams', 'inputAnchors', 'outputAnchors'):
            data[key] = [dict(item, id=node_id + ('-output-' if key == 'outputAnchors' else '-input-')
                              + item['name'] + '-' + item['type'].replace(' ', ''))
                         for item in data.get(key, [])]
        if name == 'llmChain':
            data['outputAnchors'] = [{'name': 'output', 'label': 'Output', 'type': 'options',
                                     'options': data['outputAnchors'], 'default': 'llmChain'}]
        nodes.append({'id': node_id, 'type': 'customNode', 'position': {'x': i * 380, 'y': 80}, 'data': data})
    edges = []
    for name, target in [('promptTemplate', 'prompt'), ('chatOpenAI', 'model')]:
        node = next(n for n in nodes if n['id'] == name + '_0')
        target_node = nodes[-1]
        output = node['data']['outputAnchors'][0]['id']
        anchor = next(a['id'] for a in target_node['data']['inputAnchors'] if a['name'] == target)
        edges.append({'id': name + '-chain', 'source': node['id'], 'target': 'llmChain_0',
                      'sourceHandle': output, 'targetHandle': anchor, 'type': 'buttonedge', 'data': {'label': ''}})
    return {'nodes': nodes, 'edges': edges}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://flowise:3000')
    parser.add_argument('--state-dir', default='/app/state')
    parser.add_argument('--prepare-only', action='store_true', help='Create admin only; do not read any LLM key')
    parser.add_argument('--llm-key-file', help='Dedicated LiteLLM virtual key explicitly provided for Flowise')
    args = parser.parse_args()
    # This tool is intentionally limited to the internal service.
    if args.url.rstrip('/') != 'http://flowise:3000':
        parser.error('Only http://flowise:3000 is supported')
    sys.path.insert(0, '/app')
    from llm_settings import model_for_domain
    from flowise_profiles import load_profiles, stage_graph
    state = Path(args.state_dir)
    admin_path = state / 'flowise_admin.json'
    if admin_path.exists():
        admin = json.loads(admin_path.read_text())
    else:
        admin = {'email': 'admin@sts.local', 'password': secrets.token_urlsafe(32)}
        private_json(admin_path, admin)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def api(method, path, body=None):
        request = Request(args.url.rstrip('/') + '/api/v1' + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={'Content-Type': 'application/json', 'x-request-from': 'internal'}, method=method)
        try:
            with opener.open(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            # Do not echo server bodies: they may contain credentials.
            raise RuntimeError('Flowise API ' + path + ' returned HTTP ' + str(error.code)) from None

    marker = state / 'flowise_setup.json'
    if not marker.exists():
        # Registration can already have completed if an earlier bootstrap was interrupted.
        try:
            api('POST', '/account/register', {'user': {'name': 'STS Flowise admin', 'email': admin['email'],
                                                      'credential': admin['password']}})
        except RuntimeError:
            pass  # The subsequent login must succeed; errors are not silently accepted.
    api('POST', '/auth/login', admin)
    if args.prepare_only:
        print('Flowise admin ready. No LLM key read or transferred.')
        return
    progress = json.loads(marker.read_text()) if marker.exists() else {'flows': {}, 'review_flows': {}, 'version': '3.1.4'}
    if (all(domain in progress['flows'] for domain in ('legal', 'finance'))
            and ('review_flows' not in progress or all(d in progress['review_flows'] for d in ('legal', 'finance')))):
        print('Flowise already provisioned; existing flows preserved.')
        return
    if not args.llm_key_file or 'litellm' not in os.environ.get('LLM_ENDPOINT', ''):
        raise RuntimeError('Provide --llm-key-file with a dedicated Flowise LiteLLM virtual key')
    llm_key = Path(args.llm_key_file).read_text().strip()
    if not llm_key:
        raise RuntimeError('Empty dedicated Flowise key')
    if 'prediction_key_id' not in progress:
        keys = api('POST', '/apikey', {'keyName': 'STS Matrix prediction', 'permissions': ['chatflows:execute']})
        if isinstance(keys, dict):
            keys = keys.get('data', [])
        prediction_key = next(k for k in keys if k['keyName'] == 'STS Matrix prediction')
        key_path = state / 'flowise_api_key'
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as file:
            file.write(prediction_key['apiKey'])
        progress['prediction_key_id'] = prediction_key['id']
        private_json(marker, progress)
    if 'credential_id' not in progress:
        credential = api('POST', '/credentials', {'name': 'STS dedicated Flowise LiteLLM key', 'credentialName': 'openAIApi',
                                                 'plainDataObj': {'openAIApiKey': llm_key}})
        progress['credential_id'] = credential['id']
        private_json(marker, progress)
    definitions = {name: api('GET', '/nodes/' + name) for name in ('promptTemplate', 'chatOpenAI', 'llmChain')}
    profiles = load_profiles()['domains']
    for domain, label in [('legal', 'ЮрАгент'), ('finance', 'ФинАгент')]:
        graph = build_flow(definitions, progress['credential_id'], model_for_domain(domain), os.environ['LLM_ENDPOINT'], domain)
        for stage, mapping in [('draft', 'flows'), ('review', 'review_flows')]:
            if mapping not in progress or domain in progress[mapping]:
                continue
            flow = stage_graph(graph, profiles[domain][stage])
            suffix = ' — проверка' if stage == 'review' else ''
            created = api('POST', '/chatflows', {'name': 'STS Flowise — ' + label + suffix, 'flowData': json.dumps(flow),
                                                'type': 'CHATFLOW', 'deployed': True, 'isPublic': False,
                                                'apikeyid': progress['prediction_key_id']})
            progress[mapping][domain] = created['id']
            private_json(marker, progress)
    print('Flowise provisioned: legal and finance flows. No Matrix rooms enabled yet.')


if __name__ == '__main__':
    main()
