"""Real Blender validation of scene commands, stale requests and atomic rollback."""
import sys
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blender_with_ai import scene_actions


def command(op, target="", vector=(0, 0, 0), name="", primitive="CUBE"):
    return dict(operation=op, target=target, vector=list(vector), name=name, primitive=primitive)


def rejected(commands, saved):
    try:
        scene_actions.apply_actions(bpy.context, commands, saved)
    except ValueError:
        return
    raise AssertionError("Expected rejection")

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
records, saved = scene_actions.snapshot(bpy.context)
assert records == []
scene_actions.apply_actions(bpy.context, [command("create", name="ChatCube")], saved)
obj = bpy.context.object
assert obj.name == "ChatCube" and obj.select_get()
records, saved = scene_actions.snapshot(bpy.context)
scene_actions.apply_actions(bpy.context, [command("move", obj.name, (2, 3, 4)),
    command("rotate", obj.name, (0, 0, 90)), command("scale", obj.name, (2, 2, 2)),
    command("rename", obj.name, name="Renamed")], saved)
assert tuple(obj.location) == (2, 3, 4) and tuple(obj.scale) == (2, 2, 2)
assert abs(obj.rotation_euler.z - 1.5707963) < 1e-6 and obj.name == "Renamed"
records, saved = scene_actions.snapshot(bpy.context)
obj.location.x += 1
rejected([command("move", "Renamed", (1, 0, 0))], saved)
records, saved = scene_actions.snapshot(bpy.context)
obj.lock_location[0] = True
rejected([command("move", "Renamed", (1, 0, 0))], saved)
obj.lock_location[0] = False
records, saved = scene_actions.snapshot(bpy.context)
# Force a later execution error to verify earlier mutations roll back.
original = tuple(obj.location)
real_bpy = scene_actions.bpy
mesh_ops = SimpleNamespace(primitive_cube_add=lambda **kw: {"CANCELLED"},
    primitive_uv_sphere_add=bpy.ops.mesh.primitive_uv_sphere_add,
    primitive_cylinder_add=bpy.ops.mesh.primitive_cylinder_add,
    primitive_plane_add=bpy.ops.mesh.primitive_plane_add,
    primitive_cone_add=bpy.ops.mesh.primitive_cone_add)
with patch.object(scene_actions, "bpy", SimpleNamespace(data=bpy.data, ops=SimpleNamespace(mesh=mesh_ops))):
    rejected([command("move", obj.name, (5, 0, 0)), command("create")], saved)
assert tuple(obj.location) == original
assert bpy.context.object == obj and obj.select_get()
for primitive in ("UV_SPHERE", "CYLINDER", "PLANE", "CONE"):
    records, saved = scene_actions.snapshot(bpy.context)
    scene_actions.apply_actions(bpy.context, [command("create", primitive=primitive)], saved)
assert len(bpy.context.scene.objects) == 5
print("BLENDER_ACTIONS_SMOKE_OK: primitives, transforms, rename, stale rejection, lock rejection, rollback")
