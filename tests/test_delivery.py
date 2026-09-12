"""Temporary-directory behavioral tests; no EDA, network or project mutation."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_delivery.py"
spec = importlib.util.spec_from_file_location("check_delivery", SCRIPT)
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "delivery"
        self.root.mkdir()
        (self.root / "layers").mkdir()
        (self.root / "layers" / "top.gbr").write_bytes(b"copper\x00\xff")
        (self.root / "board.epro").write_bytes(b"project")
        self.manifest = self.base / "manifest.json"
        self.report = self.base / "report.json"

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return delivery.main([os.fspath(x) for x in args])

    def snap(self, out=None):
        out = out or self.manifest
        self.assertEqual(self.cli("snapshot", self.root, "--out", out), 0)
        return json.loads(out.read_text(encoding="utf8"))

    def verify(self, manifest=None, report=None, root=None):
        args = ["verify", manifest or self.manifest, "--out", report or self.report]
        if root is not None:
            args += ["--root", root]
        return self.cli(*args)

    def test_snapshot_sorted_and_verification_pass(self):
        m = self.snap()
        self.assertEqual([f["path"] for f in m["files"]], ["board.epro", "layers/top.gbr"])
        self.assertEqual(m["files"][1]["size"], 8)
        self.assertEqual(len(m["files"][1]["sha256"]), 64)
        self.assertEqual(self.verify(), 0)
        self.assertTrue(json.loads(self.report.read_text())["passed"])

    def test_changed_same_length_detected(self):
        self.snap()
        (self.root / "board.epro").write_bytes(b"PROJECT")
        self.assertEqual(self.verify(), 1)
        r = json.loads(self.report.read_text())
        self.assertEqual([f["path"] for f in r["changed"]], ["board.epro"])
        self.assertFalse(r["missing"] or r["extra"])

    def test_added_and_missing_files_detected(self):
        self.snap()
        (self.root / "board.epro").unlink()
        (self.root / "new.txt").write_text("new")
        self.assertEqual(self.verify(), 1)
        r = json.loads(self.report.read_text())
        self.assertEqual(r["missing"], ["board.epro"])
        self.assertEqual([f["path"] for f in r["extra"]], ["new.txt"])

    def test_exact_self_outputs_only_no_extension_ignore(self):
        manifest = self.root / "manifest.json"
        report = self.root / "verify.json"
        m = self.snap(manifest)
        self.assertEqual(m["manifestPath"], "manifest.json")
        self.assertEqual(len(m["files"]), 2)
        self.assertEqual(self.verify(manifest, report), 0)
        self.assertEqual(self.verify(manifest, report), 0)
        (self.root / "unrelated.json").write_text("{}")
        self.assertEqual(self.verify(manifest, report), 1)
        self.assertEqual([x["path"] for x in json.loads(report.read_text())["extra"]], ["unrelated.json"])

    def test_relocated_directory_with_override(self):
        self.snap()
        copy = self.base / "moved"
        shutil.copytree(self.root, copy)
        self.assertEqual(self.verify(root=copy), 0)

    def test_invalid_paths_rejected_without_output(self):
        original = self.snap()
        for name in ["../outside", "/absolute", "C:/absolute", "a/../b", "a//b", "a\\b", "./a", "a."]:
            with self.subTest(name=name):
                m = json.loads(json.dumps(original))
                m["files"][0]["path"] = name
                self.manifest.write_text(json.dumps(m))
                self.assertEqual(self.verify(), 2)
                self.assertFalse(self.report.exists())

    def test_duplicate_and_case_alias_entries_rejected(self):
        original = self.snap()
        for name in ["board.epro", "BOARD.EPRO"]:
            m = json.loads(json.dumps(original))
            m["files"].append({**m["files"][0], "path": name})
            self.manifest.write_text(json.dumps(m))
            self.assertEqual(self.verify(), 2)

    def test_invalid_schema_unknown_fields_size_and_hash(self):
        original = self.snap()
        variants = []
        for key, value in [("schema", "other"), ("ignore", ["*.gbr"]), ("root", "C:/bad\x00path")]:
            m = json.loads(json.dumps(original)); m[key] = value; variants.append(m)
        for key, value in [("size", True), ("sha256", "bad")]:
            m = json.loads(json.dumps(original)); m["files"][0][key] = value; variants.append(m)
        for m in variants:
            self.manifest.write_text(json.dumps(m))
            self.assertEqual(self.verify(), 2)

    def test_duplicate_json_keys_rejected(self):
        self.snap()
        self.manifest.write_text('{"schema":"x","schema":"y"}')
        self.assertEqual(self.verify(), 2)

    def test_file_directory_hierarchy_conflicts_rejected(self):
        original = self.snap()
        m = json.loads(json.dumps(original))
        m["files"].append({**m["files"][0], "path": "board.epro/child"})
        self.manifest.write_text(json.dumps(m))
        self.assertEqual(self.verify(), 2)
        m = json.loads(json.dumps(original)); m["manifestPath"] = "layers"
        self.manifest.write_text(json.dumps(m))
        self.assertEqual(self.verify(), 2)

    def test_source_report_clobber_and_tracked_report_rejected(self):
        self.snap()
        before = self.manifest.read_bytes()
        self.assertEqual(self.verify(report=self.manifest), 2)
        self.assertEqual(self.manifest.read_bytes(), before)
        target = self.root / "board.epro"
        original = target.read_bytes()
        self.assertEqual(self.verify(report=target), 2)
        self.assertEqual(target.read_bytes(), original)

    def test_output_hardlink_alias_is_rejected(self):
        self.snap()
        try:
            os.link(self.manifest, self.report)
        except OSError as exc:
            self.skipTest(str(exc))
        before = self.manifest.read_bytes()
        self.assertEqual(self.verify(), 2)
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_snapshot_does_not_clobber_existing_artifact(self):
        target = self.root / "board.epro"
        before = target.read_bytes()
        self.assertEqual(self.cli("snapshot", self.root, "--out", target), 2)
        self.assertEqual(target.read_bytes(), before)

    def test_symlink_escape_is_rejected(self):
        self.snap()
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("must not inventory")
        link = self.root / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        self.assertEqual(self.verify(), 2)
        self.assertFalse(self.report.exists())

    def test_reparse_attribute_rejected_even_without_symlink_mode(self):
        class Junction:
            st_mode = stat.S_IFDIR | 0o755
            st_file_attributes = 0x400
        with self.assertRaises(delivery.InvalidInput):
            delivery.no_reparse(Junction(), "junction")

    def test_changed_during_hash_is_invalid_not_verified(self):
        self.snap()
        real = delivery.hash_file
        def broken(path):
            raise delivery.InvalidInput("File changed while reading")
        with patch.object(delivery, "hash_file", broken):
            self.assertEqual(self.verify(), 2)
        self.assertIs(delivery.hash_file, real)

    def test_snapshot_can_replace_its_existing_manifest(self):
        target = self.root / "manifest.json"
        first = self.snap(target)
        second = self.snap(target)
        self.assertEqual(first, second)

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive output path")
    def test_existing_self_output_with_different_case_is_excluded(self):
        self.snap(self.root / "manifest.json")
        m = self.snap(self.root / "MANIFEST.JSON")
        self.assertEqual(len(m["files"]), 2)
        self.assertEqual(self.verify(self.root / "manifest.json"), 0)

    def test_real_cli_process_exit_codes(self):
        def run(*args):
            return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                                  capture_output=True, text=True, check=False).returncode
        self.assertEqual(run("snapshot", self.root, "--out", self.manifest), 0)
        self.assertEqual(run("verify", self.manifest, "--out", self.report), 0)
        (self.root / "extra.bin").write_bytes(b"extra")
        self.assertEqual(run("verify", self.manifest, "--out", self.report), 1)
        self.assertEqual(run("verify", self.manifest, "--out", self.manifest), 2)


if __name__ == "__main__":
    unittest.main()
