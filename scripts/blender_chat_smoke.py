"""Offline UI/chat smoke; no account, network, or inference."""
import sys
import queue
import threading
import types
import unicodedata
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
import blender_with_ai as addon

class FakeRuntime:
    def __init__(self):
        self.events = queue.Queue()
        self._cancel = threading.Event()
        self.calls = []
    def chat(self, prompt, objects, model, history):
        self.calls.append((prompt, objects, model, history))
    def cancel(self):
        self._cancel.set()
    def close(self):
        pass

addon.register()
# Examples only seed the composer; they must never connect or execute.
count_before = len(bpy.data.objects)
assert bpy.ops.twin.seed_prompt(prompt='원점에 큐브를 만들어줘.') == {'FINISHED'}
assert bpy.context.window_manager.twin_prompt == '원점에 큐브를 만들어줘.'
assert addon._runtime is None and not addon._busy
assert len(bpy.data.objects) == count_before
# Korean, English and long unbroken names fit bounded native labels.
text = '긴이름' * 15 + ' mixed English request for selected objects'
lines = addon.wrapped_lines(text, 24)
assert ''.join(lines).replace(' ', '') == text.replace(' ', '')
assert all(sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in line) <= 24 for line in lines)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
r = FakeRuntime()
addon._runtime = r
addon._account = True
r.events.put({'type': 'models', 'models': [{'model': 'fake', 'displayName': 'Fake'}]})
addon.poll_events()
scene = bpy.context.scene
bpy.context.window_manager.twin_prompt = 'Create a cube'
assert bpy.ops.twin.propose() == {'FINISHED'}
assert r.calls[-1][1] == []
assert addon._busy
assert not addon.TWIN_OT_connect.poll(bpy.context)
assert not addon.TWIN_OT_login.poll(bpy.context)
command = {'operation':'create','target':'','name':'ChatCube','primitive':'CUBE','vector':[0,0,0]}
r.events.put({'type':'chat','message':'큐브 생성 요청','actions':[command]})
addon.poll_events()
assert addon._busy, 'response must not release busy before action_done'
assert bpy.data.objects.get('ChatCube') is not None
assert len(bpy.context.window_manager.twin_chat) == 2
assert '실제 실행 결과' in bpy.context.window_manager.twin_chat[-1].content
assert bpy.context.window_manager.twin_chat[-1].outcome == 'APPLIED'
assert bpy.context.window_manager.twin_chat[-1].result
assert bpy.context.window_manager.twin_chat[-1].body == '큐브 생성 요청'
assert addon._status_kind == 'SUCCESS'
# Explicit copy uses the complete displayed reply without touching the OS clipboard.
wm = bpy.context.window_manager
proxy_wm = types.SimpleNamespace(twin_chat=wm.twin_chat, twin_chat_index=wm.twin_chat_index, clipboard='')
proxy_context = types.SimpleNamespace(window_manager=proxy_wm)
operator = types.SimpleNamespace(report=lambda *args: None)
assert addon.TWIN_OT_copy_reply.execute(operator, proxy_context) == {'FINISHED'}
assert proxy_wm.clipboard == addon.message_text(wm.twin_chat[-1])
r.events.put({'type':'action_done'})
addon.poll_events()
assert not addon._busy
bpy.context.window_manager.twin_prompt = 'Move it one unit'
assert bpy.ops.twin.propose() == {'FINISHED'}
assert r.calls[-1][1][0]['object_name'] == 'ChatCube'
assert len(r.calls[-1][3]) == 2
command = dict(command, operation='move', target='ChatCube', name='', vector=[1,0,0])
r.events.put({'type':'chat','message':'이동 요청','actions':[command]})
r.events.put({'type':'action_done'})
addon.poll_events()
assert bpy.data.objects['ChatCube'].location.x == 1
# Stale transformed object blocks the complete response.
bpy.context.window_manager.twin_prompt = 'Move again'
bpy.ops.twin.propose()
bpy.data.objects['ChatCube'].location.x = 5
r.events.put({'type':'chat','message':'이동 요청','actions':[command]})
r.events.put({'type':'action_done'})
addon.poll_events()
assert bpy.data.objects['ChatCube'].location.x == 5
assert '실제 실행 실패' in bpy.context.window_manager.twin_chat[-1].content
assert bpy.context.window_manager.twin_chat[-1].outcome == 'FAILED'
assert bpy.context.window_manager.twin_prompt == 'Move again'
assert addon._status_kind == 'ERROR'
# Cancel after response was queued must still prevent execution.
bpy.context.window_manager.twin_prompt = 'Move again'
bpy.ops.twin.propose()
r.events.put({'type':'chat','message':'이동 요청','actions':[command]})
bpy.ops.twin.cancel_chat()
r.events.put({'type':'action_done'})
addon.poll_events()
assert bpy.data.objects['ChatCube'].location.x == 5
# New chat preserves scene and resets context.
bpy.ops.twin.new_chat()
assert not bpy.context.window_manager.twin_chat
assert bpy.data.objects['ChatCube'].location.x == 5
# A dialog may outlive conversation reset in another window; it must draw safely.
labels = []
closed_dialog = types.SimpleNamespace(layout=types.SimpleNamespace(label=lambda **kw: labels.append(kw['text'])))
addon.TWIN_OT_view_reply.draw(closed_dialog, bpy.context)
assert labels == ['대화가 종료되었습니다']
# Pure chat works while objects are selected, without sending those objects.
r._cancel.clear()
scene.twin_chat_selection = False
bpy.context.window_manager.twin_prompt = 'What can you do?'
bpy.ops.twin.propose()
assert r.calls[-1][1] == []
r.events.put({'type':'chat','message':'생성과 변환을 지원합니다','actions':[]})
r.events.put({'type':'action_done'})
addon.poll_events()
assert len(bpy.context.window_manager.twin_chat) == 2
# Switching the active collection must not redirect an in-flight creation.
bpy.context.window_manager.twin_prompt = 'Create in the original collection'
bpy.ops.twin.propose()
old_layer_collection = bpy.context.view_layer.active_layer_collection
new_collection = bpy.data.collections.new('OtherCollection')
scene.collection.children.link(new_collection)
bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection.children[new_collection.name]
r.events.put({'type':'chat','message':'생성 요청','actions':[dict(command, operation='create', target='', name='WrongCollectionCube')]})
r.events.put({'type':'action_done'})
addon.poll_events()
assert bpy.data.objects.get('WrongCollectionCube') is None
assert '실제 실행 실패' in bpy.context.window_manager.twin_chat[-1].content
bpy.context.view_layer.active_layer_collection = old_layer_collection
# Switching scenes while a response is in flight blocks creation.
bpy.context.window_manager.twin_prompt = 'Create in the original scene'
bpy.ops.twin.propose()
other_scene = bpy.data.scenes.new('OtherScene')
bpy.context.window.scene = other_scene
r.events.put({'type':'chat','message':'생성 요청','actions':[dict(command, operation='create', target='', name='WrongSceneCube')]})
r.events.put({'type':'action_done'})
addon.poll_events()
assert bpy.data.objects.get('WrongSceneCube') is None
assert '실제 실행 실패' in bpy.context.window_manager.twin_chat[-1].content
bpy.context.window.scene = scene
# Session-only data must not appear in the saved file, even before handlers run.
addon.add_chat(scene, 'user', 'PRIVATE_CHAT_DISK_SENTINEL_314159')
bpy.context.window_manager.twin_prompt = 'PRIVATE_PROMPT_DISK_SENTINEL_271828'
bpy.context.window_manager.twin_api_key = 'PRIVATE_API_KEY_DISK_SENTINEL_161803'

path = root / 'test-output' / 'chat.blend'
path.parent.mkdir(exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=False)
raw = path.read_bytes()
assert b'PRIVATE_CHAT_DISK_SENTINEL_314159' not in raw
assert b'PRIVATE_PROMPT_DISK_SENTINEL_271828' not in raw
assert b'PRIVATE_API_KEY_DISK_SENTINEL_161803' not in raw
bpy.ops.wm.open_mainfile(filepath=str(path))
assert not bpy.context.window_manager.twin_chat, 'session history leaked across file load'
assert not bpy.context.window_manager.twin_api_key
# Provider switching clears credentials and conversation before reconnecting.
addon.add_chat(bpy.context.scene, 'user', 'previous-provider-private-message')
bpy.context.window_manager.twin_api_key = 'previous-provider-key'
bpy.context.window_manager.twin_provider = 'OPENAI_API'
assert not bpy.context.window_manager.twin_chat
assert not bpy.context.window_manager.twin_api_key
assert addon._runtime is None
assert not addon.TWIN_OT_login.poll(bpy.context)
class FakeAPI(FakeRuntime):
    def __init__(self, provider, api_key):
        super().__init__()
        assert provider == 'openai' and api_key == 'test-session-key'
original_api = addon.APIRuntime
addon.APIRuntime = FakeAPI
bpy.context.window_manager.twin_api_key = 'test-session-key'
assert isinstance(addon.get_runtime(bpy.context), FakeAPI)
addon.APIRuntime = original_api
addon._runtime.events.put({'type':'account','account':{'type':'api'}})
addon.poll_events()
assert addon._account
addon.stop_runtime()
assert not bpy.context.window_manager.twin_api_key
bpy.context.window_manager.twin_provider = 'CLAUDE_LOGIN'
assert addon.TWIN_OT_login.poll(bpy.context)
addon.unregister()
print('BLENDER_CHAT_SMOKE_OK: chat create, followup move, stale rejection, cancel, models, pure chat, new chat, scene switch, private disk exclusion, load cleanup')
