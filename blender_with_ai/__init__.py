"""Blender chat UI: bounded commands execute on the main thread with Undo."""
import json
import os
import queue
import threading
import uuid
import webbrowser
import textwrap

import bpy
from bpy.props import StringProperty, PointerProperty, CollectionProperty, IntProperty, BoolProperty, EnumProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper

from .metadata import validate_fields, validate_proposal, build_export, parse_csv, suggest_by_name
from .runtime import CodexRuntime, MAX_HISTORY, validate_history
from .scene_actions import snapshot, apply_actions
from .api_runtime import APIRuntime
from .claude_runtime import ClaudeRuntime, discover_claude
from .launcher import discover_candidates

bl_info = {"name": "Blender with AI", "author": "Blender with AI contributors",
           "version": (0, 1, 0), "blender": (4, 2, 0), "category": "Object"}
KEY = "twin_meta"
_runtime = None
_status = "연결 전 · 오프라인 기능 사용 가능"
_account = False
_models = []
_model_items = [("__NONE__", "연결 후 모델 선택", "")]
_request_context = None
_login_url = ""
_busy = False
_pending_response = None
_login_refresh = False


def metadata(obj):
    value = obj.get(KEY)
    if value is None:
        return {}
    if not hasattr(value, "items"):
        raise ValueError(f"{obj.name}: 기존 twin_meta 형식이 올바르지 않습니다")
    return dict(value.items())


def fingerprint(obj):
    return json.dumps(metadata(obj), sort_keys=True, ensure_ascii=False)


def selected(context):
    result = list(context.selected_objects)
    if not result:
        raise ValueError("객체를 먼저 선택하세요")
    for obj in result:
        if not obj.is_editable:
            raise ValueError(f"{obj.name}: 편집할 수 없는 연결 객체입니다")
    return result


def check_ids(objects):
    seen = {}
    for obj in objects:
        object_id = metadata(obj).get("object_id")
        if not object_id:
            raise ValueError(f"{obj.name}: 객체 ID를 먼저 부여하세요")
        if object_id in seen:
            raise ValueError(f"중복 ID: {seen[object_id]} / {obj.name}. 복제 객체 ID 재발급이 필요합니다")
        seen[object_id] = obj.name


def put_preview(scene, rows, source):
    prepared = [(obj, fingerprint(obj), json.dumps(validate_fields(fields), ensure_ascii=False))
                for obj, fields in rows]
    scene.twin_preview.clear()
    for obj, before, fields in prepared:
        item = scene.twin_preview.add()
        item.target = obj
        item.before = before
        item.fields = fields
    scene.twin_preview_source = source


class TwinPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    codex_path: StringProperty(name="Codex 실행 파일", subtype="FILE_PATH", default="")
    claude_path: StringProperty(name="Claude 실행 파일", subtype="FILE_PATH", default="")

    def draw(self, context):
        self.layout.prop(self, "codex_path")
        self.layout.prop(self, "claude_path")
        self.layout.label(text="비우면 PATH 또는 기본 설치 위치에서 찾습니다")
        self.layout.label(text="API 키·로그인 토큰을 이 애드온에 저장하지 않습니다")


class TwinPreviewItem(bpy.types.PropertyGroup):
    target: PointerProperty(type=bpy.types.Object)
    before: StringProperty()
    fields: StringProperty()


class TwinChatItem(bpy.types.PropertyGroup):
    role: StringProperty()
    content: StringProperty()


class TWIN_UL_chat(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        label = '나' if item.role == 'user' else ('Claude' if context.window_manager.twin_provider in ('CLAUDE_LOGIN', 'ANTHROPIC_API') else 'GPT')
        layout.label(text=f'{label}: {item.content[:100]}')


def model_items(self, context):
    return _model_items


def add_chat(scene, role, content):
    item = bpy.context.window_manager.twin_chat.add()
    item.role, item.content = role, content
    bpy.context.window_manager.twin_chat_index = len(bpy.context.window_manager.twin_chat) - 1


class TWIN_UL_preview(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.target.name if item.target else "삭제된 객체", icon="OBJECT_DATA")


def discover_codex(context):
    prefs = context.preferences.addons[__package__].preferences
    custom = bpy.path.abspath(prefs.codex_path) if prefs.codex_path else ""
    if custom:
        if not os.path.isfile(custom):
            raise ValueError("설정한 Codex 실행 파일이 없습니다")
        return custom
    candidates = discover_candidates()
    if candidates:
        return candidates[0]
    raise ValueError("Codex를 설치하거나 애드온 설정에서 실행 파일을 선택하세요")


def claude_path(context):
    prefs = context.preferences.addons[__package__].preferences
    if prefs.claude_path:
        custom = bpy.path.abspath(prefs.claude_path)
        if not os.path.isfile(custom):
            raise ValueError("설정한 Claude 실행 파일이 없습니다")
        return custom
    candidates = discover_claude()
    if candidates:
        return candidates[0]
    raise ValueError("Claude Code를 설치하거나 애드온 설정에서 실행 파일을 선택하세요")


def provider_changed(self, context):
    # Credentials and transcripts never cross provider boundaries.
    stop_runtime()


def get_runtime(context):
    global _runtime
    if _runtime is None:
        provider = context.window_manager.twin_provider
        if provider == 'CODEX':
            _runtime = CodexRuntime(discover_codex(context))
        elif provider == 'CLAUDE_LOGIN':
            _runtime = ClaudeRuntime(claude_path(context))
        else:
            name = 'openai' if provider == 'OPENAI_API' else 'anthropic'
            env_name = 'OPENAI_API_KEY' if name == 'openai' else 'ANTHROPIC_API_KEY'
            key = context.window_manager.twin_api_key or os.environ.get(env_name, '')
            if not key.strip():
                raise ValueError("API 키를 입력하거나 해당 공급자의 환경 변수를 설정하세요")
            _runtime = APIRuntime(provider=name, api_key=key)
    return _runtime


def poll_events():
    global _status, _account, _models, _model_items, _login_url, _busy, _request_context, _pending_response, _login_refresh
    if _runtime:
        for _ in range(100):
            try:
                event = _runtime.events.get_nowait()
            except queue.Empty:
                break
            kind = event.get("type")
            if kind in ("status", "error"):
                _status = event.get("message", kind)[:500]
                if kind == "error":
                    _request_context = None
            elif kind == "action_done":
                _busy = False
                if _login_refresh:
                    _login_refresh = False
                    _runtime.connect()
                    _busy = True
            elif kind == "cancelled":
                _request_context = None
                _status = "요청 취소됨 · 장면 변경 없음"
            elif kind == "account":
                account = event.get("account")
                _account = bool(account and account.get("type") in ("chatgpt", "api", "claude"))
                _status = "모델 연결됨" if _account else "계정 또는 API 연결을 확인하세요"
            elif kind == "models":
                previous_models = [(scene, scene.twin_model) for scene in bpy.data.scenes]
                _models = event.get("models", [])
                _model_items = [(m['model'], m.get('displayName', m['model']), '')
                                for m in _models if isinstance(m, dict) and m.get('model')]
                if not _model_items:
                    _model_items = [("__NONE__", "사용 가능한 모델 없음", "")]
                available = {row[0] for row in _model_items}
                for scene, previous in previous_models:
                    scene.twin_model = previous if previous in available else _model_items[0][0]
            elif kind == "login_url":
                _login_url = event.get("url", "")
                if _login_url.startswith("https://"):
                    webbrowser.open(_login_url)
                _status = "브라우저에서 로그인을 완료하세요"
            elif kind == "login_complete":
                _status = "로그인 완료" if event.get("success") else "로그인 취소 또는 실패"
                if event.get("success"):
                    if _busy:
                        _login_refresh = True
                    else:
                        _runtime.connect()
                        _busy = True
            elif kind == "chat":
                if _request_context is not None:
                    _pending_response = event
                    bpy.ops.twin.execute_chat()
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    return 0.4


class TWIN_OT_connect(bpy.types.Operator):
    bl_idname = "twin.connect"
    bl_label = "Codex 연결 확인"
    @classmethod
    def poll(cls, context):
        return not _busy
    def execute(self, context):
        try:
            get_runtime(context).connect()
            globals()['_busy'] = True
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class TWIN_OT_login(bpy.types.Operator):
    bl_idname = "twin.login"
    bl_label = "계정 로그인"
    @classmethod
    def poll(cls, context):
        return not _busy and context.window_manager.twin_provider in ('CODEX', 'CLAUDE_LOGIN')
    def execute(self, context):
        try:
            get_runtime(context).login()
            globals()['_busy'] = True
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class TWIN_OT_disconnect(bpy.types.Operator):
    bl_idname = "twin.disconnect"
    bl_label = "연결 닫기"
    def execute(self, context):
        stop_runtime()
        return {"FINISHED"}


class TWIN_OT_init_ids(bpy.types.Operator):
    bl_idname = "twin.init_ids"
    bl_label = "선택 객체 ID 부여"
    bl_options = {"REGISTER", "UNDO"}
    def execute(self, context):
        try:
            objects = selected(context)
            values = [(obj, metadata(obj)) for obj in objects]
            for obj, value in values:
                if not value.get("object_id"):
                    value.update(object_id=str(uuid.uuid4()))
                    value.setdefault("object_type", "UNKNOWN")
                    value.setdefault("review_status", "UNREVIEWED")
                    obj[KEY] = value
            self.report({"INFO"}, "기존 ID를 유지하고 누락 ID를 부여했습니다")
            return {"FINISHED"}
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class TWIN_OT_rules(bpy.types.Operator):
    bl_idname = "twin.rules"
    bl_label = "이름 규칙으로 분류 제안"
    def execute(self, context):
        try:
            objects = selected(context)
            check_ids(objects)
            put_preview(context.scene, [(obj, suggest_by_name(obj.name)) for obj in objects], "이름 규칙")
            return {"FINISHED"}
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class TWIN_OT_propose(bpy.types.Operator):
    bl_idname = "twin.propose"
    bl_label = "메시지 보내기"
    bl_description = "대화 기록과 선택 객체의 이름·위치·회전·크기를 보내 Blender 작업을 요청합니다"
    @classmethod
    def poll(cls, context):
        return (_account and not _busy and context.scene.twin_model != '__NONE__'
                and bool(context.window_manager.twin_prompt.strip()))
    def execute(self, context):
        global _request_context, _busy, _status
        try:
            scene = context.scene
            if len(bpy.context.window_manager.twin_chat) >= MAX_HISTORY:
                raise ValueError("대화는 최대 10회입니다. 새 대화를 시작하세요")
            if context.mode != 'OBJECT':
                raise ValueError("오브젝트 모드에서 요청하세요")
            if scene.twin_chat_selection:
                records, snapshots = snapshot(context)
            else:
                with context.temp_override(selected_objects=[]):
                    records, snapshots = snapshot(context)
            history = [{"role": row.role, "content": row.content} for row in bpy.context.window_manager.twin_chat]
            validate_history(history)
            get_runtime(context).chat(bpy.context.window_manager.twin_prompt, records, scene.twin_model, history)
            _request_context = (scene, snapshots, bpy.context.window_manager.twin_prompt, context.view_layer, context.collection)
            _busy = True
            _status = "AI 답변 요청 중…"
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class TWIN_OT_execute_chat(bpy.types.Operator):
    bl_idname = "twin.execute_chat"
    bl_label = "AI Blender 작업 실행"
    bl_options = {"UNDO", "INTERNAL"}
    def execute(self, context):
        global _request_context, _pending_response, _status
        request, event = _request_context, _pending_response
        _request_context = None
        _pending_response = None
        if request is None or event is None:
            return {"CANCELLED"}
        scene, snapshots, prompt, view_layer, *collection = request
        try:
            if context.scene != scene or context.view_layer != view_layer:
                raise ValueError("요청 이후 장면 또는 뷰 레이어가 바뀌어 실행하지 않았습니다")
            if collection and context.collection != collection[0] and any(
                    action['operation'] == 'create' for action in event['actions']):
                raise ValueError("요청 이후 활성 컬렉션이 바뀌어 생성하지 않았습니다")
            if _runtime and _runtime._cancel.is_set():
                raise ValueError("취소한 요청은 실행하지 않습니다")
            summary = apply_actions(context, event['actions'], snapshots) if event['actions'] else "실행할 작업 없음"
            response = "실제 실행 결과: " + summary + "\n\n" + event['message']
            _status = "작업 완료 · Ctrl+Z로 되돌리기" if event['actions'] else "답변 완료"
        except Exception as exc:
            response = "실제 실행 실패: " + str(exc)[:1000] + "\n\n" + event['message']
            _status = str(exc)[:500]
        try:
            add_chat(scene, "user", prompt)
            add_chat(scene, "assistant", response[:8000])
            bpy.context.window_manager.twin_prompt = ""
        except ReferenceError:
            pass
        return {"FINISHED"}


class TWIN_OT_cancel_chat(bpy.types.Operator):
    bl_idname = "twin.cancel_chat"
    bl_label = "요청 취소"
    @classmethod
    def poll(cls, context):
        return _busy and _request_context is not None
    def execute(self, context):
        global _status
        _runtime.cancel()
        _status = "요청 취소 중… API 요청은 응답 종료까지 기다릴 수 있습니다"
        return {"FINISHED"}


class TWIN_OT_new_chat(bpy.types.Operator):
    bl_idname = "twin.new_chat"
    bl_label = "새 대화"
    @classmethod
    def poll(cls, context):
        return not _busy
    def execute(self, context):
        context.window_manager.twin_chat.clear()
        context.window_manager.twin_prompt = ""
        return {"FINISHED"}


class TWIN_OT_apply(bpy.types.Operator):
    bl_idname = "twin.apply"
    bl_label = "미리보기 전체 적용"
    bl_options = {"REGISTER", "UNDO"}
    def execute(self, context):
        try:
            rows = []
            for item in context.scene.twin_preview:
                obj = item.target
                if not obj or not obj.is_editable or fingerprint(obj) != item.before:
                    raise ValueError("객체가 삭제·변경되었습니다. 제안을 다시 생성하세요")
                fields = validate_fields(json.loads(item.fields))
                value = metadata(obj)
                value.update(fields)
                rows.append((obj, value))
            check_ids([obj for obj, _ in rows])
            build_export([dict(value, object_name=obj.name) for obj, value in rows])
            before = [(obj, metadata(obj)) for obj, _ in rows]
            try:
                for obj, value in rows:
                    obj[KEY] = value
            except Exception:
                for obj, value in before:
                    obj[KEY] = value
                raise ValueError("속성 저장 실패 · 기존 값을 복원했습니다") from None
            count = len(rows)
            context.scene.twin_preview.clear()
            self.report({"INFO"}, f"{count}개 적용 완료 · Ctrl+Z로 되돌릴 수 있습니다")
            return {"FINISHED"}
        except (ValueError, TypeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class TWIN_OT_discard(bpy.types.Operator):
    bl_idname = "twin.discard"
    bl_label = "제안 버리기"
    def execute(self, context):
        context.scene.twin_preview.clear()
        return {"FINISHED"}


class TWIN_OT_import_csv(bpy.types.Operator, ImportHelper):
    bl_idname = "twin.import_csv"
    bl_label = "CSV 속성 가져오기"
    filename_ext = ".csv"
    filter_glob: StringProperty(default="*.csv", options={"HIDDEN"})
    def execute(self, context):
        try:
            with open(self.filepath, encoding="utf-8-sig") as stream:
                text = stream.read(2_000_001)
            if len(text) > 2_000_000:
                raise ValueError("CSV는 2MB 이하로 나눠주세요")
            objects = [obj for obj in context.scene.objects if metadata(obj).get("object_id")]
            check_ids(objects)
            by_id = {metadata(obj)["object_id"]: obj for obj in objects}
            rows = []
            for row in parse_csv(text):
                row = dict(row)
                object_id = row.pop("object_id")
                if object_id not in by_id:
                    raise ValueError(f"장면에 없는 ID: {object_id}")
                rows.append((by_id[object_id], row))
            put_preview(context.scene, rows, "CSV")
            return {"FINISHED"}
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class TWIN_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "twin.export"
    bl_label = "선택 객체 메타데이터 내보내기"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})
    def execute(self, context):
        try:
            objects = selected(context)
            check_ids(objects)
            result = build_export([dict(metadata(obj), object_name=obj.name) for obj in objects])
            result["scene"] = {"name": context.scene.name, "unit_system": context.scene.unit_settings.system,
                               "scale_length": context.scene.unit_settings.scale_length,
                               "coordinate_note": "Local Blender coordinates; GIS transform must be agreed separately"}
            with open(self.filepath, "w", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            self.report({"INFO"}, f"{len(objects)}개 객체의 메타데이터 저장 완료")
            return {"FINISHED"}
        except (ValueError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class TWIN_PT_panel(bpy.types.Panel):
    bl_label = "Blender with AI"
    bl_idname = "TWIN_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI"
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        box = layout.box()
        box.label(text="1. AI 연결", icon="WORLD")
        box.prop(context.window_manager, "twin_provider")
        if context.window_manager.twin_provider in ('OPENAI_API', 'ANTHROPIC_API'):
            row = box.row()
            row.enabled = _runtime is None
            row.prop(context.window_manager, "twin_api_key")
            box.label(text="API 사용료는 공급자에 별도 청구됩니다")
            box.label(text="키는 세션에서만 사용 · 연결 닫으면 지웁니다")
            box.label(text="비우면 OPENAI_API_KEY / ANTHROPIC_API_KEY 사용")
        for line in textwrap.wrap(_status, width=max(18, int(context.region.width / 12))):
            box.label(text=line)
        row = box.row(align=True)
        row.operator("twin.connect", text="연결 확인")
        if context.window_manager.twin_provider in ('CODEX', 'CLAUDE_LOGIN'):
            row.operator("twin.login", text="로그인")
        box.operator("twin.disconnect", text="연결 닫기 / 요청 취소")
        box.prop(scene, "twin_model")
        if _models:
            box.label(text=f"계정에서 조회한 모델: {len(_models)}개")
        box = layout.box()
        box.label(text="AI와 Blender 작업", icon="COMMUNITY")
        box.label(text="생성 · 이동 · 회전 · 크기 · 이름 변경")
        box.label(text="요청한 작업은 실행되며 Ctrl+Z로 되돌립니다")
        box.label(text="대화는 현재 세션에만 보관됩니다 · 최대 10회")
        box.operator("twin.new_chat")
        if bpy.context.window_manager.twin_chat:
            box.template_list("TWIN_UL_chat", "", context.window_manager, "twin_chat", context.window_manager, "twin_chat_index", rows=4)
            index = min(max(bpy.context.window_manager.twin_chat_index, 0), len(bpy.context.window_manager.twin_chat) - 1)
            for paragraph in bpy.context.window_manager.twin_chat[index].content.splitlines():
                for line in textwrap.wrap(paragraph, width=max(18, int(context.region.width / 12))) or ['']:
                    box.label(text=line)
        row = box.column()
        row.enabled = not _busy
        row.prop(scene, "twin_chat_selection")
        row.prop(context.window_manager, "twin_prompt")
        box.label(text="전송: 대화 기록 + 선택 객체 이름·변환값")
        row = box.row(align=True)
        row.operator("twin.propose", icon="PLAY")
        row.operator("twin.cancel_chat", icon="CANCEL")


class TWIN_PT_legacy(bpy.types.Panel):
    bl_label = "기존 메타데이터 도구"
    bl_idname = "TWIN_PT_legacy"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI"
    bl_options = {'DEFAULT_CLOSED'}
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.operator("twin.init_ids")
        layout.operator("twin.rules")
        layout.operator("twin.import_csv")
        if scene.twin_preview:
            box = layout.box()
            box.label(text=f"4. {scene.twin_preview_source} · {len(scene.twin_preview)}개")
            box.template_list("TWIN_UL_preview", "", scene, "twin_preview", scene, "twin_preview_index", rows=3)
            index = min(max(scene.twin_preview_index, 0), len(scene.twin_preview) - 1)
            item = scene.twin_preview[index]
            old = json.loads(item.before)
            for key, value in json.loads(item.fields).items():
                box.label(text=f"{key}: {old.get(key, '—')} → {value}")
            row = box.row(align=True)
            row.operator("twin.apply", text="전체 적용", icon="CHECKMARK")
            row.operator("twin.discard", text="버리기", icon="X")
        if context.active_object:
            box = layout.box()
            box.label(text="현재 객체 속성")
            try:
                for key, value in metadata(context.active_object).items():
                    box.label(text=f"{key}: {value}")
            except ValueError as exc:
                box.label(text=str(exc))
        layout.operator("twin.export", icon="EXPORT")


def stop_runtime():
    global _runtime, _account, _busy, _request_context, _status, _models, _model_items, _login_url, _pending_response, _login_refresh
    runtime, _runtime = _runtime, None
    _pending_response = None
    _login_refresh = False
    if hasattr(bpy.context.window_manager, 'twin_chat'):
        bpy.context.window_manager.twin_chat.clear()
        bpy.context.window_manager.twin_prompt = ""
    if hasattr(bpy.context.window_manager, 'twin_api_key'):
        bpy.context.window_manager.twin_api_key = ""
    if runtime:
        threading.Thread(target=runtime.close, daemon=True).start()
    _account = False
    _busy = False
    _request_context = None
    _models = []
    _model_items = [("__NONE__", "연결 후 모델 선택", "")]
    _login_url = ""
    _status = "연결 닫힘 · 세션 대화와 입력한 API 키를 지웠습니다"


@bpy.app.handlers.persistent
def on_load(_):
    stop_runtime()
    if not bpy.app.timers.is_registered(poll_events):
        bpy.app.timers.register(poll_events, first_interval=0.4, persistent=True)


CLASSES = (TwinPreferences, TwinPreviewItem, TwinChatItem, TWIN_UL_chat, TWIN_UL_preview, TWIN_OT_connect, TWIN_OT_login,
           TWIN_OT_disconnect, TWIN_OT_init_ids, TWIN_OT_rules, TWIN_OT_propose, TWIN_OT_cancel_chat, TWIN_OT_new_chat, TWIN_OT_execute_chat, TWIN_OT_apply,
           TWIN_OT_discard, TWIN_OT_import_csv, TWIN_OT_export, TWIN_PT_panel, TWIN_PT_legacy)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.twin_provider = EnumProperty(name="연결 방식", items=[
        ('CODEX', 'ChatGPT 계정 (Codex)', '로컬 Codex 로그인 사용'),
        ('CLAUDE_LOGIN', 'Claude 계정 (Claude Code)', 'Claude Code 공식 계정 로그인'),
        ('OPENAI_API', 'OpenAI API', 'OpenAI API 키로 연결'),
        ('ANTHROPIC_API', 'Claude API', 'Anthropic API 키로 연결')],
        default='CODEX', update=provider_changed, options={'SKIP_SAVE'})
    bpy.types.WindowManager.twin_api_key = StringProperty(name="API 키", subtype='PASSWORD',
                                                        options={'SKIP_SAVE'})
    bpy.types.Scene.twin_model = EnumProperty(name="모델", items=model_items)
    bpy.types.WindowManager.twin_prompt = StringProperty(name="요청", default="원점에 큐브를 만들어줘.", maxlen=8000, options={"SKIP_SAVE"})
    bpy.types.WindowManager.twin_chat = CollectionProperty(type=TwinChatItem, options={'SKIP_SAVE'})
    bpy.types.WindowManager.twin_chat_index = IntProperty(default=0, options={'SKIP_SAVE'})
    bpy.types.Scene.twin_chat_selection = BoolProperty(name="선택 객체 포함", default=True)
    bpy.types.Scene.twin_preview = CollectionProperty(type=TwinPreviewItem)
    bpy.types.Scene.twin_preview_index = IntProperty(default=0)
    bpy.types.Scene.twin_preview_source = StringProperty()
    bpy.app.handlers.load_pre.append(on_load)
    bpy.app.timers.register(poll_events, first_interval=0.4, persistent=True)


def unregister():
    stop_runtime()
    if bpy.app.timers.is_registered(poll_events):
        bpy.app.timers.unregister(poll_events)
    if on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(on_load)
    for name in ("twin_model", "twin_chat_selection", "twin_preview", "twin_preview_index", "twin_preview_source"):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for name in ('twin_chat', 'twin_chat_index', 'twin_prompt', 'twin_provider', 'twin_api_key'):
        if hasattr(bpy.types.WindowManager, name):
            delattr(bpy.types.WindowManager, name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
