"""Claude contract tests without login or model traffic."""
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

package = types.ModuleType('twin_claude_tests')
package.__path__ = [str(Path(__file__).parents[1] / 'blender_with_ai')]
sys.modules[package.__name__] = package
claude = importlib.import_module('twin_claude_tests.claude_runtime')

AUTH = json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'subscriptionType': 'max',
                   'email': 'never-show@example.com', 'token': 'secret'})
ANSWER = json.dumps({'type': 'result', 'is_error': False,
                     'structured_output': {'message': '안녕하세요', 'actions': []}})


class ClaudeRuntimeTests(unittest.TestCase):
    def events(self, runtime):
        return list(runtime.events.queue)

    def test_account_omits_identifiers_and_aliases_labelled(self):
        runtime = claude.ClaudeRuntime()
        with patch.object(runtime, '_run', return_value=AUTH):
            runtime._connect()
        events = self.events(runtime)
        self.assertNotIn('secret', json.dumps(events))
        self.assertNotIn('never-show', json.dumps(events))
        self.assertEqual(runtime._account, {'type': 'claude', 'planType': 'max'})
        self.assertTrue(all('alias' in m['displayName'] for m in events[1]['models']))

    def test_console_auth_cannot_send_inference(self):
        runtime = claude.ClaudeRuntime()
        with patch.object(runtime, '_run', return_value=json.dumps({'loggedIn': True, 'authMethod': 'api_key'})) as run:
            with self.assertRaises(RuntimeError):
                runtime._chat('hello', [], 'sonnet', [])
        self.assertEqual(run.call_count, 1)

    def test_tools_customizations_mcp_and_persistence_disabled(self):
        runtime = claude.ClaudeRuntime()
        with patch.object(runtime, '_run', side_effect=[AUTH, ANSWER]) as run:
            runtime._chat('hello', [], 'sonnet', [])
        argv, payload, _ = run.call_args.args
        self.assertIn('--safe-mode', argv)
        self.assertIn('--no-session-persistence', argv)
        self.assertIn('--strict-mcp-config', argv)
        self.assertEqual(argv[argv.index('--tools') + 1], '')
        self.assertEqual(json.loads(argv[argv.index('--mcp-config') + 1]), {'mcpServers': {}})
        self.assertEqual(json.loads(payload)['instruction'], 'hello')
        self.assertEqual(self.events(runtime)[-1]['type'], 'chat')

    def test_invalid_output_never_emits_raw_output_or_actions(self):
        runtime = claude.ClaudeRuntime()
        with patch.object(runtime, '_run', side_effect=[AUTH, '{secret raw output']):
            worker = runtime.chat('hello', [], 'sonnet')
            worker.join(2)
        events = self.events(runtime)
        self.assertNotIn('secret raw', json.dumps(events))
        self.assertFalse(any(e['type'] == 'chat' for e in events))
        self.assertEqual(events[-1]['type'], 'action_done')

    def test_environment_removes_api_and_cloud_routing(self):
        with patch.dict(claude.os.environ, {'ANTHROPIC_API_KEY': 'secret', 'ANTHROPIC_BASE_URL': 'evil',
              'CLAUDE_CODE_OAUTH_TOKEN': 'secret', 'CLAUDE_CODE_USE_BEDROCK': '1', 'AWS_PROFILE': 'billing',
              'PATH': '/keep'}, clear=True):
            self.assertEqual(claude.subscription_environment(), {'PATH': '/keep'})

    def test_official_subscription_login_and_cancellation(self):
        runtime = claude.ClaudeRuntime()
        with patch.object(runtime, '_run', side_effect=['', AUTH]) as run:
            runtime._login()
        self.assertEqual(run.call_args_list[0].args[0], ['auth', 'login', '--claudeai'])
        runtime.cancel_login()
        self.assertTrue(runtime._cancel.is_set())
        runtime.close()
        self.assertTrue(runtime._closed)

    def test_real_subprocess_output_bound_and_cancellation(self):
        runtime = claude.ClaudeRuntime(sys.executable)
        self.assertEqual(runtime._run(['-c', "print('ok')"], timeout=5).strip(), 'ok')
        with patch.object(claude, 'MAX_MESSAGE', 128), self.assertRaises(RuntimeError):
            runtime._run(['-c', "print('x' * 1000)"], timeout=5)
        worker = runtime._action(runtime._run, ['-c', 'import time; time.sleep(30)'])
        runtime.cancel()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(any(event['type'] == 'cancelled' for event in self.events(runtime)))
        self.assertIsNone(runtime._process)

    def test_duplicate_keys_rejected_in_both_output_formats(self):
        for duplicate in ('\"operation\":\"move\",\"operation\":\"rotate\",\"target\":\"Cube\"',
                          '\"operation\":\"move\",\"target\":\"Cube\",\"target\":\"Cube\"'):
            invalid = '{"message":"hi","actions":[{' + duplicate + ',"name":"","primitive":"CUBE","vector":[1,0,0]}]}'
            envelopes = ['{"structured_output":' + invalid + '}', json.dumps({'result': invalid})]
            for envelope in envelopes:
                with self.subTest(envelope=envelope):
                    runtime = claude.ClaudeRuntime()
                    with patch.object(runtime, '_run', side_effect=[AUTH, envelope]):
                        with self.assertRaises(RuntimeError):
                            runtime._chat('hello', [{'object_name': 'Cube'}], 'sonnet', [])
                    self.assertFalse(any(event['type'] == 'chat' for event in self.events(runtime)))
