import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compare_netlists.py"
spec = importlib.util.spec_from_file_location("compare_netlists", SCRIPT)
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)


def simple():
    return {"nets": {"VCC": [{"ref": "U1", "pin": "1"}],
                     "GND": [{"ref": "U1", "pin": "2"}]},
            "unconnected": [{"ref": "U1", "pin": "3", "no_connect": True}]}


def compare(a, b, **kw):
    return n.compare(n.normalize(a), n.normalize(b), **kw)


class NetlistTests(unittest.TestCase):
    def test_equal_and_empty_nets_not_shortened_together(self):
        doc = simple()
        doc["unconnected"].append({"ref": "U2", "pin": "7"})
        result = compare(doc, copy.deepcopy(doc))
        self.assertTrue(result["passed"])
        self.assertEqual(result["actual"]["counts"]["nets"], 2)
        self.assertEqual(result["actual"]["counts"]["unconnectedLogicalEndpoints"], 2)

    def test_same_counts_wrong_net_detected(self):
        actual = simple()
        actual["nets"]["VCC"][0]["pin"] = "2"
        actual["nets"]["GND"][0]["pin"] = "1"
        result = compare(simple(), actual)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["wrongNet"]), 2)

    def test_missing_and_extra_endpoints(self):
        actual = simple(); actual["nets"]["VCC"][0]["pin"] = "8"
        result = compare(simple(), actual)
        self.assertEqual(result["missing"], [{"ref": "U1", "pin": "1", "net": "VCC"}])
        self.assertEqual(result["extra"], [{"ref": "U1", "pin": "8", "net": "VCC"}])

    def test_duplicate_logical_pin_rejected(self):
        doc = simple(); doc["nets"]["GND"].append({"ref": "U1", "pin": "1"})
        with self.assertRaises(n.InputError): n.normalize(doc)

    def test_nc_is_not_implied_by_blank_net(self):
        actual = simple(); del actual["unconnected"][0]["no_connect"]
        result = compare(simple(), actual)
        self.assertFalse(result["passed"])
        self.assertIsNone(result["ncDifferences"][0]["actual"])
        self.assertTrue(compare(simple(), actual, scope="connected")["passed"])

    def test_connected_scope_does_not_hide_lost_active_pin(self):
        actual = simple(); actual["nets"].pop("VCC")
        actual["unconnected"].append({"ref": "U1", "pin": "1"})
        self.assertEqual(len(compare(simple(), actual, scope="connected")["missing"]), 1)

    def test_native_pininfo_and_explicit_document_selection(self):
        native = {"version": "2", "components": {"some-uid": {
            "props": {"Designator": "U1"}, "pinInfoMap": {
                "1": {"number": "1", "net": "VCC"},
                "2": {"number": "2", "net": "GND"},
                "3": {"number": "3", "net": ""}}}}}
        collection = [json.dumps(simple()), json.dumps(native)]
        with self.assertRaises(n.InputError): n.normalize(collection)
        result = n.compare(n.normalize(simple()), n.normalize(collection, 1), "connected")
        self.assertTrue(result["passed"])
        with self.assertRaises(n.InputError): n.normalize(collection, 2)

    def test_native_key_number_and_duplicate_component_rejected(self):
        comp = {"props": {"Designator": "J1"}, "pinInfoMap": {"1": {"number": "2", "net": "GND"}}}
        with self.assertRaises(n.InputError): n.normalize({"components": {"a": comp}})
        comp["pinInfoMap"]["1"]["number"] = "1"
        with self.assertRaises(n.InputError): n.normalize({"components": {"a": comp, "b": comp}})

    def test_distinct_physical_pads_same_pin_are_preserved(self):
        actual = json.loads((ROOT / "assets/examples/netlist-actual.json").read_text())
        expected = json.loads((ROOT / "assets/examples/netlist-expected.json").read_text())
        result = compare(expected, actual, require_physical=True)
        self.assertTrue(result["passed"])
        self.assertEqual(result["actual"]["counts"]["logicalEndpoints"], 6)
        self.assertEqual(result["actual"]["counts"]["physicalPads"], 7)

    def test_wrong_physical_pad_assignment_fails(self):
        actual = json.loads((ROOT / "assets/examples/netlist-actual.json").read_text())
        actual["physicalPads"][-1]["net"] = "3V3"
        expected = json.loads((ROOT / "assets/examples/netlist-expected.json").read_text())
        result = compare(expected, actual)
        self.assertFalse(result["passed"])
        self.assertEqual(result["physicalPadAssignmentFindings"][0]["id"], "j1-shell-b")

    def test_duplicate_physical_id_invalid(self):
        actual = json.loads((ROOT / "assets/examples/netlist-actual.json").read_text())
        actual["physicalPads"].append(actual["physicalPads"][0])
        with self.assertRaises(n.InputError): n.normalize(actual)

    def test_physical_inventory_missing_pin_fails(self):
        actual = json.loads((ROOT / "assets/examples/netlist-actual.json").read_text())
        actual["physicalPads"].pop(0)
        expected = json.loads((ROOT / "assets/examples/netlist-expected.json").read_text())
        self.assertFalse(compare(expected, actual)["passed"])

    def test_require_physical_cannot_pass_logical_only(self):
        with self.assertRaises(n.InputError): compare(simple(), simple(), require_physical=True)

    def test_missing_or_extra_duplicate_number_pad_fails(self):
        expected = json.loads((ROOT / "assets/examples/netlist-actual.json").read_text())
        actual = copy.deepcopy(expected)
        actual["physicalPads"].pop()
        result = compare(expected, actual, require_physical=True)
        self.assertFalse(result["passed"])
        self.assertEqual(result["physicalPadInventoryDifferences"], [
            {"ref": "J1", "pin": "10", "expectedCount": 2, "actualCount": 1}])
        actual = copy.deepcopy(expected)
        actual["physicalPads"].append({**actual["physicalPads"][-1], "id": "unexpected-third-pad"})
        self.assertFalse(compare(expected, actual)["passed"])

    def test_physical_baseline_requires_inventory_but_not_matching_export_ids(self):
        expected = json.loads((ROOT / "assets/examples/netlist-actual.json").read_text())
        actual = copy.deepcopy(expected)
        for pad in actual["physicalPads"]:
            pad["id"] = "regenerated-" + pad["id"]
        self.assertTrue(compare(expected, actual)["passed"])
        del actual["physicalPads"]
        with self.assertRaises(n.InputError): compare(expected, actual)

    def test_names_exact_unless_alias_declared(self):
        actual = simple(); actual["nets"]["gnd"] = actual["nets"].pop("GND")
        self.assertFalse(compare(simple(), actual)["passed"])
        self.assertTrue(n.compare(n.normalize(simple()), n.normalize(actual, aliases={"gnd": "GND"}))["passed"])
        with self.assertRaises(n.InputError): n.normalize(actual, aliases=[])

    def test_bad_json_and_schema_rejected(self):
        for text in ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}']:
            with self.assertRaises(n.InputError): n.parse_json(text)
        for value in [{}, {"nets": {}}, {"schema": "unknown", "nets": {}}, {"components": []}]:
            with self.assertRaises(n.InputError): n.normalize(value)

    def test_invalid_pin_and_contradictory_nc_rejected(self):
        for bad in [None, True, 1.25, "", " 1"]:
            value = simple(); value["nets"]["VCC"][0]["pin"] = bad
            with self.assertRaises(n.InputError): n.normalize(value)
        value = simple(); value["nets"]["VCC"][0]["no_connect"] = True
        with self.assertRaises(n.InputError): n.normalize(value)

    def test_cli_exit_codes_reports_and_no_source_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td); expected = p / "e.json"; actual = p / "a.json"; report = p / "report.json"
            expected.write_text(json.dumps(simple())); actual.write_bytes(expected.read_bytes())
            def run(out):
                return subprocess.run([sys.executable, str(SCRIPT), str(expected), str(actual), "--out", str(out)], capture_output=True)
            self.assertEqual(run(report).returncode, 0)
            value = simple(); value["nets"]["VCC"][0]["pin"] = "5"; actual.write_text(json.dumps(value))
            self.assertEqual(run(report).returncode, 1)
            self.assertEqual(json.loads(report.read_text())["status"], "FAIL")
            before = expected.read_bytes()
            self.assertEqual(run(expected).returncode, 2)
            self.assertEqual(expected.read_bytes(), before)
            actual.write_text('{"nets":null}')
            self.assertEqual(run(report).returncode, 2)
            self.assertEqual(json.loads(report.read_text())["status"], "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()
