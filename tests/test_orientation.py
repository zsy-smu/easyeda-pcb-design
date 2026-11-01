"""Orientation sweep tests: all four rotations, cost and crossing; no EDA needed."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "optimize_orientation.py"
SPEC = importlib.util.spec_from_file_location("optimize_orientation", SCRIPT)
ori = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ori)


def part(cid, cx, cy, pitch, t1, t2, current=0):
    return {"id": cid, "center": {"xMm": cx, "yMm": cy}, "pitchMm": pitch,
            "currentRotationDeg": current,
            "pad1": {"target": {"xMm": t1[0], "yMm": t1[1]}},
            "pad2": {"target": {"xMm": t2[0], "yMm": t2[1]}}}


def doc(components):
    return {"schema": "orientation-targets/v1", "components": components}


class OrientationTests(unittest.TestCase):
    def test_horizontal_targets_prefer_0_over_180(self):
        r = ori.build_spec(doc([part("R13", 5, 5, 2, (0, 5), (10, 5), current=180)]))
        c = r["components"][0]
        self.assertEqual(0, c["recommendedRotationDeg"])
        self.assertEqual(12.0, c["currentCostMm"])
        self.assertEqual(8.0, c["bestCostMm"])
        self.assertEqual(4.0, c["costDeltaMm"])

    def test_all_four_rotations_evaluated(self):
        r = ori.build_spec(doc([part("R1", 5, 5, 2, (0, 5), (10, 5))]))
        rots = {e["rotationDeg"] for e in r["components"][0]["evaluations"]}
        self.assertEqual({0, 90, 180, 270}, rots)

    def test_vertical_targets_prefer_90(self):
        r = ori.build_spec(doc([part("D2", 20, 8, 3, (20, 0), (20, 16), current=0)]))
        self.assertEqual(90, r["components"][0]["recommendedRotationDeg"])

    def test_pad_keeps_target_under_rotation_no_pin_swap(self):
        # Rotating never swaps which target a pad binds: at 180 pad1 is on the right
        # but still scored against target1 (the left target) -> cost rises, not re-bound.
        r = ori.build_spec(doc([part("R9", 5, 5, 2, (0, 5), (10, 5))]))
        e180 = next(e for e in r["components"][0]["evaluations"] if e["rotationDeg"] == 180)
        # pad1 at x=6 scored to target x=0 -> 6; pad2 at x=4 to target x=10 -> 6
        self.assertEqual(12.0, e180["costMm"])

    def test_crossing_penalised(self):
        # At 0 deg the left pad (4,5) reaches to the right target (10,8) and the right
        # pad (6,5) reaches to the left target (0,8): the two ratlines cross.
        r = ori.build_spec(doc([part("R7", 5, 5, 2, (10, 8), (0, 8))]))
        c = r["components"][0]
        crossed = [e for e in c["evaluations"] if e["crossing"]]
        self.assertTrue(crossed, "expected at least one rotation to cross")
        # The recommended rotation must not be a crossing one (180 here).
        rec = next(e for e in c["evaluations"] if e["rotationDeg"] == c["recommendedRotationDeg"])
        self.assertFalse(rec["crossing"])
        self.assertEqual(180, c["recommendedRotationDeg"])

    def test_bad_schema_rotation_pitch_and_duplicate(self):
        with self.assertRaises(ori.InputError):
            ori.build_spec({"schema": "x", "components": [part("R", 5, 5, 2, (0, 5), (10, 5))]})
        bad = doc([part("R", 5, 5, 2, (0, 5), (10, 5))])
        bad["components"][0]["currentRotationDeg"] = 45
        with self.assertRaises(ori.InputError):
            ori.build_spec(bad)
        bad2 = doc([part("R", 5, 5, 0, (0, 5), (10, 5))])
        with self.assertRaises(ori.InputError):
            ori.build_spec(bad2)
        dup = doc([part("R", 5, 5, 2, (0, 5), (10, 5)),
                   part("R", 20, 5, 2, (0, 5), (10, 5))])
        with self.assertRaises(ori.InputError):
            ori.build_spec(dup)

    def test_cli_preset_and_bad_file(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "o.json"
            r = subprocess.run([sys.executable, str(SCRIPT), "--out", str(out)],
                               capture_output=True, text=True)
            self.assertEqual(0, r.returncode, r.stderr)
            bad = Path(td) / "b.json"
            bad.write_text("{x", encoding="utf-8")
            r = subprocess.run([sys.executable, str(SCRIPT), str(bad), "--out",
                                str(Path(td) / "o2.json")], capture_output=True, text=True)
            self.assertEqual(2, r.returncode)


if __name__ == "__main__":
    unittest.main()
