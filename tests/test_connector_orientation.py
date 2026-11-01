"""Connector orientation gate tests; no EDA needed."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_connector_orientation.py"
SPEC = importlib.util.spec_from_file_location("connector_orientation", SCRIPT)
co = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(co)


def board():
    return {"xMinMm": 0, "yMinMm": 0, "xMaxMm": 100, "yMaxMm": 80}


def doc(connectors, setback=5.0):
    return {"schema": "connector-orientation/v1", "board": board(),
            "edgeSetbackMm": setback, "connectors": connectors}


def header(cid, x, y, vertical=True, normal=None, kind="header"):
    if vertical:
        pads = [{"xMm": x, "yMm": y, "diameterMm": 1.2},
                {"xMm": x, "yMm": y + 4, "diameterMm": 1.2}]
    else:
        pads = [{"xMm": x, "yMm": y, "diameterMm": 1.2},
                {"xMm": x + 4, "yMm": y, "diameterMm": 1.2}]
    out = {"id": cid, "kind": kind, "pads": pads}
    if normal is not None:
        out["matingNormal"] = normal
    return out


def codes(report):
    return {(f["objectId"], f["code"]) for f in report["findings"]}


class ConnectorTests(unittest.TestCase):
    def test_good_left_header_passes(self):
        d = doc([header("J1", 2, 30, vertical=True, normal={"x": -1, "y": 0})])
        r = co.check(d)
        self.assertEqual("PASS", r["status"])
        self.assertEqual([], r["findings"])

    def test_buried_header_off_edge(self):
        d = doc([header("J2", 48, 38, vertical=False, normal={"x": 1, "y": 0})])
        c = codes(co.check(d))
        # Off the edge band: only the band failure; orientation checks are skipped.
        self.assertIn(("J2", "NOT_ON_EDGE_BAND"), c)
        self.assertNotIn(("J2", "PIN_ROW_NOT_PARALLEL"), c)

    def test_pinrow_wrong_while_on_band(self):
        # On the left edge but pads run horizontally instead of along Y.
        d = doc([header("J5", 2, 30, vertical=False, normal={"x": -1, "y": 0})])
        self.assertIn(("J5", "PIN_ROW_NOT_PARALLEL"), codes(co.check(d)))

    def test_mating_face_inward(self):
        # Sits on the left edge but the mating face points inward (+x).
        d = doc([header("J3", 2, 30, vertical=True, normal={"x": 1, "y": 0})])
        self.assertIn(("J3", "MATING_FACE_INWARD"), codes(co.check(d)))

    def test_good_top_header_horizontal_pads(self):
        d = doc([header("J4", 40, 78, vertical=False, normal={"x": 0, "y": 1})])
        self.assertEqual("PASS", co.check(d)["status"])

    def test_usb_straddling_edge_passes_no_pinrow_rule(self):
        usb = {"id": "USB1", "kind": "usb",
               "pads": [{"xMm": -2, "yMm": 40, "widthMm": 1.5, "heightMm": 1.0},
                        {"xMm": 2, "yMm": 40, "widthMm": 1.5, "heightMm": 1.0}],
               "matingNormal": {"x": -1, "y": 0}}
        self.assertEqual("PASS", co.check(doc([usb]))["status"])

    def test_usb_not_straddling_edge(self):
        usb = {"id": "USB2", "kind": "usb",
               "pads": [{"xMm": 10, "yMm": 40, "widthMm": 1.5, "heightMm": 1.0},
                        {"xMm": 14, "yMm": 40, "widthMm": 1.5, "heightMm": 1.0}],
               "matingNormal": {"x": -1, "y": 0}}
        # Fully inside, nearest edge is left but it does not cross the edge ->
        # both NOT_ON_EDGE_BAND and USB_NOT_STRADDLING_EDGE.
        c = codes(co.check(doc([usb])))
        self.assertIn(("USB2", "USB_NOT_STRADDLING_EDGE"), c)

    def test_bad_schema_duplicate_and_kind(self):
        with self.assertRaises(co.InputError):
            co.check({"schema": "x", "board": board(), "connectors":
                      [header("J", 2, 30, normal={"x": -1, "y": 0})]})
        with self.assertRaises(co.InputError):
            bad = doc([header("J", 2, 30, normal={"x": -1, "y": 0})])
            bad["connectors"][0]["kind"] = "magic"
            co.check(bad)
        with self.assertRaises(co.InputError):
            co.check(doc([header("J", 2, 30, normal={"x": -1, "y": 0}),
                          header("J", 60, 30, normal={"x": 1, "y": 0})]))

    def test_cli_preset_and_bad_file(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "o.json"
            r = subprocess.run([sys.executable, str(SCRIPT), "--out", str(out)],
                               capture_output=True, text=True)
            self.assertEqual(1, r.returncode)  # preset intentionally has a buried header
            bad = Path(td) / "b.json"
            bad.write_text("{x", encoding="utf-8")
            r = subprocess.run([sys.executable, str(SCRIPT), str(bad), "--out",
                                str(Path(td) / "o2.json")], capture_output=True, text=True)
            self.assertEqual(2, r.returncode)


if __name__ == "__main__":
    unittest.main()
