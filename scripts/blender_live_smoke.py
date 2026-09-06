"""Opt-in live Codex test in a disposable factory-startup Blender scene.

Uses the existing ChatGPT login and two model turns. Never logs credentials,
changes login, or saves a blend file. Run with --background --factory-startup.
"""
import argparse
import queue
import sys
from pathlib import Path
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blender_with_ai.runtime import CodexRuntime
from blender_with_ai.claude_runtime import ClaudeRuntime
from blender_with_ai.scene_actions import snapshot, apply_actions


def collect(client, worker):
    worker.join(240)
    if worker.is_alive():
        client.cancel()
        raise RuntimeError('Live test timed out')
    events = []
    while True:
        try:
            events.append(client.events.get_nowait())
        except queue.Empty:
            break
    errors = [e['message'] for e in events if e['type'] == 'error']
    if errors:
        raise RuntimeError('; '.join(errors))
    return events


parser = argparse.ArgumentParser()
parser.add_argument('--provider', choices=('codex', 'claude'), default='codex')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
client = ClaudeRuntime() if args.provider == 'claude' else CodexRuntime()
try:
    events = collect(client, client.connect())
    models = next(e['models'] for e in events if e['type'] == 'models')
    candidates = [m['model'] for m in models]
    if not candidates:
        raise RuntimeError('No account models available')
    model = 'gpt-5.6-luna' if 'gpt-5.6-luna' in candidates else candidates[0]
    print('LIVE_MODEL:', model, flush=True)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    prompt = '원점에 큐브 하나를 생성하고 이름을 GPT_Live_Cube로 해줘. 다른 작업은 하지 마.'
    records, saved = snapshot(bpy.context)
    events = collect(client, client.chat(prompt, records, model, []))
    response = next(e for e in events if e['type'] == 'chat')
    assert len(response['actions']) == 1 and response['actions'][0]['operation'] == 'create', response
    summary = apply_actions(bpy.context, response['actions'], saved)
    obj = bpy.data.objects['GPT_Live_Cube']
    assert tuple(obj.location) == (0, 0, 0)
    print('LIVE_CREATE_OK', flush=True)
    history = [{'role': 'user', 'content': prompt},
               {'role': 'assistant', 'content': response['message'] + '\nActual execution: ' + summary}]
    records, saved = snapshot(bpy.context)
    events = collect(client, client.chat('방금 만든 선택 객체를 X축으로 2만큼 옮겨줘. 다른 작업은 하지 마.', records, model, history))
    response = next(e for e in events if e['type'] == 'chat')
    assert len(response['actions']) == 1 and response['actions'][0]['operation'] == 'move', response
    apply_actions(bpy.context, response['actions'], saved)
    assert tuple(obj.location) == (2, 0, 0), tuple(obj.location)
    print('LIVE_FOLLOWUP_MOVE_OK', flush=True)
    print('BLENDER_LIVE_SMOKE_OK', flush=True)
finally:
    client.close()
