#!/usr/bin/env python3
"""Verify report freshness and declared coverage; no EDA calls or recertification."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

# Shared standard-library filesystem guards; no delivery inventory is performed.
from check_delivery import (InvalidInput, alias, checked_path, fingerprint, hash_file,
                            invalid_constant, output_guard, require, unique_object,
                            write_json)

PLAN_SCHEMA = "evidence-plan/v1"
REPORT_SCHEMA = "eda-evidence-check/v1"
NETLIST = "eda-netlist-comparison/v1"
MANUFACTURING = "easyeda-manufacturing-check/v1"
LAYOUT = "eda-layout-report/v1"
SUPPORTED = {NETLIST, MANUFACTURING, LAYOUT}
SCOPES = {
    NETLIST: {"logical-connected", "logical-all-listed", "explicit-nc",
              "physical-assignment", "physical-multiplicity"},
    MANUFACTURING: {"outline", "drill-geometry", "copper-layer-inventory", "known-plating"},
    LAYOUT: {"declared-rectangular-layout-contract"},
}
LIMITATIONS = [
    "Checks report bytes, original input hashes, declared coverage and available identity only.",
    "Does not rerun source checks or certify electrical, copper, manufacturing or native-save correctness.",
    "The independently reviewed plan and pinned report hashes are trust inputs, not signatures.",
    "A null context requests file/selection binding only; project/document identity is not verified.",
    "Files must remain stable during this check; this is not an atomic filesystem snapshot.",
]


def text(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"{label} must be a nonempty string")
    return value


def digest(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            f"{label} must be a lowercase SHA256")
    return value


def snapshot_json(path):
    path = checked_path(path, must_exist=True)
    before = path.stat()
    require(path.is_file() and before.st_size <= 32 * 1024 * 1024, "JSON file must be regular and <=32 MiB")
    raw = path.read_bytes()
    require(fingerprint(before) == fingerprint(path.stat()), f"JSON changed while reading: {path}")
    def finite_float(value):
        number = float(value)
        require(math.isfinite(number), "Non-finite JSON number")
        return number
    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object,
                       parse_constant=invalid_constant, parse_float=finite_float)
    return value, {"file": str(path), "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def plan_path(value, base):
    value = text(value, "path")
    require(not any(ord(c) < 32 for c in value), "Control characters in path")
    require(".." not in value.replace("\\", "/").split("/"), "Parent traversal is unsupported")
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return checked_path(path)


def validate_plan(data, base):
    require(isinstance(data, dict) and set(data) == {"schema", "checks"}, "Invalid plan fields")
    require(data["schema"] == PLAN_SCHEMA, "Unsupported plan schema")
    require(isinstance(data["checks"], list) and 0 < len(data["checks"]) <= 1000,
            "checks must contain 1..1000 required checks")
    seen = set()
    protected = []
    for item in data["checks"]:
        required = {"id", "report", "reportSha256", "expectedSchema", "requiredScope", "inputs", "context"}
        require(isinstance(item, dict) and required <= set(item)
                and not (set(item) - required - {"selection"}), "Invalid check fields")
        name = text(item["id"], "id")
        require(name not in seen, f"Duplicate check id: {name}")
        seen.add(name)
        digest(item["reportSha256"], "reportSha256")
        text(item["expectedSchema"], "expectedSchema")
        scope = item["requiredScope"]
        require(isinstance(scope, list) and scope and all(isinstance(s, str) and s for s in scope)
                and len(set(scope)) == len(scope), "requiredScope must be a nonempty unique string array")
        context = item["context"]
        require(context is None or (isinstance(context, dict) and bool(context)
                and set(context) <= {"projectId", "documentId"}), "Invalid context")
        if context is not None:
            for key, value in context.items():
                text(value, key)
        inputs = item["inputs"]
        require(isinstance(inputs, dict) and bool(inputs), "inputs must map all input roles to paths")
        item["report"] = str(plan_path(item["report"], base))
        protected.append(Path(item["report"]))
        for role, value in inputs.items():
            text(role, "input role")
            inputs[role] = str(plan_path(value, base))
            protected.append(Path(inputs[role]))
        if item["expectedSchema"] == NETLIST:
            sel = item.get("selection")
            require(isinstance(sel, dict) and set(sel) == {"expectedIndex", "actualIndex"},
                    "Netlist check requires explicit selection indices (null for a single document)")
            require(all(v is None or type(v) is int and v >= 0 for v in sel.values()), "Invalid selection index")
        else:
            require("selection" not in item, "selection is only supported for netlist reports")
    return data, protected


def require_bool(value, label):
    require(type(value) is bool, f"{label} must be Boolean")
    return value


def file_binding(value, label):
    require(isinstance(value, dict), f"Missing original input binding: {label}")
    text(value.get("file"), f"{label}.file")
    digest(value.get("sha256"), f"{label}.sha256")
    return {"file": value["file"], "sha256": value["sha256"]}


def adapt_netlist(report):
    require(report.get("scope") in {"all", "connected"}, "Unknown netlist scope")
    for key in ("missing", "extra", "wrongNet", "ncDifferences", "physicalPadAssignmentFindings",
                "physicalPadInventoryDifferences"):
        require(isinstance(report.get(key), list), f"Missing netlist result: {key}")
        require(not report["passed"] or not report[key], "PASS contradicts netlist findings")
    selection = report.get("selection")
    require(isinstance(selection, dict) and set(selection) == {"expectedIndex", "actualIndex"}, "Missing selection")
    require(all(v is None or type(v) is int and v >= 0 for v in selection.values()), "Invalid report selection")
    bindings = report.get("inputs")
    require(isinstance(bindings, dict) and {"expected", "actual"} <= set(bindings)
            and not (set(bindings) - {"expected", "actual", "aliases"}), "Missing/unknown netlist input bindings")
    aliases = report.get("actualNetAliases")
    require(isinstance(aliases, dict), "Missing actualNetAliases")
    require(not aliases or "aliases" in bindings, "Aliases used without original file binding")
    coverage = {"logical-connected"}
    if report["scope"] == "all":
        coverage.update({"logical-all-listed", "explicit-nc"})
    actual = report.get("actual", {}).get("counts", {}).get("physicalPads")
    require(actual is None or type(actual) is int and actual > 0, "Invalid physical pad count")
    if actual is not None:
        coverage.add("physical-assignment")
    if require_bool(report.get("physicalMultiplicityCompared"), "physicalMultiplicityCompared"):
        require(actual is not None, "Physical multiplicity claimed without actual pads")
        coverage.add("physical-multiplicity")
    # v1 reports do not bind project/document identity, even if arbitrary fields are appended.
    return bindings, coverage, {}, selection


def adapt_manufacturing(report):
    require(report.get("checked") is True, "Manufacturing check was not completed")
    coverage = set()
    for key, scope in (("outline", "outline"), ("drills", "drill-geometry"),
                       ("copperLayers", "copper-layer-inventory")):
        result = report.get(key)
        require(isinstance(result, dict), f"Missing manufacturing result: {key}")
        passed = require_bool(result.get("passed"), f"{key}.passed")
        require(not report["passed"] or passed, "PASS contradicts a manufacturing result")
        coverage.add(scope)
    for where, value in (("outline.issues", report["outline"].get("issues")),
                         ("drills.missing", report["drills"].get("missing")),
                         ("drills.unmatchedExportedFeatures", report["drills"].get("unmatchedExportedFeatures")),
                         ("drills.ambiguous", report["drills"].get("ambiguous"))):
        require(isinstance(value, list), f"Missing manufacturing details: {where}")
        require(not report["passed"] or not value, "PASS contradicts manufacturing findings")
    plating = report["drills"].get("platingClassification")
    require(isinstance(plating, dict), "Missing plating classification")
    if require_bool(plating.get("verified"), "plating.verified"):
        require(plating.get("status") == "VERIFIED_AGAINST_DECLARED_EXPECTATION"
                and plating.get("unknown") == [] and plating.get("mismatches") == [], "Contradictory plating verification")
        coverage.add("known-plating")
    bindings = {"expected": report.get("expectedManifest"),
                "export": {"file": report.get("exportPath"), "sha256": report.get("exportSha256")}}
    # suppliedEvidence is explicitly unverified metadata, not an identity attestation.
    return bindings, coverage, {}, None


def adapt_layout(report):
    require(report.get("checked") is True, "Layout check was not completed")
    require(report.get("scope") == "declared-rectangular-layout-contract", "Unknown layout scope")
    require(isinstance(report.get("findings"), list), "Missing layout findings")
    require(not report["passed"] or not report["findings"], "PASS contradicts layout findings")
    ids = report.get("documentId")
    require(isinstance(ids, dict) and set(ids) == {"requirements", "actual"}, "Missing layout document identity")
    for key, value in ids.items():
        text(value, f"documentId.{key}")
    require(not report["passed"] or ids["requirements"] == ids["actual"], "PASS contradicts document identity")
    count = report.get("counts", {}).get("checksEvaluated")
    require(type(count) is int and count > 0, "No evaluated layout checks")
    bindings = report.get("inputs")
    require(isinstance(bindings, dict) and set(bindings) == {"requirements", "actual"}, "Missing layout input bindings")
    return bindings, {report["scope"]}, {"documentId": ids["actual"]}, None


def audit_check(item):
    result = {"id": item["id"], "expectedSchema": item["expectedSchema"], "findings": [], "inputs": {}}
    def finding(code, message, severity="FAIL", **details):
        result["findings"].append({"code": code, "severity": severity, "message": message, **details})
    try:
        schema = item["expectedSchema"]
        require(schema in SUPPORTED, "Unsupported report schema; delivery verification must be rerun with check_delivery.py verify")
        require(set(item["requiredScope"]) <= SCOPES[schema], "Unsupported requested scope for this schema")
        report_path = Path(item["report"])
        if not report_path.exists():
            finding("MISSING_REPORT", "Required report is missing", file=str(report_path))
            return result
        report, proof = snapshot_json(report_path)
        result["report"] = proof
        if proof["sha256"] != item["reportSha256"]:
            finding("REPORT_CHANGED", "Report differs from the independently pinned report", expected=item["reportSha256"], actual=proof["sha256"])
            return result
        require(isinstance(report, dict), "Report must be an object")
        require(report.get("schema") == schema, "Report schema does not match expectedSchema")
        passed = require_bool(report.get("passed"), "report.passed")
        require(report.get("status") in {"PASS", "FAIL"}, "Report did not complete a supported check")
        require((report["status"] == "PASS") == passed, "Contradictory report status/passed")
        adapter = {NETLIST: adapt_netlist, MANUFACTURING: adapt_manufacturing, LAYOUT: adapt_layout}[schema]
        bindings, coverage, context, selection = adapter(report)
        require(set(bindings) == set(item["inputs"]), "Plan must bind every original input role, with no omissions or extras")
        result["availableScope"] = sorted(coverage)
        result["requiredScope"] = item["requiredScope"]
        result["sourcePassed"] = passed
        result["contextVerified"] = False
        missing = sorted(set(item["requiredScope"]) - coverage)
        if missing:
            finding("INSUFFICIENT_SCOPE", "Report does not cover every required check", missingScope=missing)
        if selection is not None and selection != item["selection"]:
            finding("WRONG_SELECTION", "Report selected different netlist documents", expected=item["selection"], actual=selection)
        if item["context"] is not None:
            for key, expected in item["context"].items():
                if not isinstance(context.get(key), str) or not context[key]:
                    finding("UNBOUND_CONTEXT", f"Report does not attest {key}", "ERROR")
                elif context[key] != expected:
                    finding("WRONG_CONTEXT", f"Report {key} differs from the plan", expected=expected, actual=context[key])
            result["contextVerified"] = all(context.get(k) == v for k, v in item["context"].items())
        for role, binding in bindings.items():
            original = file_binding(binding, role)
            path = Path(item["inputs"][role])
            if not path.exists():
                finding("MISSING_INPUT", "Required current input is missing", role=role, file=str(path))
                continue
            current = {"file": str(path), **hash_file(checked_path(path, must_exist=True))}
            result["inputs"][role] = {"recorded": original, "current": current}
            if current["sha256"] != original["sha256"]:
                finding("STALE_INPUT", "Current input differs from the report's original input", role=role,
                        expected=original["sha256"], actual=current["sha256"])
        if not passed:
            finding("SOURCE_CHECK_FAILED", "The source report did not pass its declared check")
    except (InvalidInput, OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError) as exc:
        finding("UNSUPPORTED_OR_INVALID", str(exc), "ERROR")
    return result


def run(plan_path_value, output_value):
    plan_path_value = checked_path(plan_path_value, must_exist=True)
    output = checked_path(output_value)
    plan, proof = snapshot_json(plan_path_value)
    plan, protected = validate_plan(plan, plan_path_value.parent)
    protected.append(plan_path_value)
    require(not any(alias(output, p) for p in protected), "Output aliases a plan, report or source input")
    output_guard(output, allowed_schema=REPORT_SCHEMA)
    results = [audit_check(check) for check in plan["checks"]]
    findings = [{"checkId": r["id"], **f} for r in results for f in r["findings"]]
    # Recheck already-read files to avoid accepting an input replaced during another check.
    proofs = [proof] + [r["report"] for r in results if "report" in r]
    proofs += [v["current"] for r in results for v in r["inputs"].values()]
    for known in proofs:
        try:
            current = hash_file(checked_path(known["file"], must_exist=True))
            require(current["sha256"] == known["sha256"], "File changed during evidence verification")
        except (InvalidInput, OSError) as exc:
            findings.append({"code": "CHANGED_DURING_AUDIT", "severity": "ERROR", "message": str(exc), "file": known["file"]})
    code = 2 if any(f["severity"] == "ERROR" for f in findings) else 1 if findings else 0
    result = {"schema": REPORT_SCHEMA, "status": ("PASS", "FAIL", "ERROR")[code],
              "passed": code == 0, "checked": code != 2, "plan": proof, "checks": results,
              "findings": findings, "limitations": LIMITATIONS, "nativeMutation": False,
              "electricalCertification": False}
    require(not any(alias(output, p) for p in protected), "Output became an input alias")
    output_guard(output, allowed_schema=REPORT_SCHEMA)
    write_json(output, result)
    return result, code


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InvalidInput(message)


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument("plan", help="Independently reviewed evidence-plan/v1 JSON")
    parser.add_argument("--out", required=True, help="New independent JSON report")
    try:
        args = parser.parse_args(argv)
        result, code = run(args.plan, args.out)
    except (InvalidInput, OSError, ValueError, TypeError, RecursionError, OverflowError) as exc:
        result = {"schema": REPORT_SCHEMA, "status": "ERROR", "passed": False, "checked": False,
                  "findings": [{"code": "INVALID_INPUT_OR_OUTPUT", "severity": "ERROR", "message": str(exc)}],
                  "limitations": LIMITATIONS, "nativeMutation": False}
        code = 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
