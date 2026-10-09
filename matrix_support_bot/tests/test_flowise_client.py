import asyncio
import json

import pytest

from flowise_client import FlowiseClient, FlowiseError


ROOM = '!pilot:demo.local'
FLOW = 'b0849029-67e9-4434-8c41-e7e72edc171c'


def client():
    return FlowiseClient('http://flowise:3000', {'flowise'},
                         {ROOM: {'domain': 'legal', 'flow_id': FLOW}}, 'test-prediction-key')


class Response:
    def __init__(self, status=200, chunks=None):
        self.status, self.chunks = status, chunks or [b'{"text":"answer"}']
        self.content = self

    async def iter_chunked(self, size):
        for chunk in self.chunks:
            yield chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class HTTP:
    def __init__(self, response=None):
        self.response, self.calls = response or Response(), []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def predict(http, room=ROOM, domain='legal', sender='@user:demo.local', event='$test'):
    return asyncio.run(client().predict(http, domain, room, sender, event,
                                        [{'role': 'user', 'content': 'question [S1]'}]))


@pytest.mark.parametrize('url', ['http://elsewhere:3000', 'https://flowise.evil',
                                'http://user:pass@flowise', 'http://flowise/?key=x', 'file:///secret'])
def test_rejects_unapproved_destinations(url):
    with pytest.raises(FlowiseError):
        FlowiseClient(url, {'flowise'}, {}, 'test')


def test_unconfigured_rooms_and_wrong_specialist_never_make_a_request():
    http = HTTP()
    assert not client().selected('!main:demo.local', 'legal')
    for room, domain in [('!main:demo.local', 'legal'), (ROOM, 'finance')]:
        with pytest.raises(FlowiseError):
            predict(http, room, domain)
    assert not http.calls


def test_chunked_body_auth_and_opaque_request_session():
    http = HTTP(Response(chunks=[b'{"te', b'xt":"answer"}']))
    assert predict(http) == 'answer'
    url, request = http.calls[0]
    assert url.endswith('/prediction/' + FLOW)
    assert request['allow_redirects'] is False
    assert request['headers']['Authorization'] == 'Bearer test-prediction-key'
    assert request['json']['question'] == 'USER:\nquestion [S1]'
    first = request['json']['overrideConfig']['sessionId']
    assert len(first) == 64 and '@user' not in json.dumps(request['json'])
    predict(http, sender='@another:demo.local')
    assert http.calls[-1][1]['json']['overrideConfig']['sessionId'] != first
    predict(http, event='$another')
    assert http.calls[-1][1]['json']['overrideConfig']['sessionId'] != first


@pytest.mark.parametrize('response', [Response(401), Response(302), Response(500),
                                       Response(chunks=[b'x' * (2 * 1024 * 1024), b'x']),
                                       Response(chunks=[b'{"text":""}']), Response(chunks=[b'[]'])])
def test_transport_and_invalid_answers_fail_without_fallback_or_secret_output(response):
    with pytest.raises(FlowiseError) as error:
        predict(HTTP(response))
    assert 'test-prediction-key' not in str(error.value)


def test_configuration_is_opt_in_and_requires_key(tmp_path):
    assert FlowiseClient.from_env({}) is None
    routes = tmp_path / 'routes.json'
    routes.write_text('{}')
    env = {'FLOWISE_ROOM_ROUTES_FILE': str(routes)}
    assert FlowiseClient.from_env(env) is None
    routes.write_text(json.dumps({ROOM: {'domain': 'legal', 'flow_id': FLOW}}))
    with pytest.raises(FlowiseError):
        FlowiseClient.from_env(env)
    key = tmp_path / 'key'
    key.write_text('test')
    env['FLOWISE_API_KEY_FILE'] = str(key)
    assert FlowiseClient.from_env(env).selected(ROOM, 'legal')


def test_malformed_flow_id_rejected():
    with pytest.raises(FlowiseError):
        FlowiseClient('http://flowise', {'flowise'},
                      {ROOM: {'domain': 'legal', 'flow_id': '-' * 36}}, 'test')
