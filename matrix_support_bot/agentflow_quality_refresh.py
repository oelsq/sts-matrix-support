"""Refresh the known 13-node claim pipeline without changing its IDs or models."""
import copy
import json
from agentflow_quality_graph import build_quality_agentflow
from agentflow_release import NAMES
from agentflow_upgrade import digest, payload


def refresh_plan(routes, api):
    grouped = {}
    for route in routes.values():
        if route.get('engine') != 'agentflow_v2' or route.get('quality_contract') != 'sts.claims.v1':
            raise ValueError('Expected existing claim-audited routes')
        grouped.setdefault(route['domain'], set()).add(route['flow_id'])
    if set(grouped) != {'legal', 'finance'} or any(len(ids) != 1 for ids in grouped.values()):
        raise ValueError('Expected one claim workflow per specialist')
    definitions = {name: api('GET', '/nodes/' + name) for name in NAMES}
    plan = {'version': 1, 'status': 'planned', 'routes_digest': digest(routes), 'flows': {}}
    for domain, ids in grouped.items():
        ident = next(iter(ids))
        before = payload(api('GET', '/chatflows/' + ident))
        if before['type'] != 'AGENTFLOW' or before['isPublic'] or not before['apikeyid']:
            raise ValueError('Expected a protected Agentflow')
        graph = json.loads(before['flowData'])
        by_id = {node['id']: node for node in graph['nodes']}
        if len(by_id) != len(graph['nodes']) or 'agentAgentflow_0' not in by_id:
            raise ValueError('Unknown workflow nodes')
        model = by_id['agentAgentflow_0']['data']['inputs']['agentModelConfig']
        fresh = build_quality_agentflow(definitions, domain, model)
        expected = {node['id']: node for node in fresh['nodes']}
        wiring = lambda value: {(e['source'], e['sourceHandle'], e['target']) for e in value['edges']}
        if set(by_id) != set(expected) or wiring(graph) != wiring(fresh) or len(graph['edges']) != len(fresh['edges']):
            raise ValueError('Unknown claim workflow topology')
        for node_id, node in by_id.items():
            values = node['data']['inputs']
            if node['data']['name'] != expected[node_id]['data']['name']:
                raise ValueError('Unexpected node type')
            if values.get('agentTools') or values.get('agentEnableMemory') or values.get('llmEnableMemory'):
                raise ValueError('Stateful or tool-enabled workflow needs separate review')
        start = by_id['startAgentflow_0']['data']['inputs']
        prepare = by_id['customFunctionAgentflow_0']['data']['inputs']['customFunctionJavascriptFunction']
        if not start.get('startEphemeralMemory') or start.get('startPersistState') or 'sts.claims.v1' not in prepare:
            raise ValueError('Expected ephemeral quality request scope')
        after_graph = copy.deepcopy(graph)
        # Preserve visual layout, credentials, per-stage models, routing and state keys.
        for node in after_graph['nodes']:
            node_id = node['id']
            if node_id in {'customFunctionAgentflow_1', 'customFunctionAgentflow_2', 'customFunctionAgentflow_3', 'customFunctionAgentflow_4'}:
                key = 'customFunctionJavascriptFunction'
            elif node_id in {'llmAgentflow_0', 'llmAgentflow_1', 'llmAgentflow_2'}:
                key = 'llmMessages'
            else:
                continue
            node['data']['inputs'][key] = copy.deepcopy(expected[node_id]['data']['inputs'][key])
        after_graph['nodes'][-1]['data']['label']='Итог или запрос повторного поиска'
        plan['flows'][domain] = {'id': ident, 'before': before, 'after': dict(before, flowData=json.dumps(after_graph, ensure_ascii=False))}
    return plan
