"""Run using Blender --background --factory-startup --python this_file."""
import sys
import json
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
import twin_assistant as addon

addon.register()
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
for name, location in (("B1_DOOR_001", (0, 0, 0)), ("1F_STAIR_001", (3, 0, 0))):
    bpy.ops.mesh.primitive_cube_add(location=location)
    bpy.context.object.name = name
bpy.ops.object.select_all(action="SELECT")
assert bpy.ops.twin.init_ids() == {"FINISHED"}
objects = list(bpy.context.selected_objects)
ids = {obj.name: addon.metadata(obj)["object_id"] for obj in objects}
assert len(set(ids.values())) == 2
bpy.ops.twin.init_ids()
assert ids == {obj.name: addon.metadata(obj)["object_id"] for obj in objects}
assert bpy.ops.twin.rules() == {"FINISHED"}
assert len(bpy.context.scene.twin_preview) == 2
assert all(addon.metadata(obj)["object_type"] == "UNKNOWN" for obj in objects)
assert bpy.ops.twin.apply() == {"FINISHED"}
assert addon.metadata(bpy.data.objects["B1_DOOR_001"])["object_type"] == "DOOR"
assert addon.metadata(bpy.data.objects["1F_STAIR_001"])["floor"] == "1F"
assert all(addon.metadata(obj)["review_status"] == "NEEDS_REVIEW" for obj in objects)
# Stale preview is rejected as a whole.
bpy.ops.twin.rules()
objects[0][addon.KEY]["zone"] = "changed"
try:
    result = bpy.ops.twin.apply()
except RuntimeError:
    result = {"CANCELLED"}
assert result == {"CANCELLED"}
bpy.ops.twin.discard()
# ID-matched CSV import remains a preview until explicit apply.
out = root / "test-output"
out.mkdir(exist_ok=True)
csv_file = out / "metadata.csv"
csv_file.write_text("object_id,zone\n" + ids[objects[0].name] + ",WEST\n", encoding="utf-8")
assert bpy.ops.twin.import_csv(filepath=str(csv_file)) == {"FINISHED"}
assert addon.metadata(objects[0]).get("zone") == "changed"
assert bpy.ops.twin.apply() == {"FINISHED"}
assert addon.metadata(objects[0])["zone"] == "WEST"
target = out / "metadata.json"
assert bpy.ops.twin.export(filepath=str(target)) == {"FINISHED"}
exported = json.loads(target.read_text())
assert len(exported["objects"]) == 2
assert {row["object_id"] for row in exported["objects"]} == set(ids.values())
# Save/reopen proves custom properties persist in .blend.
blend_path = out / "sample.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
bpy.ops.wm.open_mainfile(filepath=str(blend_path))
assert addon.metadata(bpy.data.objects["B1_DOOR_001"])["object_id"] == ids["B1_DOOR_001"]
addon.unregister()
addon.register()
addon.unregister()
print("BLENDER_SMOKE_OK: registration, UUID, rule preview/apply, stale rejection, CSV, export, save/reopen")
