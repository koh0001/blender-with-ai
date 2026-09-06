"""Offline HTTP contract and credential/cancellation boundary tests."""
import importlib
import io
import json
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest import mock
from urllib.error import HTTPError

package = types.ModuleType('twin_api_tests')
package.__path__ = [str(Path(__file__).parents[1] / 'twin_assistant')]
sys.modules['twin_api_tests'] = package
api = importlib.import_module('twin_api_tests.api_runtime')

COMMAND = {'operation': 'create', 'target': '', 'name': 'Cube',
           'primitive': 'CUBE', 'vector': [0, 0, 0]}


class APITests(unittest.TestCase):
    def make(self, provider='openai'):
        client = api.APIRuntime(provider, 'test-secret-never-log')
        self.addCleanup(client.close)
        return client

    def collect(self, client, worker):
        worker.join(3)
        self.assertFalse(worker.is_alive())
        events = []
        while not client.events.empty():
            events.append(client.events.get_nowait())
        self.assertEqual(sum(e['type'] == 'action_done' for e in events), 1)
        return events

    def response(self, provider, text=None):
        text = text if text is not None else json.dumps({'message': 'Creating', 'actions': [COMMAND]})
        if provider == 'openai':
            return {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': text}]}]}
        return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': text}]}

    def test_connect_lists_models_without_inference(self):
        for provider, model in [('openai', 'gpt-5-mini'), ('anthropic', 'claude-sonnet-4-6')]:
            with self.subTest(provider=provider):
                client = self.make(provider)
                calls = []
                def http(req, timeout):
                    calls.append(req)
                    return io.BytesIO(json.dumps({'data': [{'id': model}, {'id': 'text-embedding-3-small'},
                        {'id': 'gpt-4o-realtime-preview'}, {'id': 'gpt-4o-2024-05-13'}]}).encode())
                with mock.patch.object(client._opener, 'open', side_effect=http):
                    events = self.collect(client, client.connect())
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].get_method(), 'GET')
                self.assertIsNone(calls[0].data)
                self.assertEqual(next(e['models'] for e in events if e['type'] == 'models'),
                                 [{'model': model, 'displayName': model}])
                self.assertEqual(next(e['account'] for e in events if e['type'] == 'account'), {'type': 'api'})

    def test_provider_payloads_and_scene_response(self):
        for provider, model in [('openai', 'gpt-5-mini'), ('anthropic', 'claude-sonnet-4-6')]:
            with self.subTest(provider=provider):
                client = self.make(provider)
                client._models = {model: {}}
                calls = []
                def http(req, timeout):
                    calls.append(req)
                    return io.BytesIO(json.dumps(self.response(provider)).encode())
                history = [{'role': 'user', 'content': 'hello'}, {'role': 'assistant', 'content': 'hi'}]
                with mock.patch.object(client._opener, 'open', side_effect=http):
                    events = self.collect(client, client.chat('create', [], model, history))
                req = calls[0]
                body = json.loads(req.data)
                self.assertNotIn('tools', body)
                self.assertNotIn('test-secret-never-log', json.dumps(events))
                self.assertNotIn('test-secret-never-log', req.data.decode())
                if provider == 'openai':
                    self.assertEqual(req.full_url, 'https://api.openai.com/v1/responses')
                    self.assertFalse(body['store'])
                    self.assertTrue(body['text']['format']['strict'])
                    self.assertEqual(json.loads(body['input'])['history'], history)
                else:
                    self.assertEqual(req.full_url, 'https://api.anthropic.com/v1/messages')
                    self.assertEqual(req.get_header('Anthropic-version'), '2023-06-01')
                    schema = body['output_config']['format']['schema']
                    self.assertNotIn('maxItems', schema['properties']['actions'])
                    self.assertNotIn('minItems', schema['properties']['actions']['items']['properties']['vector'])
                self.assertEqual(next(e['actions'] for e in events if e['type'] == 'chat'), [COMMAND])

    def test_claude_older_model_uses_validated_json_fallback(self):
        client = self.make('anthropic')
        client._models = {'claude-sonnet-4-20250514': {}}
        with mock.patch.object(client, '_http', return_value=self.response('anthropic')) as http:
            events = self.collect(client, client.chat('create', [], 'claude-sonnet-4-20250514', []))
        body = http.call_args.args[1]
        self.assertNotIn('output_config', body)
        self.assertIn('JSON schema:', body['system'])
        self.assertTrue(any(e['type'] == 'chat' for e in events))

    def test_errors_refusal_tools_duplicate_json_and_incomplete_are_rejected(self):
        bad = [self.response('openai', '{"message":"a","message":"b","actions":[]}'),
               self.response('openai', json.dumps({'message': 'run', 'actions': [dict(COMMAND, operation='python')]})),
               {'status': 'incomplete', 'output': []},
               {'status': 'completed', 'output': [{'type': 'function_call'}]},
               {'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'refusal', 'refusal': 'no'}]}]}]
        client = self.make()
        client._models = {'gpt-5-mini': {}}
        for result in bad:
            with mock.patch.object(client, '_http', return_value=result):
                events = self.collect(client, client.chat('create', [], 'gpt-5-mini', []))
            self.assertTrue(any(e['type'] == 'error' for e in events))
            self.assertFalse(any(e['type'] == 'chat' for e in events))
        secret_error = HTTPError('https://api.openai.com', 401, 'test-secret-never-log', {},
                                 io.BytesIO(b'test-secret-never-log'))
        with mock.patch.object(client._opener, 'open', side_effect=secret_error):
            events = self.collect(client, client.connect())
        self.assertNotIn('test-secret-never-log', json.dumps(events))

    def test_cancel_drops_late_response_and_blocks_overlapping_send(self):
        client = self.make()
        client._models = {'gpt-5-mini': {}}
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            release.wait(2)
            return self.response('openai')
        with mock.patch.object(client, '_http', side_effect=slow):
            worker = client.chat('create', [], 'gpt-5-mini', [])
            self.assertTrue(entered.wait(1))
            client.cancel()
            with self.assertRaises(RuntimeError):
                client.chat('new', [], 'gpt-5-mini', [])
            release.set()
            events = self.collect(client, worker)
        self.assertFalse(any(e['type'] == 'chat' for e in events))
        self.assertTrue(any(e['type'] == 'cancelled' for e in events))
        with mock.patch.object(client, '_http', return_value=self.response('openai')):
            events = self.collect(client, client.chat('new', [], 'gpt-5-mini', []))
        self.assertTrue(any(e['type'] == 'chat' for e in events))

    def test_size_key_endpoint_redirect_and_close_boundaries(self):
        with self.assertRaises(ValueError):
            api.APIRuntime('openai', 'secret\nheader')
        client = self.make()
        with self.assertRaises(ValueError):
            client._http('https://evil.invalid/')
        with self.assertRaises(ValueError):
            api._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.invalid/')
        with mock.patch.object(client._opener, 'open', return_value=io.BytesIO(b'x' * (api.MAX_MESSAGE + 1))):
            events = self.collect(client, client.connect())
        self.assertTrue(any(e['type'] == 'error' for e in events))
        with self.assertRaises(ValueError):
            client.chat('x' * 8001, [], 'gpt-5-mini', [])
        client.close()
        self.assertEqual(client._api_key, '')
        with self.assertRaises(RuntimeError):
            client.connect()


if __name__ == '__main__':
    unittest.main()
