"""Execute the limited action vocabulary on Blender's main thread."""
import math
import bpy
from .actions import validate_response


def _state(obj):
    return (obj.name, tuple(obj.location), tuple(obj.rotation_euler), tuple(obj.scale),
            obj.rotation_mode, obj.parent.as_pointer() if obj.parent else 0,
            tuple(obj.matrix_world[i][j] for i in range(4) for j in range(4)))


def snapshot(context):
    records, snapshots = [], {}
    for obj in context.selected_objects:
        records.append({"object_name": obj.name, "type": obj.type,
                        "location": list(obj.location),
                        "rotation_degrees": [math.degrees(v) for v in obj.rotation_euler],
                        "scale": list(obj.scale)})
        snapshots[obj.name] = {"object": obj, "pointer": obj.as_pointer(),
                               "state": _state(obj), "scene": context.scene.as_pointer()}
    return records, snapshots


def apply_actions(context, commands, snapshots):
    _, commands = validate_response({"message": "", "actions": commands},
                                    [{"object_name": name} for name in snapshots])
    if not commands:
        return "장면 변경 없음."
    previous_mode = context.mode
    # Object-level commands are safe from sculpt/paint modes. Edit mode is
    # deliberately left alone because switching it can disrupt mesh edits.
    restorable_modes = {"SCULPT", "PAINT_VERTEX", "PAINT_WEIGHT", "PAINT_TEXTURE"}
    if previous_mode != "OBJECT":
        if previous_mode not in restorable_modes:
            raise ValueError("현재 모드에서는 실행할 수 없습니다. 오브젝트 모드로 전환해 주세요.")
        if context.object is None or bpy.ops.object.mode_set(mode="OBJECT") != {"FINISHED"}:
            raise ValueError("오브젝트 모드로 전환하지 못했습니다.")
    for name, saved in snapshots.items():
        obj = saved["object"]
        try:
            fresh = (saved["scene"] == context.scene.as_pointer()
                     and context.scene.objects.get(name) == obj
                     and obj.as_pointer() == saved["pointer"] and _state(obj) == saved["state"])
        except ReferenceError:
            fresh = False
        if not fresh:
            raise ValueError("요청 이후 장면이 변경됐습니다. 다시 요청해 주세요.")
    for command in commands:
        if command["operation"] == "create":
            if not context.collection.is_editable:
                raise ValueError("현재 컬렉션을 편집할 수 없습니다.")
            continue
        obj = snapshots[command["target"]]["object"]
        op = command["operation"]
        if not obj.is_editable or obj.override_library:
            raise ValueError("연결되거나 편집할 수 없는 오브젝트입니다.")
        if op in ("move", "rotate", "scale"):
            if obj.constraints or obj.animation_data:
                raise ValueError("제약 조건이나 애니메이션이 있는 오브젝트는 지원하지 않습니다.")
            locks = {"move": obj.lock_location, "rotate": obj.lock_rotation,
                     "scale": obj.lock_scale}[op]
            if any(locks) or (op == "rotate" and obj.rotation_mode != "XYZ"):
                raise ValueError("변환 잠금 또는 지원하지 않는 회전 모드입니다.")
        if op == "rename":
            other = bpy.data.objects.get(command["name"])
            if other is not None and other != obj:
                raise ValueError("이미 존재하는 오브젝트 이름입니다.")
    # Plan resultant transforms before touching any data, including cumulative overflow.
    planned = {name: [list(s["state"][1]), list(s["state"][2]), list(s["state"][3])]
               for name, s in snapshots.items()}
    reserved = set(bpy.data.objects.keys())
    for cmd in commands:
        op, name = cmd["operation"], cmd["name"]
        if op in ("create", "rename") and name:
            if name in reserved and not (op == "rename" and name == cmd["target"]):
                raise ValueError("중복된 오브젝트 이름입니다.")
            reserved.add(name)
        if op in ("move", "rotate", "scale"):
            axis = {"move": 0, "rotate": 1, "scale": 2}[op]
            old = planned[cmd["target"]][axis]
            values = [a * b if op == "scale" else a + (math.radians(b) if op == "rotate" else b)
                      for a, b in zip(old, cmd["vector"])]
            if any(not math.isfinite(v) or abs(v) > 1e7 or (op == "scale" and abs(v) < 1e-8) for v in values):
                raise ValueError("누적 변환이 허용 범위를 초과했습니다.")
            planned[cmd["target"]][axis] = values
    selected, active = list(context.selected_objects), context.view_layer.objects.active
    created = []
    results = []
    meshes_before = set(bpy.data.meshes.keys())
    try:
        for cmd in commands:
            op = cmd["operation"]
            if op == "create":
                operator = {"CUBE": bpy.ops.mesh.primitive_cube_add,
                            "UV_SPHERE": bpy.ops.mesh.primitive_uv_sphere_add,
                            "CYLINDER": bpy.ops.mesh.primitive_cylinder_add,
                            "PLANE": bpy.ops.mesh.primitive_plane_add,
                            "CONE": bpy.ops.mesh.primitive_cone_add}[cmd["primitive"]]
                if operator(location=cmd["vector"], enter_editmode=False) != {"FINISHED"}:
                    raise ValueError("오브젝트 생성 실패")
                obj = context.object
                created.append(obj)
                if cmd["name"]:
                    obj.name = cmd["name"]
                    if obj.name != cmd["name"]:
                        raise ValueError("요청한 이름을 사용할 수 없습니다.")
                shape = {"CUBE": "큐브", "UV_SPHERE": "구", "CYLINDER": "원기둥",
                         "PLANE": "평면", "CONE": "원뿔"}[cmd["primitive"]]
                location = ", ".join(f"{axis} {value:.6g}" for axis, value in zip("XYZ", obj.location))
                results.append(f"생성 · {shape} {obj.name}: 위치 {location}")
            else:
                obj = snapshots[cmd["target"]]["object"]
                previous_name = obj.name
                if op == "rename":
                    obj.name = cmd["name"]
                elif op == "move":
                    obj.location = [a + b for a, b in zip(obj.location, cmd["vector"])]
                elif op == "rotate":
                    obj.rotation_euler = [a + math.radians(b) for a, b in zip(obj.rotation_euler, cmd["vector"])]
                elif op == "scale":
                    obj.scale = [a * b for a, b in zip(obj.scale, cmd["vector"])]
                if op == "rename":
                    results.append(f"이름 변경 · {previous_name} → {obj.name}")
                else:
                    label = {"move": "이동", "rotate": "회전", "scale": "크기"}[op]
                    if op == "scale":
                        detail = ", ".join(f"{axis} {value * 100:.6g}%" for axis, value in zip("XYZ", cmd["vector"]))
                    else:
                        unit = "°" if op == "rotate" else ""
                        detail = ", ".join(f"{axis} {value:+.6g}{unit}" for axis, value in zip("XYZ", cmd["vector"]))
                    results.append(f"{label} · {obj.name}: {detail}")
        if created:
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in created:
                obj.select_set(True)
            context.view_layer.objects.active = created[-1]
        context.view_layer.update()
    except Exception:
        for obj in created:
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in list(bpy.data.meshes):
            if mesh.name not in meshes_before and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for saved in snapshots.values():
            obj, state = saved["object"], saved["state"]
            obj.name, obj.location, obj.rotation_euler, obj.scale = state[:4]
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in selected:
            obj.select_set(True)
        context.view_layer.objects.active = active
        context.view_layer.update()
        if previous_mode != "OBJECT" and context.object is not None:
            try:
                bpy.ops.object.mode_set(mode=previous_mode)
            except RuntimeError:
                pass
        raise
    if previous_mode != "OBJECT" and context.object is not None:
        try:
            bpy.ops.object.mode_set(mode=previous_mode)
        except RuntimeError:
            pass
    return (f"장면 작업 {len(commands)}개 실행 완료.\n" + "\n".join(results))[:2400]
