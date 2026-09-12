"""Synthetic geometry/contract and real CLI refusal tests; no native EDA needed."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_layout.py"
SPEC = importlib.util.spec_from_file_location("layout_checker", SCRIPT)
layout = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(layout)


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.e = json.loads((ROOT / "assets/examples/layout-requirements.json").read_text(encoding="utf-8"))
        self.a = json.loads((ROOT / "assets/examples/layout-actual.json").read_text(encoding="utf-8"))

    def codes(self):
        return {f["code"] for f in layout.check(self.e, self.a)["findings"]}

    def test_example_passes_with_explicit_overhang(self):
        r = layout.check(self.e, self.a)
        self.assertEqual("PASS", r["status"])
        self.assertTrue(r["checked"])
        self.assertEqual([], r["findings"])
        self.assertGreater(r["counts"]["checksEvaluated"], 10)

    def test_named_point_cannot_silently_change_component_owner(self):
        self.a["points"][0]["componentId"] = "C1"
        self.assertIn("POINT_OWNER_MISMATCH", self.codes())

    def test_distance_requirement_owner_must_exist(self):
        self.e["maxDistances"][0]["fromComponentId"] = "U404"
        with self.assertRaises(layout.InputError):
            layout.check(self.e, self.a)

    def test_overhang_zero_or_omitted_refuses_and_limit_is_not_unbounded(self):
        for value in (None, {"left": 0, "right": 0, "top": 0, "bottom": 0},
                      {"left": 0.99, "right": 0, "top": 0, "bottom": 0}):
            with self.subTest(value=value):
                if value is None:
                    self.e["components"][0].pop("allowedOverhangMm", None)
                else:
                    self.e["components"][0]["allowedOverhangMm"] = value
                self.assertIn("OUTSIDE_BOARD", self.codes())

    def test_overhang_each_global_edge(self):
        c = self.a["components"][1]
        cases = [("left", "xMinMm", -11), ("right", "xMaxMm", 11),
                 ("top", "yMaxMm", 7), ("bottom", "yMinMm", -7)]
        for edge, key, value in cases:
            with self.subTest(edge=edge):
                c["envelope"] = {"xMinMm": -1, "yMinMm": -1, "xMaxMm": 1, "yMaxMm": 1}
                c["envelope"][key] = value
                self.e["components"][1]["allowedOverhangMm"] = {s: int(s == edge) for s in ("left", "right", "top", "bottom")}
                self.assertNotIn("OUTSIDE_BOARD", self.codes())

    def test_board_coordinates_and_document_binding(self):
        self.a["board"]["xMaxMm"] += 0.02
        self.a["documentId"] = "wrong-pcb"
        self.assertTrue({"BOARD_MISMATCH", "DOCUMENT_MISMATCH"} <= self.codes())

    def test_board_tolerance(self):
        self.a["board"]["xMaxMm"] += 0.005
        self.assertNotIn("BOARD_MISMATCH", self.codes())

    def test_wrong_connector_direction_and_anchor(self):
        self.a["components"][0]["connector"] = {"anchor": {"xMm": -8, "yMm": 0}, "directionDeg": 0}
        self.assertTrue({"CONNECTOR_DIRECTION_MISMATCH", "CONNECTOR_ANCHOR_MISMATCH"} <= self.codes())

    def test_angle_wrap_and_tolerance(self):
        self.e["components"][0]["connector"]["directionDeg"] = 359
        self.a["components"][0]["connector"]["directionDeg"] = 1
        self.assertNotIn("CONNECTOR_DIRECTION_MISMATCH", self.codes())
        self.a["components"][0]["connector"]["directionDeg"] = 2
        self.assertIn("CONNECTOR_DIRECTION_MISMATCH", self.codes())

    def test_fixed_position_uses_radial_distance(self):
        self.e["components"][1]["fixedPosition"] = {"xMm": 0, "yMm": 0, "toleranceMm": 5}
        self.a["components"][1]["position"] = {"xMm": 3, "yMm": 4}
        self.assertNotIn("FIXED_POSITION_MISMATCH", self.codes())
        self.e["components"][1]["fixedPosition"]["toleranceMm"] = 4.99
        self.assertIn("FIXED_POSITION_MISMATCH", self.codes())

    def test_side_and_through_board_expectation(self):
        self.a["components"][1]["side"] = "BOTTOM"
        self.a["components"][1]["throughBoard"] = True
        self.assertTrue({"SIDE_MISMATCH", "THROUGH_BOARD_MISMATCH"} <= self.codes())

    def test_keepout_matches_physical_envelope_not_origin(self):
        self.a["components"][1]["envelope"]["xMaxMm"] = 5.01
        self.assertIn("KEEPOUT_INTERSECTION", self.codes())

    def test_keepout_boundary_touch_is_forbidden(self):
        self.a["components"][1]["envelope"]["xMaxMm"] = 5
        self.assertIn("KEEPOUT_INTERSECTION", self.codes())
        self.a["components"][1]["envelope"]["xMaxMm"] = 4.999
        self.assertNotIn("KEEPOUT_INTERSECTION", self.codes())

    def test_opposite_face_clear_but_through_board_hits(self):
        self.a["components"][1]["envelope"]["yMaxMm"] = 3.1
        self.assertNotIn("KEEPOUT_INTERSECTION", self.codes())
        self.a["components"][1]["throughBoard"] = True
        self.assertIn("KEEPOUT_INTERSECTION", self.codes())

    def test_distance_failure_and_exact_limit(self):
        self.e["maxDistances"][0]["maxMm"] = 1.5
        self.assertNotIn("POINT_DISTANCE_EXCEEDED", self.codes())
        self.e["maxDistances"][0]["maxMm"] = 1.49
        self.assertIn("POINT_DISTANCE_EXCEEDED", self.codes())

    def test_inventory_missing_and_extra(self):
        self.e["maxDistances"] = []
        self.a["points"] = []
        self.a["components"][2]["id"] = "C99"
        self.assertTrue({"COMPONENT_MISSING", "COMPONENT_EXTRA"} <= self.codes())

    def test_empty_and_duplicate_objects_rejected(self):
        for which in ("requirements", "actual"):
            for kind in ("empty", "duplicate"):
                with self.subTest(which=which, kind=kind):
                    e, a = copy.deepcopy(self.e), copy.deepcopy(self.a)
                    d = e if which == "requirements" else a
                    d["components"] = [] if kind == "empty" else d["components"] + [copy.deepcopy(d["components"][0])]
                    with self.assertRaises(layout.InputError):
                        layout.check(e, a)

    def test_missing_measurement_is_error_not_board_pass(self):
        del self.a["components"][0]["connector"]
        with self.assertRaises(layout.InputError):
            layout.check(self.e, self.a)
        self.setUp()
        self.a["points"] = []
        with self.assertRaises(layout.InputError):
            layout.check(self.e, self.a)

    def test_point_requires_existing_owner(self):
        self.a["points"][0]["componentId"] = "missing"
        with self.assertRaises(layout.InputError):
            layout.check(self.e, self.a)

    def test_sources_and_complete_inventory_are_required(self):
        mutations = [lambda e, a: e["source"].update(confirmed=False),
                     lambda e, a: a["source"].update(confirmed=False),
                     lambda e, a: e["source"].update(independentOfActual=False),
                     lambda e, a: a["source"].update(id=e["source"]["id"]),
                     lambda e, a: a.update(inventoryComplete=False)]
        for mutation in mutations:
            e, a = copy.deepcopy(self.e), copy.deepcopy(self.a)
            mutation(e, a)
            with self.subTest(mutation=mutation), self.assertRaises(layout.InputError):
                layout.check(e, a)

    def test_unknown_fields_units_frames_and_types(self):
        mutations = [lambda e, a: e.update(uncheckedRule=True),
                     lambda e, a: a["components"][0]["envelope"].update(rotationDeg=30),
                     lambda e, a: e["source"].update(unverified={"NaN": float("nan")}),
                     lambda e, a: a.update(units="mil"),
                     lambda e, a: a.update(coordinates="canvas-y-down"),
                     lambda e, a: a["components"][0].update(side="BOTH"),
                     lambda e, a: a["components"][0].update(throughBoard=1),
                     lambda e, a: e["board"].update(toleranceMm=True),
                     lambda e, a: a["components"][0]["connector"].update(directionDeg=360),
                     lambda e, a: e["components"][0]["allowedOverhangMm"].update(left=-1),
                     lambda e, a: e["keepouts"][0].update(sides=[]),
                     lambda e, a: a["board"].update(xMaxMm=-10)]
        for mutation in mutations:
            e, a = copy.deepcopy(self.e), copy.deepcopy(self.a)
            mutation(e, a)
            with self.subTest(mutation=mutation), self.assertRaises(layout.InputError):
                layout.check(e, a)

    def test_findings_stable_when_arrays_reordered(self):
        self.a["components"][0]["connector"]["directionDeg"] = 0
        self.a["components"][1]["envelope"]["xMaxMm"] = 5.1
        first = layout.check(self.e, self.a)
        self.e["components"].reverse()
        self.e["keepouts"].reverse()
        self.a["components"].reverse()
        self.a["points"].reverse()
        self.assertEqual(first, layout.check(self.e, self.a))

    def cli(self, folder, e=None, a=None, out=None):
        folder = Path(folder)
        ep, ap = folder / "requirements.json", folder / "actual.json"
        ep.write_text(json.dumps(self.e if e is None else e), encoding="utf-8")
        ap.write_text(json.dumps(self.a if a is None else a), encoding="utf-8")
        op = Path(out) if out else folder / "new" / "report.json"
        result = subprocess.run([sys.executable, str(SCRIPT), str(ep), str(ap), "--out", str(op)],
                                capture_output=True, text=True)
        return result, ep, ap, op

    def test_cli_pass_fail_hashes_and_new_directory(self):
        with tempfile.TemporaryDirectory() as td:
            r, ep, ap, op = self.cli(td)
            self.assertEqual(0, r.returncode, r.stderr)
            report = json.loads(op.read_text(encoding="utf-8"))
            for role, path in (("requirements", ep), ("actual", ap)):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), report["inputs"][role]["sha256"])
            self.a["components"][0]["connector"]["directionDeg"] = 0
            r, _, _, op = self.cli(td)
            self.assertEqual(1, r.returncode)
            self.assertEqual("FAIL", json.loads(op.read_text(encoding="utf-8"))["status"])

    def test_cli_nonfinite_and_duplicate_json_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as td:
                r, ep, ap, op = self.cli(td)
                text = ap.read_text(encoding="utf-8").replace('"xMinMm": -10', '"xMinMm": ' + value)
                ap.write_text(text, encoding="utf-8")
                r = subprocess.run([sys.executable, str(SCRIPT), str(ep), str(ap), "--out", str(op)], capture_output=True, text=True)
                self.assertEqual(2, r.returncode, r.stderr)
                self.assertFalse(json.loads(op.read_text(encoding="utf-8"))["checked"])
        with tempfile.TemporaryDirectory() as td:
            _, ep, ap, op = self.cli(td)
            ap.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
            r = subprocess.run([sys.executable, str(SCRIPT), str(ep), str(ap), "--out", str(op)], capture_output=True, text=True)
            self.assertEqual(2, r.returncode)
            self.assertIn("Duplicate JSON key", r.stderr)

    def test_cli_output_alias_and_hardlink_cannot_touch_sources(self):
        for source_name in ("requirements.json", "actual.json"):
            for invalid in (False, True):
                for hardlink in (False, True):
                    with self.subTest(source=source_name, invalid=invalid, hardlink=hardlink), tempfile.TemporaryDirectory() as td:
                        _, ep, ap, _ = self.cli(td)
                        if invalid:
                            ap.write_text("{bad json", encoding="utf-8")
                        source = Path(td) / source_name
                        out = Path(td) / "alias.json" if hardlink else source
                        if hardlink:
                            os.link(source, out)
                        before = {p: p.read_bytes() for p in (ep, ap)}
                        r = subprocess.run([sys.executable, str(SCRIPT), str(ep), str(ap), "--out", str(out)], capture_output=True, text=True)
                        self.assertEqual(2, r.returncode, r.stderr)
                        self.assertIn("aliases an input", r.stderr)
                        self.assertEqual(before, {p: p.read_bytes() for p in (ep, ap)})

    def test_cli_missing_arguments_machine_readable(self):
        r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(2, r.returncode)
        self.assertEqual("ERROR", json.loads(r.stderr)["status"])


if __name__ == "__main__":
    unittest.main()
