"""Check stale/insufficient evidence with actual producers and independent mutations."""
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
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    import check_evidence as evidence
finally:
    sys.path.pop(0)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.expected = self.base / "expected.json"
        self.actual = self.base / "actual.json"
        self.report = self.base / "netlist-report.json"
        for target, source in ((self.expected, "netlist-expected.json"), (self.actual, "netlist-actual.json")):
            target.write_bytes((ROOT / "assets" / "examples" / source).read_bytes())
        self.produce_netlist()
        self.plan = self.base / "evidence-plan.json"
        self.out = self.base / "evidence-report.json"
        self.data = {"schema": "evidence-plan/v1", "checks": [{
            "id": "nets", "report": self.report.name, "reportSha256": sha(self.report),
            "expectedSchema": evidence.NETLIST, "requiredScope": ["logical-all-listed", "explicit-nc"],
            "inputs": {"expected": self.expected.name, "actual": self.actual.name},
            "selection": {"expectedIndex": None, "actualIndex": None}, "context": None}]}

    def command(self, script, *args):
        return subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / script), *map(str, args)],
                              capture_output=True, text=True, encoding="utf-8", cwd=self.base, timeout=30)

    def produce_netlist(self, scope=None):
        args = [self.expected, self.actual, "--out", self.report]
        if scope:
            args += ["--scope", scope]
        result = self.command("compare_netlists.py", *args)
        self.assertEqual(result.returncode, 0, result.stderr)

    def verify(self):
        self.plan.write_text(json.dumps(self.data), encoding="utf-8")
        result = self.command("check_evidence.py", self.plan, "--out", self.out)
        return result.returncode, json.loads(result.stdout)

    def codes(self, result):
        return {f["code"] for f in result["findings"]}

    def repin(self):
        self.data["checks"][0]["reportSha256"] = sha(self.report)

    def edit_report(self, mutate):
        report = json.loads(self.report.read_text(encoding="utf-8"))
        mutate(report)
        self.report.write_text(json.dumps(report), encoding="utf-8")
        self.repin()

    def test_real_netlist_report_and_same_bytes_relocation(self):
        code, report = self.verify()
        self.assertEqual(code, 0, report)
        self.assertFalse(report["electricalCertification"])
        self.assertFalse(report["checks"][0]["contextVerified"])
        renamed = self.base / "moved-actual.json"
        renamed.write_bytes(self.actual.read_bytes())
        self.data["checks"][0]["inputs"]["actual"] = renamed.name
        self.assertEqual(self.verify()[0], 0)

    def test_old_pass_cannot_be_reused_after_input_change(self):
        self.actual.write_bytes(self.actual.read_bytes() + b"\n")
        code, report = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("STALE_INPUT", self.codes(report))

    def test_report_change_is_detected_before_trusting_new_claim(self):
        self.report.write_bytes(self.report.read_bytes() + b"\n")
        code, report = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("REPORT_CHANGED", self.codes(report))

    def test_connected_scope_does_not_cover_all_pins(self):
        self.produce_netlist("connected")
        self.repin()
        code, report = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("INSUFFICIENT_SCOPE", self.codes(report))

    def test_wrong_document_selection_fails(self):
        self.data["checks"][0]["selection"]["actualIndex"] = 3
        code, report = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("WRONG_SELECTION", self.codes(report))

    def test_missing_context_cannot_be_attested_by_plan_or_appended_field(self):
        self.data["checks"][0]["context"] = {"documentId": "PCB6"}
        self.edit_report(lambda r: r.update(documentId="PCB6"))
        code, report = self.verify()
        self.assertEqual(code, 2)
        self.assertIn("UNBOUND_CONTEXT", self.codes(report))

    def test_missing_report_or_current_input_fails(self):
        for field in ("report", "actual"):
            with self.subTest(field=field):
                original = copy.deepcopy(self.data)
                if field == "report":
                    self.data["checks"][0]["report"] = "missing.json"
                else:
                    self.data["checks"][0]["inputs"]["actual"] = "missing.json"
                code, report = self.verify()
                self.assertEqual(code, 1)
                self.assertIn("MISSING_REPORT" if field == "report" else "MISSING_INPUT", self.codes(report))
                self.data = original

    def test_unsupported_reports_scopes_and_omitted_bindings_are_errors(self):
        changes = [lambda c: c.update(expectedSchema="eda-delivery-verification/v1"),
                   lambda c: c.update(requiredScope=["industrial-qualified"]),
                   lambda c: c["inputs"].pop("actual")]
        for change in changes:
            with self.subTest(change=change):
                original = copy.deepcopy(self.data)
                change(self.data["checks"][0])
                self.assertEqual(self.verify()[0], 2)
                self.data = original

    def test_source_failure_and_contradictory_pass_are_distinct(self):
        original = self.report.read_bytes()
        self.edit_report(lambda r: r.update(status="FAIL", passed=False, wrongNet=[{"pin": "U1.1"}]))
        code, report = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("SOURCE_CHECK_FAILED", self.codes(report))
        self.report.write_bytes(original)
        self.edit_report(lambda r: r.update(wrongNet=[{"pin": "U1.1"}]))
        self.assertEqual(self.verify()[0], 2)

    def test_layout_producer_identity_and_staleness(self):
        req, actual = self.base / "layout-req.json", self.base / "layout-actual.json"
        for target, name in ((req, "layout-requirements.json"), (actual, "layout-actual.json")):
            target.write_bytes((ROOT / "assets/examples" / name).read_bytes())
        report = self.base / "layout-report.json"
        result = self.command("check_layout.py", req, actual, "--out", report)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.data["checks"] = [{"id": "layout", "report": report.name, "reportSha256": sha(report),
            "expectedSchema": evidence.LAYOUT, "requiredScope": ["declared-rectangular-layout-contract"],
            "inputs": {"requirements": req.name, "actual": actual.name},
            "context": {"documentId": "example-pcb-01"}}]
        code, result = self.verify()
        self.assertEqual(code, 0, result)
        self.assertTrue(result["checks"][0]["contextVerified"])
        self.data["checks"][0]["context"]["documentId"] = "another-pcb"
        code, result = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("WRONG_CONTEXT", self.codes(result))
        self.data["checks"][0]["context"] = {"projectId": "not-bound"}
        self.assertEqual(self.verify()[0], 2)

    def make_manufacturing_check(self, known_plating=True):
        spec = importlib.util.spec_from_file_location("evidence_manufacturing_fixture", ROOT / "tests/test_manufacturing.py")
        fixtures = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixtures)
        if not fixtures.HAS_GEOMETRY:
            self.skipTest("Shapely >=2,<3 unavailable; manufacturing evidence integration not run")
        expected = self.base / "manufacturing-expected.json"
        export = self.base / "export.zip"
        report = self.base / "manufacturing-report.json"
        manifest = fixtures.expected()
        expected.write_text(json.dumps(manifest), encoding="utf-8")
        files = fixtures.fixture(manifest)
        if not known_plating:
            files["drill.drl"] = files["drill.drl"].replace(";TYPE=PLATED\n", "")
        fixtures.write_zip(export, files)
        result = self.command("check_manufacturing.py", export, "--expected", expected, "--out", report)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.data["checks"] = [{"id": "export", "report": report.name, "reportSha256": sha(report),
            "expectedSchema": evidence.MANUFACTURING,
            "requiredScope": ["outline", "drill-geometry", "copper-layer-inventory"],
            "inputs": {"expected": expected.name, "export": export.name}, "context": None}]
        return export, report

    def test_manufacturing_producer_and_changed_archive(self):
        export, _ = self.make_manufacturing_check()
        self.data["checks"][0]["requiredScope"].append("known-plating")
        code, result = self.verify()
        self.assertEqual(code, 0, result)
        export.write_bytes(export.read_bytes() + b"\n")
        code, result = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("STALE_INPUT", self.codes(result))

    def test_geometry_pass_does_not_imply_known_plating(self):
        self.make_manufacturing_check(known_plating=False)
        self.assertEqual(self.verify()[0], 0)
        self.data["checks"][0]["requiredScope"].append("known-plating")
        code, result = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("INSUFFICIENT_SCOPE", self.codes(result))

    def test_manufacturing_contradictory_details_not_trusted(self):
        _, report = self.make_manufacturing_check()
        data = json.loads(report.read_text(encoding="utf-8"))
        data["drills"]["missing"] = ["hole-not-present"]
        report.write_text(json.dumps(data), encoding="utf-8")
        self.data["checks"][0]["reportSha256"] = sha(report)
        self.assertEqual(self.verify()[0], 2)

    def test_output_cannot_alias_plan_report_or_input(self):
        self.plan.write_text(json.dumps(self.data), encoding="utf-8")
        for target in (self.plan, self.report, self.actual):
            for hardlink in (False, True):
                with self.subTest(target=target.name, hardlink=hardlink):
                    output = self.base / ("alias-" + target.name) if hardlink else target
                    if hardlink:
                        os.link(target, output)
                    before = target.read_bytes()
                    result = self.command("check_evidence.py", self.plan, "--out", output)
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertEqual(target.read_bytes(), before)

    def test_malformed_duplicate_and_nonfinite_json_are_errors(self):
        for raw in ('{"schema":1,"schema":2}', '{', '{"value": NaN}', '{"value": 1e999}'):
            with self.subTest(raw=raw):
                self.plan.write_text(raw, encoding="utf-8")
                result = self.command("check_evidence.py", self.plan, "--out", self.out)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["status"], "ERROR")

    def test_empty_plan_duplicate_check_and_unknown_field_refused(self):
        for data in ({"schema": "evidence-plan/v1", "checks": []},
                     {"schema": "evidence-plan/v1", "checks": self.data["checks"] * 2},
                     {**self.data, "ignoreMissing": True}):
            self.data = copy.deepcopy(data)
            self.assertEqual(self.verify()[0], 2)


if __name__ == "__main__":
    unittest.main()
