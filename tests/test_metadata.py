import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "twin_metadata", Path(__file__).resolve().parents[1] / "blender_with_ai" / "metadata.py"
)
metadata = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metadata)


class MetadataTests(unittest.TestCase):
    def test_fields_reject_bad_types_unknown_fields_and_values(self):
        for fields in [None, [], {"object_id": "replace-id"}, {"floor": {"value": "B1"}},
                       {"floor": float("nan")}, {"floor": float("inf")},
                       {"zone": True}, {"source": "x" * 257}, {"floor": "B1\x00"},
                       {"object_type": "safe"}, {"review_status": "APPROVED"}]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                metadata.validate_fields(fields)

    def test_ai_cannot_verify_and_does_not_mutate_input(self):
        original = {"object_type": "DOOR", "review_status": "UNREVIEWED"}
        result = metadata.validate_fields(original, from_ai=True)
        self.assertEqual(result["review_status"], "NEEDS_REVIEW")
        self.assertEqual(original["review_status"], "UNREVIEWED")
        with self.assertRaises(ValueError):
            metadata.validate_fields({"review_status": "VERIFIED"}, from_ai=True)

    def test_proposals_bound_to_selection_and_strict_shape(self):
        valid = {"object_name": "Door", "fields": {"object_type": "DOOR"}}
        self.assertEqual(metadata.validate_proposal([valid], ["Door"])[0]["fields"],
                         {"object_type": "DOOR", "review_status": "NEEDS_REVIEW"})
        for raw in [[valid, valid], [{**valid, "object_name": "Other"}],
                    [{**valid, "code": "delete_all()"}], {"updates": [valid]},
                    '[{"object_name":"Door","fields":{"floor":NaN}}]',
                    '[{"object_name":"Door","object_name":"Door","fields":{}}]',
                    [{"object_name": "Door", "fields": {"script": "import os"}}]]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                metadata.validate_proposal(raw, ["Door"])

    def test_json_proposal(self):
        result = metadata.validate_proposal('[{"object_name":"문","fields":{"floor":"B1"}}]', ["문"])
        self.assertEqual(result[0]["fields"]["floor"], "B1")
        for raw in ['```json\n[]\n```', '{broken', 'null']:
            with self.assertRaises(ValueError):
                metadata.validate_proposal(raw, ["문"])

    def test_export_preserves_identity_after_rename(self):
        original = {"object_id": "persistent-uuid", "object_name": "Renamed Door", "floor": "B1"}
        result = metadata.build_export([original])
        self.assertEqual(result, {"schema_version": 1, "objects": [original]})
        self.assertIsNot(result["objects"][0], original)
        with self.assertRaises(ValueError):
            metadata.build_export([original, {**original, "object_name": "Duplicate"}])
        for record in [{"object_name": "Door"}, {**original, "object_id": ""},
                       {**original, "can_evacuate": True}]:
            with self.assertRaises(ValueError):
                metadata.build_export([record])

    def test_csv_id_only_and_bom_partial_updates(self):
        self.assertEqual(metadata.parse_csv('\ufeffobject_id,floor,zone\nuuid-1,B1,\n'),
                         [{"object_id": "uuid-1", "floor": "B1"}])
        for text in ['object_name,floor\nDoor,B1\n', 'object_id,object_name\nid,Door\n',
                     'object_id,floor\nid,B1\nid,1F\n', 'object_id,floor\n,B1\n',
                     'object_id,floor,floor\nid,B1,B2\n', 'object_id,floor\nid,B1,extra\n',
                     'object_id,floor\nid\n', 'object_id,floor\nid,"unclosed',
                     'object_id,object_type\nid,UNSAFE\n']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                metadata.parse_csv(text)

    def test_name_rules_conservative_and_reviewable(self):
        self.assertEqual(metadata.suggest_by_name("B1_DOOR_001"),
                         {"object_type": "DOOR", "floor": "B1", "source": "name_rule",
                          "review_status": "NEEDS_REVIEW"})
        self.assertEqual(metadata.suggest_by_name("지하2층_계단_01")["floor"], "B2")
        self.assertEqual(metadata.suggest_by_name("1층_출입문_02")["object_type"], "DOOR")
        self.assertEqual(metadata.suggest_by_name("DOORMAT_001")["object_type"], "UNKNOWN")
        self.assertEqual(metadata.suggest_by_name("DOOR_STAIR_001")["object_type"], "UNKNOWN")
        self.assertNotIn("floor", metadata.suggest_by_name("B1_B2_DOOR"))
        self.assertNotIn("width", metadata.suggest_by_name("DOOR_900"))


if __name__ == "__main__":
    unittest.main()
