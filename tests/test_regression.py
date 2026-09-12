"""Exercise the runner against temporary tiny skills, never this full test suite."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "run_regression.py"
PASSING = "import unittest\nclass Checks(unittest.TestCase):\n    def test_ok(self): self.assertEqual(2 + 2, 4)\n"


class RegressionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "portable skill"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "tests").mkdir()
        self.runner = self.root / "scripts" / "run_regression.py"
        shutil.copyfile(RUNNER, self.runner)
        self.output = self.base / "reports" / "result.json"
        self.write_test("test_sample.py", PASSING)

    def write_test(self, name, content):
        path = self.root / "tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def run_cli(self, *extra, output=None):
        output = output or self.output
        result = subprocess.run([sys.executable, "-X", "utf8", str(self.runner),
                                 "--out", str(output), *extra], cwd=self.base,
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        data = json.loads(output.read_text(encoding="utf-8")) if output == self.output and output.exists() else None
        return result, data

    def test_portable_pass_hashes_and_repeat(self):
        result, report = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((report["status"], report["testsRun"], report["passed"]), ("PASS", 1, 1))
        self.assertTrue(report["inputsUnchanged"])
        self.assertEqual([row["path"] for row in report["inputs"]],
                         ["scripts/run_regression.py", "tests/test_sample.py"])
        self.assertTrue(all(len(row["sha256"]) == 64 for row in report["inputs"]))
        second, newer = self.run_cli()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(report["inputs"], newer["inputs"])

    def test_recursive_discovery_equal_basenames(self):
        self.write_test("nested/test_sample.py", PASSING)
        result, report = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["testsRun"], 2)
        self.assertEqual(len(report["testFiles"]), 2)

    def test_fixture_changes_invalidate_report(self):
        assets = self.root / "assets"
        assets.mkdir()
        (assets / "fixture.json").write_text('{"version": 1}')
        self.write_test("test_sample.py", "import unittest\nfrom pathlib import Path\n"
                        "class Checks(unittest.TestCase):\n"
                        "    def test_mutates_fixture(self):\n"
                        "        p = Path(__file__).parents[1] / 'assets' / 'fixture.json'\n"
                        "        p.write_text('{\"version\": 2}')\n")
        result, report = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertFalse(report["inputsUnchanged"])
        self.assertIn("assets/fixture.json", [row["path"] for row in report["inputs"]])

    def test_skips_are_partial_and_strict_is_nonzero(self):
        self.write_test("test_skip.py", "import unittest\nclass Checks(unittest.TestCase):\n"
                        "    @unittest.skip('optional geometry unavailable')\n    def test_skip(self): pass\n")
        result, report = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((report["status"], report["passed"], report["skips"]), ("PARTIAL", 1, 1))
        self.assertEqual(report["skipDetails"][0]["reason"], "optional geometry unavailable")
        strict, stricter = self.run_cli("--strict-skips")
        self.assertEqual(strict.returncode, 1)
        self.assertEqual(stricter["status"], "PARTIAL")

    def test_failures_and_errors_not_counted_as_passed(self):
        self.write_test("test_bad.py", "import unittest\nclass Checks(unittest.TestCase):\n"
                        "    def test_failure(self): self.fail('wrong net')\n"
                        "    def test_error(self): raise RuntimeError('broken parser')\n")
        result, report = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual((report["testsRun"], report["passed"], report["failures"], report["errors"]), (3, 1, 1, 1))
        self.assertIn("wrong net", report["failureDetails"][0]["traceback"])
        self.assertIn("broken parser", report["errorDetails"][0]["traceback"])

    def test_assertion_failure_exit_one(self):
        self.write_test("test_sample.py", "import unittest\nclass Checks(unittest.TestCase):\n"
                        "    def test_bad(self): self.fail('broken')\n")
        result, report = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["status"], "FAIL")

    def test_zero_tests_and_broken_import_are_errors(self):
        self.write_test("test_sample.py", "# empty test module\n")
        result, report = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["testsRun"], 0)
        self.assertTrue(report["discoveryErrors"])
        self.write_test("test_sample.py", "raise RuntimeError('import failed')\n")
        result, report = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("import failed", report["discoveryErrors"][0]["reason"])

    def test_import_skip_and_expected_failure_remain_visible(self):
        self.write_test("test_skip.py", "import unittest\nraise unittest.SkipTest('missing optional tool')\n")
        self.write_test("test_expected.py", "import unittest\nclass Checks(unittest.TestCase):\n"
                        "    @unittest.expectedFailure\n    def test_known(self): self.fail('known defect')\n")
        result, report = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((report["status"], report["expectedFailures"], report["skips"]), ("PARTIAL", 1, 1))
        self.assertEqual(report["skipDetails"][0]["phase"], "import")

    def test_input_changes_invalidate_report(self):
        self.write_test("test_sample.py", "import unittest\nfrom pathlib import Path\n"
                        "class Checks(unittest.TestCase):\n"
                        "    def test_mutates_input(self):\n"
                        "        p = Path(__file__)\n        p.write_text(p.read_text() + '# changed\\n')\n")
        result, report = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["passed"], 1)
        self.assertFalse(report["inputsUnchanged"])
        self.assertTrue(any("changed" in item["reason"] for item in report["runnerErrors"]))

    def test_source_and_unrelated_output_protected_before_tests(self):
        source = self.root / "tests" / "test_sample.py"
        before = source.read_bytes()
        result, _ = self.run_cli(output=source)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(source.read_bytes(), before)
        self.output.parent.mkdir()
        self.output.write_text("valuable unrelated text", encoding="utf-8")
        result = subprocess.run([sys.executable, str(self.runner), "--out", str(self.output)],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.output.read_text(), "valuable unrelated text")

    def test_source_hardlink_protected(self):
        source = self.root / "tests" / "test_sample.py"
        alias = self.base / "hardlink.json"
        try:
            os.link(source, alias)
        except OSError as exc:
            self.skipTest(f"Hardlinks unavailable: {exc}")
        before = source.read_bytes()
        result, _ = self.run_cli(output=alias)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(source.read_bytes(), before)

    def test_output_symlink_or_parent_alias_protected(self):
        alias = self.base / "linked"
        try:
            alias.symlink_to(self.root / "tests", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Symlink creation unavailable: {exc}")
        source = self.root / "tests" / "test_sample.py"
        before = source.read_bytes()
        result, _ = self.run_cli(output=alias / source.name)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
