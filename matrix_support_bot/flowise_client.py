"""Opt-in Flowise predictions for explicitly configured Matrix rooms."""
import hashlib
import json
import uuid
from pathlib import Path
from urllib.parse import urlsplit


class FlowiseError(ValueError):
    pass


def _valid_id(value):
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


class FlowiseClient:
    def __init__(self, url, allowed_hosts, routes, api_key, timeout=75):
        parsed = urlsplit(url)
        if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in allowed_hosts
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {'', '/'}):
            raise FlowiseError('Invalid or unapproved Flowise URL')
        if not isinstance(routes, dict):
            raise FlowiseError('Flowise room routes must be an object')
        for room, entry in routes.items():
            if (not isinstance(room, str) or not room.startswith('!') or ':' not in room
                    or not isinstance(entry, dict) or entry.get('domain') not in {'legal', 'finance'}
                    or not _valid_id(entry.get('flow_id'))):
                raise FlowiseError('Invalid Flowise room route')
        if not api_key.strip():
            raise FlowiseError('A Flowise API key is required')
        self.url, self.routes, self.api_key, self.timeout = url.rstrip('/'), routes, api_key.strip(), timeout

    @classmethod
    def from_env(cls, env):
        path = env.get('FLOWISE_ROOM_ROUTES_FILE')
        if not path or not Path(path).exists():
            return None
        routes = json.loads(Path(path).read_text())
        if routes == {}:
            return None
        key_path = env.get('FLOWISE_API_KEY_FILE', '')
        if not key_path or not Path(key_path).is_file():
            raise FlowiseError('Missing Flowise API key file')
        return cls(env.get('FLOWISE_URL', 'http://flowise:3000'),
                   set(env.get('FLOWISE_ALLOWED_HOSTS', 'flowise').split(',')),
                   routes, Path(key_path).read_text())

    def selected(self, room, domain):
        return room in self.routes and self.routes[room]['domain'] == domain

    async def predict(self, http, domain, room, sender, event_id, messages):
        if not self.selected(room, domain):
            raise FlowiseError('Room/domain not enabled for Flowise')
        # No Matrix identifiers or other users' history are sent to Flowise.
        # The flow has no memory node; the bot supplies its scoped context.
        session = hashlib.sha256(('\0'.join([room, sender, event_id])).encode()).hexdigest()
        prompt = '\n\n'.join(m['role'].upper() + ':\n' + m['content'] for m in messages)
        body = {'question': prompt, 'streaming': False, 'overrideConfig': {'sessionId': session}}
        async with http.post(self.url + '/api/v1/prediction/' + self.routes[room]['flow_id'],
                             json=body, headers={'Authorization': 'Bearer ' + self.api_key},
                             allow_redirects=False, timeout=self.timeout) as response:
            if response.status != 200:
                raise FlowiseError('Flowise prediction failed (HTTP ' + str(response.status) + ')')
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > 2 * 1024 * 1024:
                    raise FlowiseError('Flowise response exceeds size limit')
            result = json.loads(data)
        text = result.get('text') if isinstance(result, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > 60000:
            raise FlowiseError('Flowise returned an invalid answer')
        return text.strip()
