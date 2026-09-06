import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("actions", Path(__file__).resolve().parents[1] / "blender_with_ai/actions.py")
actions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(actions)


def command(**kwargs):
    return {"operation": "move", "target": "Cube", "name": "", "primitive": "CUBE", "vector": [1, 0, 0], **kwargs}


class ActionTests(unittest.TestCase):
    def validate(self, commands):
        return actions.validate_response({"message": "Done", "actions": commands}, [{"object_name": "Cube"}])

    def test_supported_commands_and_chat(self):
        for op in ("move", "rotate", "scale"):
            self.validate([command(operation=op, vector=[1, 1, 1])])
        self.validate([command(operation="rename", name="문", vector=[0, 0, 0])])
        actions.validate_response({"message": "Hi", "actions": [command(operation="create", target="")]}, [])
        self.assertEqual(self.validate([]), ("Done", []))

    def test_untrusted_commands_rejected(self):
        for cmd in [command(operation="python"), command(target="Other"),
                    command(vector=[float("nan"), 0, 0]), command(vector=[True, 0, 0]),
                    command(vector=[1e9, 0, 0]), command(vector=[0, 0]),
                    command(operation="scale", vector=[0, 1, 1]),
                    command(operation="rename", name=""), command(name="unexpected"),
                    command(code="import os"), command(operation="create", target="Cube"),
                    command(primitive="UV_SPHERE"), command(name="x\x00")]:
            with self.subTest(cmd=cmd), self.assertRaises(ValueError):
                self.validate([cmd])
        with self.assertRaises(ValueError):
            self.validate([command()] * 21)

    def test_response_shape_duplicate_keys_and_copy(self):
        with self.assertRaises(ValueError):
            actions.validate_response('{"message":"a","message":"b","actions":[]}', [])
        with self.assertRaises(ValueError):
            actions.validate_response({"message": "a", "actions": [], "code": "bad"}, [])
        original = command()
        _, result = self.validate([original])
        result[0]["vector"][0] = 9
        self.assertEqual(original["vector"][0], 1)
