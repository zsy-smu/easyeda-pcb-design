"""Placement AABB collision gate tests; no EDA needed."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_placement_collision.py"
SPEC = importlib.util.spec_from_file_location("placement_collision", SCRIPT)
pc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pc)


def doc(components, margin=0.3):
    return {"schema": "placement-collision/v1", "defaultMarginMm": margin,
            "components": components}


def part(cid, x0, y0, x1, y1, side="TOP", through=False):
    return {"id": cid, "side": side, "throughBoard": through,
            "outline": {"xMinMm": x0, "yMinMm": y0, "xMaxMm": x1, "yMaxMm": y1}}


class PlacementTests(unittest.TestCase):
    def test_contained_body_is_fully_separated_by_suggested_translation(self):
        for b in [part('B',4,4,6,6),part('B',.1,2,3,4),part('B',-2,-2,12,12)]:
            a=part('A',0,0,10,10)
            collision=pc.check(doc([a,b],margin=0))['collisions'][0]
            move=collision['separation'];axis=move['axis'];delta=move['distanceMm']*(1 if move['direction'].startswith('+')else-1)
            for edge in ['MinMm','MaxMm']:b['outline'][axis+edge]+=delta
            self.assertTrue(pc.check(doc([a,b],margin=0))['passed'])

    def test_body_outline_not_hidden_by_small_pad_array(self):
        a=part('CAN',0,0,10,10);a['pads']=[{'xMm':5,'yMm':5,'diameterMm':1}]
        self.assertFalse(pc.check(doc([a,part('OTHER',8,8,9,9)],margin=0))['passed'])

    def test_tiny_overlap_not_rounded_away_and_nonfinite_rejected(self):
        self.assertFalse(pc.check(doc([part('A',0,0,1,1),part('B',.9999,0,2,1)],margin=0))['passed'])
        with self.assertRaises(pc.InputError):pc.check(doc([part('A',0,0,float('nan'),1)]))

    def test_clean_placement_passes(self):
        d = doc([part("A", 0, 0, 1, 1), part("B", 5, 5, 6, 6)])
        r = pc.check(d)
        self.assertEqual("PASS", r["status"])
        self.assertEqual([], r["collisions"])

    def test_overlap_detected_with_separation(self):
        d = doc([part("A", 0, 0, 10, 10), part("B", 8, 8, 12, 12)], margin=0)
        r = pc.check(d)
        self.assertEqual("FAIL", r["status"])
        self.assertEqual(1, len(r["collisions"]))
        c = r["collisions"][0]
        self.assertEqual({"a": "A", "b": "B"}, {"a": c["a"], "b": c["b"]})
        # Smaller overlap is on X (2) vs Y (2) -> x; push B +x by 2.
        self.assertEqual("x", c["separation"]["axis"])
        self.assertEqual(2.0, c["separation"]["distanceMm"])

    def test_touching_edges_allowed(self):
        d = doc([part("A", 0, 0, 1, 1), part("B", 1, 0, 2, 1)], margin=0)
        self.assertEqual("PASS", pc.check(d)["status"])

    def test_opposite_sides_no_collision(self):
        d = doc([part("A", 0, 0, 10, 10, side="TOP"),
                 part("B", 5, 5, 12, 12, side="BOTTOM")])
        self.assertEqual("PASS", pc.check(d)["status"])

    def test_through_board_hits_both_sides(self):
        d = doc([part("A", 0, 0, 10, 10, side="TOP"),
                 part("B", 5, 5, 12, 12, side="BOTTOM", through=True)])
        self.assertEqual("FAIL", pc.check(d)["status"])

    def test_collinear_pads_with_diameter(self):
        # Axial part: two pads on the same y, each with a diameter -> valid box.
        d = doc([
            {"id": "R1", "side": "TOP", "throughBoard": False,
             "pads": [{"xMm": 0, "yMm": 0, "diameterMm": 1.5},
                      {"xMm": 5, "yMm": 0, "diameterMm": 1.5}]},
            {"id": "R2", "side": "TOP", "throughBoard": False,
             "pads": [{"xMm": 10, "yMm": 0, "diameterMm": 1.5},
                      {"xMm": 15, "yMm": 0, "diameterMm": 1.5}]},
        ])
        r = pc.check(d)
        self.assertEqual("PASS", r["status"])
        box = pc.build_box(d["components"][0], 0.3)
        self.assertAlmostEqual(-1.05, box["xMinMm"], places=2)
        self.assertAlmostEqual(1.05, box["yMaxMm"], places=2)

    def test_margin_expands_box_and_turns_gap_into_collision(self):
        # 0.2 mm real gap, but 0.3 mm margin -> collision.
        d = doc([part("A", 0, 0, 1, 1), part("B", 1.2, 0, 2, 1)], margin=0.3)
        self.assertEqual("FAIL", pc.check(d)["status"])
        d2 = doc([part("A", 0, 0, 1, 1), part("B", 1.2, 0, 2, 1)], margin=0.05)
        self.assertEqual("PASS", pc.check(d2)["status"])

    def test_bad_schema_duplicate_missing_geometry(self):
        with self.assertRaises(pc.InputError):
            pc.check({"schema": "x", "components": [part("A", 0, 0, 1, 1)]})
        with self.assertRaises(pc.InputError):
            pc.check(doc([part("A", 0, 0, 1, 1), part("A", 5, 5, 6, 6)]))
        with self.assertRaises(pc.InputError):
            pc.check(doc([{"id": "Z", "side": "TOP", "throughBoard": False}]))

    def test_cli_pass_fail_and_bad_json(self):
        with tempfile.TemporaryDirectory() as td:
            clean = Path(td) / "clean.json"
            clean.write_text(json.dumps(doc([part("A", 0, 0, 1, 1)])), encoding="utf-8")
            out = Path(td) / "r.json"
            r = subprocess.run([sys.executable, str(SCRIPT), str(clean), "--out", str(out)],
                               capture_output=True, text=True)
            self.assertEqual(0, r.returncode, r.stderr)
            bad = Path(td) / "bad.json"
            bad.write_text("{broken", encoding="utf-8")
            r = subprocess.run([sys.executable, str(SCRIPT), str(bad), "--out",
                                str(Path(td) / "r2.json")], capture_output=True, text=True)
            self.assertEqual(2, r.returncode)


if __name__ == "__main__":
    unittest.main()
