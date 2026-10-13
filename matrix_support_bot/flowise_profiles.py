"""Versioned stateless Flowise instruction profiles; no credentials or user data."""
import copy
import json
import math
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name('agents') / 'flowise_profiles.json'


def load_profiles(path=None):
    value = json.loads(Path(path or DEFAULT_PATH).read_text())
    if value.get('version') != 1 or set(value.get('domains', {})) != {'legal', 'finance'}:
        raise ValueError('Invalid Flowise profile domains/version')
    for stages in value['domains'].values():
        if set(stages) != {'draft', 'review'}:
            raise ValueError('Both draft and review profiles required')
        for profile in stages.values():
            if set(profile) != {'instructions', 'temperature', 'max_tokens'}:
                raise ValueError('Unknown profile field')
            instructions = profile['instructions']
            if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 12000:
                raise ValueError('Invalid profile instructions')
            temperature, limit = profile['temperature'], profile['max_tokens']
            if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                    or not math.isfinite(temperature) or not 0 <= temperature <= 1
                    or isinstance(limit, bool) or not isinstance(limit, int) or not 256 <= limit <= 4000):
                raise ValueError('Invalid profile generation limits')
    return value


def stage_graph(graph, profile):
    """Keep model, credentials, endpoints, node IDs and wiring; change only instructions/limits."""
    result = copy.deepcopy(graph)
    nodes = {n['data']['name']: n for n in result['nodes']}
    if len(result['nodes']) != 3 or set(nodes) != {'promptTemplate', 'chatOpenAI', 'llmChain'}:
        raise ValueError('Only the known stateless three-node graph can be upgraded')
    prompt = nodes['promptTemplate']['data']['inputs']
    if prompt.get('promptValues') != '{"request":"{{question}}"}':
        raise ValueError('Unexpected prompt variable mapping; preserve manual graph changes')
    # LangChain template braces in administrator prose must stay literal.
    instructions = profile['instructions'].replace('{', '{{').replace('}', '}}')
    prompt['template'] = instructions + '\n\n{request}'
    model = nodes['chatOpenAI']['data']['inputs']
    model.update(temperature=profile['temperature'], maxTokens=profile['max_tokens'])
    return result
