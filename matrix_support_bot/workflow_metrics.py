"""Allowlisted numeric Flowise timings; no prompts, identities or node labels."""
import math
import json

STAGES = {
    'startAgentflow_0': 'start', 'customFunctionAgentflow_0': 'prepare',
    'agentAgentflow_0': 'draft', 'customFunctionAgentflow_1': 'unitize',
    'llmAgentflow_0': 'audit', 'customFunctionAgentflow_2': 'gate',
    'conditionAgentflow_0': 'decision', 'directReplyAgentflow_0': 'reply',
    'llmAgentflow_1': 'repair', 'customFunctionAgentflow_3': 'unitize_repaired',
    'llmAgentflow_2': 'audit_repaired', 'customFunctionAgentflow_4': 'gate_repaired',
    'directReplyAgentflow_1': 'reply_repaired',
}
MODEL_STAGES = {'draft', 'audit', 'repair', 'audit_repaired'}


def bounded_number(value, maximum):
    return type(value) in {int, float} and math.isfinite(value) and 0 <= value <= maximum


def execution_metrics(result):
    """Flowise components output timeMetadata.delta in milliseconds.

    Missing/invalid durations remain missing, never zero. This function neither
    persists nor returns any content from node input/output apart from timings.
    """
    nodes = result.get('agentFlowExecutedData') if isinstance(result, dict) else None
    if not isinstance(nodes, list) or len(nodes) > 64:
        return {'available': False, 'stages': {}, 'model_nodes_executed': None, 'repair_used': None}
    stages = {}
    for node in nodes:
        if not isinstance(node, dict) or node.get('nodeId') not in STAGES:
            continue
        stage = STAGES[node['nodeId']]
        values = stages.setdefault(stage, {'calls': 0, 'timed_calls': 0, 'milliseconds': None})
        values['calls'] += 1
        data = node.get('data')
        output = data.get('output') if isinstance(data, dict) else None
        timing = output.get('timeMetadata') if isinstance(output, dict) else None
        # Flowise serializes our CustomFunction result into output.content.
        # Parse only our known custom stages, never model prose or nested state.
        if timing is None and stage in {'prepare', 'unitize', 'gate', 'unitize_repaired', 'gate_repaired'} and isinstance(output, dict):
            content = output.get('content')
            if isinstance(content, str) and len(content) <= 512000:
                try:
                    value = json.loads(content)
                    timing = value.get('timeMetadata') if isinstance(value, dict) else None
                except (ValueError, RecursionError):
                    pass
        duration = timing.get('delta') if isinstance(timing, dict) else None
        if bounded_number(duration, 3600000):
            values['timed_calls'] += 1
            values['milliseconds'] = round((values['milliseconds'] or 0) + duration, 3)
    return {'available': bool(stages), 'stages': stages,
            'model_nodes_executed': sum(v['calls'] for k, v in stages.items() if k in MODEL_STAGES) if stages else None,
            'repair_used': 'repair' in stages if stages else None}


class PredictionText(str):
    def __new__(cls, text, metrics):
        value = super().__new__(cls, text)
        value.workflow_metrics = metrics
        return value
