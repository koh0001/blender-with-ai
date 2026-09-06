"""Blender chat UI: bounded commands execute on the main thread with Undo."""
import json
import os
import queue
import threading
import uuid
import webbrowser
import time
import unicodedata

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
_status_kind = "IDLE"
_busy_started = 0.0


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
    result: StringProperty()
    body: StringProperty()
    outcome: StringProperty()


def assistant_name(context):
    return 'Claude' if context.window_manager.twin_provider in ('CLAUDE_LOGIN', 'ANTHROPIC_API') else 'GPT'


def wrapped_lines(text, columns):
    """Conservative native-UI wrapping, counting wide Korean characters twice."""
    lines = []
    for paragraph in str(text).splitlines() or ['']:
        line, width = '', 0
        for char in paragraph:
            size = 2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
            if width + size > columns and line:
                split = line.rfind(' ')
                if split > len(line) // 2:
                    lines.append(line[:split])
                    line = line[split + 1:]
                    width = sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in line)
                else:
                    lines.append(line)
                    line, width = '', 0
            line += char
            width += size
        lines.append(line.rstrip())
    return lines


def panel_columns(context):
    scale = context.preferences.system.ui_scale
    width = context.region.width if context.region else 320
    return max(18, min(90, int((width / scale - 42) / 7)))


def draw_text(layout, text, columns, limit=6):
    lines = wrapped_lines(text, columns)
    for line in lines[:limit]:
        layout.label(text=line)
    return len(lines) > limit


def selected_message(context):
    wm = context.window_manager
    history = getattr(wm, 'twin_chat', None)
    if not history:
        return None
    return history[min(max(wm.twin_chat_index, 0), len(history) - 1)]


def message_text(item):
    if item.result:
        prefix = '실제 실행 실패: ' if item.outcome == 'FAILED' else '실제 실행 결과: '
        return prefix + item.result + '\n\n' + item.body
    return item.body or item.content


def begin_busy(message):
    global _busy, _busy_started, _status, _status_kind
    _busy = True
    _busy_started = time.monotonic()
    _status = message
    _status_kind = 'BUSY'


def show_error(message):
    global _status, _status_kind
    _status = str(message)[:500]
    _status_kind = 'ERROR'


class TWIN_UL_chat(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        role = '나' if item.role == 'user' else assistant_name(context)
        split = layout.split(factor=0.24)
        split.label(text=role, icon='USER' if item.role == 'user' else 'COMMUNITY')
        row = split.row(align=True)
        preview = (item.body or item.content).replace('\n', ' ')
        row.label(text=preview[:90])
        if item.outcome == 'FAILED':
            row.label(text='', icon='ERROR')
        elif item.outcome == 'APPLIED':
            row.label(text='', icon='CHECKMARK')


def model_items(self, context):
    return _model_items


def add_chat(scene, role, content, result="", body="", outcome=""):
    item = bpy.context.window_manager.twin_chat.add()
    item.role, item.content = role, content
    item.result, item.body, item.outcome = result, body or content, outcome
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
    global _status, _account, _models, _model_items, _login_url, _busy, _request_context, _pending_response, _login_refresh, _status_kind, _busy_started
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
                    _status_kind = 'ERROR'
                elif not _busy:
                    _status_kind = 'IDLE'
            elif kind == "action_done":
                _busy = False
                _busy_started = 0.0
                if _status_kind == 'BUSY':
                    _status_kind = 'IDLE'
                if _login_refresh:
                    _login_refresh = False
                    _runtime.connect()
                    begin_busy("로그인 상태 확인 중…")
            elif kind == "cancelled":
                _request_context = None
                _status = "요청을 취소했습니다. 장면은 변경하지 않았습니다."
                _status_kind = 'CANCELLED'
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
                _status = "브라우저에서 로그인을 완료한 뒤 돌아오세요."
                _status_kind = 'IDLE'
            elif kind == "login_complete":
                _status = "로그인 완료" if event.get("success") else "로그인 취소 또는 실패"
                if event.get("success"):
                    if _busy:
                        _login_refresh = True
                    else:
                        _runtime.connect()
                        begin_busy("로그인 상태 확인 중…")
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
    bl_label = "연결 확인"
    @classmethod
    def poll(cls, context):
        return not _busy
    def execute(self, context):
        try:
            get_runtime(context).connect()
            begin_busy("연결을 확인하고 있습니다…")
            return {"FINISHED"}
        except Exception as exc:
            show_error(exc)
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
            begin_busy("공식 로그인 창을 준비하고 있습니다…")
            return {"FINISHED"}
        except Exception as exc:
            show_error(exc)
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
            if scene.twin_chat_selection:
                records, snapshots = snapshot(context)
            else:
                with context.temp_override(selected_objects=[]):
                    records, snapshots = snapshot(context)
            history = [{"role": row.role, "content": row.content} for row in bpy.context.window_manager.twin_chat]
            validate_history(history)
            get_runtime(context).chat(bpy.context.window_manager.twin_prompt, records, scene.twin_model, history)
            _request_context = (scene, snapshots, bpy.context.window_manager.twin_prompt, context.view_layer, context.collection)
            begin_busy("AI가 요청을 확인하고 있습니다…")
            return {"FINISHED"}
        except Exception as exc:
            show_error(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class TWIN_OT_execute_chat(bpy.types.Operator):
    bl_idname = "twin.execute_chat"
    bl_label = "AI Blender 작업 실행"
    bl_options = {"UNDO", "INTERNAL"}
    def execute(self, context):
        global _request_context, _pending_response, _status, _status_kind
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
            outcome = 'APPLIED' if event['actions'] else 'NO_ACTION'
            _status_kind = 'SUCCESS' if event['actions'] else 'IDLE'
            _status = "작업 완료 · Ctrl+Z로 되돌리기" if event['actions'] else "답변 완료"
        except Exception as exc:
            summary = str(exc)[:1000]
            response = "실제 실행 실패: " + summary + "\n\n" + event['message']
            outcome = 'FAILED'
            show_error(exc)
        try:
            add_chat(scene, "user", prompt)
            add_chat(scene, "assistant", response[:8000], result=summary,
                     body=event['message'], outcome=outcome)
            if outcome != 'FAILED':
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
        global _status, _status_kind
        _runtime.cancel()
        _status_kind = 'CANCELLING'
        _status = "요청 취소 중… API 요청은 응답 종료까지 기다릴 수 있습니다"
        return {"FINISHED"}


class TWIN_OT_new_chat(bpy.types.Operator):
    bl_idname = "twin.new_chat"
    bl_label = "새 대화"
    @classmethod
    def poll(cls, context):
        return not _busy
    def execute(self, context):
        global _status, _status_kind
        context.window_manager.twin_chat.clear()
        context.window_manager.twin_prompt = ""
        _status = "새 대화입니다. 원하는 작업을 적어주세요."
        _status_kind = 'IDLE'
        return {"FINISHED"}


class TWIN_OT_seed_prompt(bpy.types.Operator):
    bl_idname = "twin.seed_prompt"
    bl_label = "예시로 시작하기"
    bl_description = "입력창에 예시를 넣습니다. 전송하기 전에는 실행하지 않습니다"
    prompt: StringProperty(options={'SKIP_SAVE'})
    @classmethod
    def poll(cls, context):
        return not _busy
    def execute(self, context):
        context.window_manager.twin_prompt = self.prompt
        return {'FINISHED'}


class TWIN_OT_copy_reply(bpy.types.Operator):
    bl_idname = "twin.copy_reply"
    bl_label = "메시지 복사"
    bl_description = "선택한 대화와 실제 실행 결과를 클립보드로 복사합니다"
    @classmethod
    def poll(cls, context):
        return selected_message(context) is not None
    def execute(self, context):
        context.window_manager.clipboard = message_text(selected_message(context))
        self.report({'INFO'}, "메시지를 복사했습니다")
        return {'FINISHED'}


class TWIN_OT_view_reply(bpy.types.Operator):
    bl_idname = "twin.view_reply"
    bl_label = "대화 자세히 보기"
    bl_description = "긴 메시지를 페이지별로 읽습니다. 대화는 파일에 저장하지 않습니다"
    page: IntProperty(name="페이지", default=1, min=1, max=1000, options={'SKIP_SAVE'})
    @classmethod
    def poll(cls, context):
        return selected_message(context) is not None
    def _lines(self, context):
        item = selected_message(context)
        return wrapped_lines(message_text(item) if item else '', 76)
    def check(self, context):
        self.page = min(self.page, max(1, (len(self._lines(context)) + 19) // 20))
        return True
    def invoke(self, context, event):
        self.page = 1
        return context.window_manager.invoke_popup(self, width=620)
    def draw(self, context):
        layout = self.layout
        item = selected_message(context)
        if item is None:
            layout.label(text='대화가 종료되었습니다', icon='INFO')
            return
        layout.label(text='나' if item.role == 'user' else assistant_name(context),
                     icon='USER' if item.role == 'user' else 'COMMUNITY')
        lines = self._lines(context)
        pages = max(1, (len(lines) + 19) // 20)
        index = min(self.page, pages) - 1
        for line in lines[index * 20:(index + 1) * 20]:
            layout.label(text=line)
        layout.separator()
        row = layout.row(align=True)
        row.prop(self, "page")
        row.label(text=f"/ {pages}")
        row.operator("twin.copy_reply", text="복사", icon='COPYDOWN')
    def execute(self, context):
        return {'FINISHED'}


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
    # Keep the legacy metadata workflow out of the primary AI chat tab.
    bl_category = "Metadata"
    def draw(self, context):
        layout = self.layout
        wm, scene = context.window_manager, context.scene
        columns = panel_columns(context)
        layout.use_property_split = False
        layout.use_property_decorate = False

        header = layout.row(align=True)
        header.label(text=assistant_name(context) + (' · 연결됨' if _account else ' · 연결 전'),
                     icon='CHECKMARK' if _account else 'WORLD')
        header.prop(wm, 'twin_show_connection', text='', icon='PREFERENCES', emboss=False)
        if not _account or wm.twin_show_connection:
            box = layout.box()
            box.label(text="AI 연결", icon='WORLD')
            controls = box.column()
            controls.enabled = not _busy
            controls.prop(wm, 'twin_provider', text='')
            if wm.twin_provider in ('OPENAI_API', 'ANTHROPIC_API'):
                key_row = controls.row()
                key_row.enabled = _runtime is None
                key_row.prop(wm, 'twin_api_key')
                draw_text(box, "API 사용료는 공급자에 별도 청구됩니다. 키는 현재 세션에서만 사용합니다.", columns, 4)
                env_name = 'OPENAI_API_KEY' if wm.twin_provider == 'OPENAI_API' else 'ANTHROPIC_API_KEY'
                draw_text(box, "키를 비우면 " + env_name + " 환경 변수를 사용합니다.", columns, 3)
            elif not _account:
                draw_text(box, '기존 CLI 계정을 연결하거나 공식 로그인으로 시작하세요.', columns, 3)
            row = box.row(align=True)
            row.scale_y = 1.15
            row.operator('twin.connect', text='연결 확인', icon='LINKED')
            if wm.twin_provider in ('CODEX', 'CLAUDE_LOGIN'):
                row.operator('twin.login', text='로그인', icon='USER')
            if _runtime:
                box.operator('twin.disconnect', text='연결 닫기', icon='UNLINKED')
            if _account:
                model_row = box.row()
                model_row.enabled = not _busy
                model_row.prop(scene, 'twin_model')
                if wm.twin_provider == 'CLAUDE_LOGIN':
                    draw_text(box, 'CLI 모델 별칭 · 사용 가능 여부는 요청 시 확인합니다.', columns, 3)
        elif _models:
            row = layout.row()
            row.enabled = not _busy
            row.prop(scene, 'twin_model', text='모델')

        if _busy:
            box = layout.box()
            elapsed = max(0, int(time.monotonic() - _busy_started)) if _busy_started else 0
            title = '취소하는 중' if _status_kind == 'CANCELLING' else ('답변을 기다리는 중' if _request_context else '연결 확인 중')
            box.label(text=f'{title} · {elapsed}초', icon='TIME')
            draw_text(box, ('장면을 그대로 두면 완료 후 요청한 작업을 실행합니다.'
                            if _request_context and _status_kind != 'CANCELLING' else _status),
                      columns, 3)
        elif _status_kind in ('ERROR', 'CANCELLED'):
            box = layout.box()
            box.alert = _status_kind == 'ERROR'
            box.label(text='요청을 완료하지 못했습니다' if _status_kind == 'ERROR' else '요청을 취소했습니다',
                      icon='ERROR' if _status_kind == 'ERROR' else 'INFO')
            draw_text(box, _status, columns, 4)
            if _status_kind == 'ERROR':
                draw_text(box, '입력은 남겨두었습니다. 선택 객체와 연결 상태를 확인한 뒤 다시 보내세요.', columns, 4)
        elif not _account and _status != '연결 전 · 오프라인 기능 사용 가능':
            draw_text(layout, _status, columns, 4)

        layout.separator()
        title = layout.row(align=True)
        title.label(text='대화', icon='COMMUNITY')
        if wm.twin_chat:
            title.label(text=f'{len(wm.twin_chat) // 2} / 10')
            title.operator('twin.new_chat', text='', icon='ADD')
            layout.template_list('TWIN_UL_chat', '', wm, 'twin_chat', wm, 'twin_chat_index', rows=3, maxrows=3)
            item = selected_message(context)
            box = layout.box()
            top = box.row(align=True)
            top.label(text='나의 요청' if item.role == 'user' else assistant_name(context) + ' 답변',
                      icon='USER' if item.role == 'user' else 'COMMUNITY')
            top.operator('twin.copy_reply', text='', icon='COPYDOWN')
            top.operator('twin.view_reply', text='', icon='FULLSCREEN_ENTER')
            if item.result:
                result = box.box()
                result.alert = item.outcome == 'FAILED'
                result.label(text={'APPLIED': '실제 실행 결과', 'NO_ACTION': '장면 변경 없음',
                                   'FAILED': '실행 실패'}.get(item.outcome, '실행 결과'),
                             icon='ERROR' if item.outcome == 'FAILED' else ('CHECKMARK' if item.outcome == 'APPLIED' else 'INFO'))
                draw_text(result, item.result, columns - 4, 4)
            truncated = draw_text(box, item.body or item.content, columns - 2, 6)
            if truncated:
                box.operator('twin.view_reply', text='전체 메시지 읽기', icon='TEXT')
        else:
            box = layout.box()
            box.label(text='무엇을 만들고 싶으세요?', icon='OUTLINER_OB_MESH')
            draw_text(box, '예시를 골라 수정해보세요. 전송 버튼을 누르면 작업을 시작합니다.', columns, 4)
            examples = [
                ('큐브 만들기', '원점에 큐브 하나를 만들어줘.', 'MESH_CUBE'),
                ('선택 객체 이동', '선택한 객체를 X축으로 2만큼 옮겨줘.', 'ORIENTATION_LOCAL'),
                ('이름 정리하기', '선택한 객체의 이름을 Sample로 바꿔줘.', 'SORTALPHA')]
            for label, prompt, icon in examples:
                button = box.operator('twin.seed_prompt', text=label, icon=icon)
                button.prompt = prompt

        context_box = layout.box()
        row = context_box.row(align=True)
        row.enabled = not _busy
        row.prop(scene, 'twin_chat_selection', text='선택 객체 함께 보내기')
        objects = list(context.selected_objects)
        if scene.twin_chat_selection and objects:
            context_box.label(text=f'{len(objects)}개 선택됨', icon='OBJECT_DATA')
            names = ', '.join(obj.name for obj in objects[:3])
            if len(objects) > 3:
                names += f' 외 {len(objects) - 3}개'
            draw_text(context_box, names, columns, 2)
        else:
            draw_text(context_box, '선택 객체 없이 새 오브젝트 생성이나 질문을 할 수 있습니다.', columns, 3)

        composer = layout.column(align=True)
        composer.enabled = not _busy
        composer.label(text='작업 요청')
        row = composer.row()
        row.scale_y = 1.3
        row.prop(wm, 'twin_prompt', text='')
        actions = layout.row(align=True)
        actions.scale_y = 1.45
        if _busy and _request_context is not None:
            actions.operator('twin.cancel_chat', text='요청 취소', icon='CANCEL')
        else:
            actions.operator('twin.propose', text='보내기', icon='PLAY')
        if not _account:
            draw_text(layout, 'AI를 연결하면 요청을 보낼 수 있습니다.', columns, 2)
        elif len(wm.twin_chat) >= MAX_HISTORY:
            draw_text(layout, '대화가 가득 찼습니다. + 버튼으로 새 대화를 시작하세요.', columns, 3)
        footer = layout.column(align=True)
        footer.label(text='Ctrl+Z로 실행 취소')
        footer.label(text='대화는 세션에만 보관')


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
    global _runtime, _account, _busy, _request_context, _status, _models, _model_items, _login_url, _pending_response, _login_refresh, _status_kind, _busy_started
    runtime, _runtime = _runtime, None
    _pending_response = None
    _login_refresh = False
    _status_kind = 'IDLE'
    _busy_started = 0.0
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


CLASSES = (TwinPreferences, TwinChatItem, TWIN_UL_chat, TWIN_OT_connect, TWIN_OT_login,
           TWIN_OT_disconnect, TWIN_OT_propose, TWIN_OT_cancel_chat, TWIN_OT_new_chat,
           TWIN_OT_execute_chat, TWIN_OT_seed_prompt, TWIN_OT_copy_reply, TWIN_OT_view_reply,
           TWIN_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.twin_provider = EnumProperty(name="연결 방식", items=[
        ('CODEX', 'ChatGPT 계정 (Codex)', '로컬 Codex 로그인 사용'),
        ('CLAUDE_LOGIN', 'Claude 계정 (Claude Code)', 'Claude Code 공식 계정 로그인'),
        ('OPENAI_API', 'OpenAI API', 'OpenAI API 키로 연결'),
        ('ANTHROPIC_API', 'Claude API', 'Anthropic API 키로 연결')],
        default='CODEX', update=provider_changed, options={'SKIP_SAVE'})
    bpy.types.WindowManager.twin_show_connection = BoolProperty(name="연결 설정", default=False,
        description="AI 제공자, 로그인 방식과 모델 설정을 표시합니다", options={'SKIP_SAVE'})
    bpy.types.WindowManager.twin_api_key = StringProperty(name="API 키", subtype='PASSWORD',
                                                        options={'SKIP_SAVE'})
    bpy.types.Scene.twin_model = EnumProperty(name="모델", items=model_items)
    bpy.types.WindowManager.twin_prompt = StringProperty(name="요청", default="", maxlen=8000, options={"SKIP_SAVE"})
    bpy.types.WindowManager.twin_chat = CollectionProperty(type=TwinChatItem, options={'SKIP_SAVE'})
    bpy.types.WindowManager.twin_chat_index = IntProperty(default=0, options={'SKIP_SAVE'})
    bpy.types.Scene.twin_chat_selection = BoolProperty(name="선택 객체 포함", default=True)
    bpy.app.handlers.load_pre.append(on_load)
    bpy.app.timers.register(poll_events, first_interval=0.4, persistent=True)


def unregister():
    stop_runtime()
    if bpy.app.timers.is_registered(poll_events):
        bpy.app.timers.unregister(poll_events)
    if on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(on_load)
    for name in ("twin_model", "twin_chat_selection"):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for name in ('twin_chat', 'twin_chat_index', 'twin_prompt', 'twin_provider', 'twin_api_key', 'twin_show_connection'):
        if hasattr(bpy.types.WindowManager, name):
            delattr(bpy.types.WindowManager, name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
