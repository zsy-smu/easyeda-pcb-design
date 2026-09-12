#!/usr/bin/env python3
"""Check an explicit, bounded layout contract; no EDA access or coordinate inference.

Python 3.10+, standard library only. See references/layout-tool.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile


SCHEMA = "eda-layout-report/v1"
RECT_KEYS = {"xMinMm", "yMinMm", "xMaxMm", "yMaxMm"}
SIDES = {"TOP", "BOTTOM"}
LIMIT = 1_000_000.0
LIMITATIONS = [
    "PASS only covers the supplied confirmed layout contract and measured JSON snapshot.",
    "Source independence, confirmation, complete inventory and document identity are declarations, not authenticated evidence.",
    "Only global millimetres, Cartesian y-up, axis-aligned rectangular boards/envelopes/keepouts are supported.",
    "Envelopes and allowed board overhang must represent independently reviewed physical requirements; their truth is not measured here.",
    "Connector direction is a measured global mating direction, not an inferred footprint rotation.",
    "Point distances are straight-line planar distances, not copper length, return paths or decoupling performance.",
    "No component-to-component collision, copper, netlist, electrical, 3D, native DRC, save or manufacturing qualification is performed.",
]


class InputError(ValueError):
    pass


def object_fields(value, required, optional, where):
    if not isinstance(value, dict):
        raise InputError(f"{where}: expected an object")
    missing = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    if missing:
        raise InputError(f"{where}: missing fields {', '.join(sorted(missing))}")
    if unknown:
        raise InputError(f"{where}: unknown fields {', '.join(sorted(unknown))}")
    return value


def label(value, where):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InputError(f"{where}: expected a nonempty string without edge whitespace")
    return value


def number(value, where, minimum=-LIMIT, maximum=LIMIT):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{where}: expected a finite number, not a boolean")
    if not minimum <= value <= maximum or not math.isfinite(value):
        raise InputError(f"{where}: expected a finite number in [{minimum}, {maximum}]")
    return value


def boolean(value, where):
    if not isinstance(value, bool):
        raise InputError(f"{where}: expected true or false")


def side(value, where):
    if not isinstance(value, str) or value not in SIDES:
        raise InputError(f"{where}: expected TOP or BOTTOM")


def angle(value, where):
    number(value, where, 0, 360)
    if value == 360:
        raise InputError(f"{where}: use [0, 360) degrees")


def point(value, where, tolerance=False):
    required = {"xMm", "yMm"} | ({"toleranceMm"} if tolerance else set())
    object_fields(value, required, set(), where)
    for key in ("xMm", "yMm"):
        number(value[key], f"{where}.{key}")
    if tolerance:
        number(value["toleranceMm"], f"{where}.toleranceMm", 0)


def rect(value, where, tolerance=False):
    object_fields(value, RECT_KEYS | ({"toleranceMm"} if tolerance else set()), set(), where)
    for key in RECT_KEYS:
        number(value[key], f"{where}.{key}")
    if value["xMinMm"] >= value["xMaxMm"] or value["yMinMm"] >= value["yMaxMm"]:
        raise InputError(f"{where}: rectangle must have positive width and height")
    if tolerance:
        number(value["toleranceMm"], f"{where}.toleranceMm", 0)


def records(value, where, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise InputError(f"{where}: expected {'a nonempty' if nonempty else 'an'} array")
    result = {}
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise InputError(f"{where}[{i}]: expected an object")
        key = label(item.get("id"), f"{where}[{i}].id")
        if key in result:
            raise InputError(f"{where}: duplicate id {key}")
        result[key] = item
    return result


def reject_constant(value):
    raise InputError(f"JSON constant {value} is not supported")


def unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise InputError(f"Duplicate JSON key: {key}")
        out[key] = value
    return out


def load_document(path):
    data = Path(path).read_bytes()
    try:
        doc = json.loads(data.decode("utf-8-sig"), parse_constant=reject_constant,
                         object_pairs_hook=unique_object)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise InputError(f"{path}: invalid UTF-8 JSON: {exc}") from exc
    proof = {"file": str(Path(path).resolve()), "sha256": hashlib.sha256(data).hexdigest(),
             "sizeBytes": len(data)}
    return doc, proof


def validate(doc, expected):
    role = "requirements" if expected else "actual"
    base = {"schema", "units", "coordinates", "documentId", "source", "board", "components"}
    extra = {"keepouts", "maxDistances"} if expected else {"points", "inventoryComplete"}
    object_fields(doc, base | extra, set(), role)
    if doc["schema"] != f"eda-layout-{role}/v1":
        raise InputError(f"{role}.schema: unsupported schema")
    if doc["units"] != "mm" or doc["coordinates"] != "cartesian-y-up":
        raise InputError(f"{role}: only mm and cartesian-y-up are supported; adapt explicitly")
    label(doc["documentId"], f"{role}.documentId")
    src = object_fields(doc["source"], {"id", "description", "confirmed"} |
                        ({"independentOfActual"} if expected else set()), set(), f"{role}.source")
    label(src["id"], f"{role}.source.id")
    label(src["description"], f"{role}.source.description")
    if src["confirmed"] is not True:
        raise InputError(f"{role}.source.confirmed must be true; unconfirmed input cannot qualify")
    if expected and src["independentOfActual"] is not True:
        raise InputError("requirements.source.independentOfActual must be true")
    if not expected and doc["inventoryComplete"] is not True:
        raise InputError("actual.inventoryComplete must be true; partial inventories are unsupported")
    rect(doc["board"], f"{role}.board", tolerance=expected)
    components = records(doc["components"], f"{role}.components", nonempty=True)
    for cid, item in components.items():
        where = f"{role}.components[{cid}]"
        if expected:
            object_fields(item, {"id"}, {"fixedPosition", "side", "throughBoard", "connector", "allowedOverhangMm"}, where)
            if "fixedPosition" in item:
                point(item["fixedPosition"], where + ".fixedPosition", tolerance=True)
            if "allowedOverhangMm" in item:
                overhang = object_fields(item["allowedOverhangMm"], {"left", "right", "top", "bottom"}, set(),
                                         where + ".allowedOverhangMm")
                for key, value in overhang.items():
                    number(value, where + ".allowedOverhangMm." + key, 0)
        else:
            object_fields(item, {"id", "position", "side", "throughBoard", "envelope"}, {"connector"}, where)
            point(item["position"], where + ".position")
            rect(item["envelope"], where + ".envelope")
        if "side" in item:
            side(item["side"], where + ".side")
        if "throughBoard" in item:
            boolean(item["throughBoard"], where + ".throughBoard")
        if "connector" in item:
            conn = object_fields(item["connector"], {"anchor", "directionDeg"} |
                                 ({"directionToleranceDeg"} if expected else set()), set(), where + ".connector")
            point(conn["anchor"], where + ".connector.anchor", tolerance=expected)
            angle(conn["directionDeg"], where + ".connector.directionDeg")
            if expected:
                number(conn["directionToleranceDeg"], where + ".connector.directionToleranceDeg", 0, 180)
    if expected:
        for kid, item in records(doc["keepouts"], "requirements.keepouts").items():
            where = f"requirements.keepouts[{kid}]"
            object_fields(item, {"id", "rect", "sides"}, set(), where)
            rect(item["rect"], where + ".rect")
            sides = item["sides"]
            if not isinstance(sides, list) or not sides:
                raise InputError(where + ".sides: expected a nonempty array")
            for value in sides:
                side(value, where + ".sides")
            if len(set(sides)) != len(sides):
                raise InputError(where + ".sides: duplicate side")
        for rid, item in records(doc["maxDistances"], "requirements.maxDistances").items():
            where = f"requirements.maxDistances[{rid}]"
            object_fields(item, {"id", "fromPoint", "toPoint", "fromComponentId", "toComponentId", "maxMm"}, set(), where)
            label(item["fromPoint"], where + ".fromPoint")
            label(item["toPoint"], where + ".toPoint")
            if item["fromPoint"] == item["toPoint"]:
                raise InputError(where + ": two distinct named points are required")
            for key in ("fromComponentId", "toComponentId"):
                label(item[key], where + "." + key)
                if item[key] not in components:
                    raise InputError(where + ": distance endpoint owner is absent from requirements components")
            number(item["maxMm"], where + ".maxMm", 0)
    else:
        for pid, item in records(doc["points"], "actual.points").items():
            where = f"actual.points[{pid}]"
            object_fields(item, {"id", "componentId", "xMm", "yMm"}, set(), where)
            label(item["componentId"], where + ".componentId")
            if item["componentId"] not in components:
                raise InputError(where + ": componentId is absent from actual components")
            number(item["xMm"], where + ".xMm")
            number(item["yMm"], where + ".yMm")
    return doc


def distance(a, b):
    return math.hypot(a["xMm"] - b["xMm"], a["yMm"] - b["yMm"])


def contained(inner, outer):
    return (inner["xMinMm"] >= outer["xMinMm"] and inner["xMaxMm"] <= outer["xMaxMm"]
            and inner["yMinMm"] >= outer["yMinMm"] and inner["yMaxMm"] <= outer["yMaxMm"])


def intersects(a, b):
    # Closed keepouts: touching their boundary is a violation too.
    return not (a["xMaxMm"] < b["xMinMm"] or a["xMinMm"] > b["xMaxMm"]
                or a["yMaxMm"] < b["yMinMm"] or a["yMinMm"] > b["yMaxMm"])


def check(requirements, actual):
    e, a = validate(requirements, True), validate(actual, False)
    if e["source"]["id"] == a["source"]["id"]:
        raise InputError("Requirements and actual source IDs must differ; self-derived requirements cannot qualify")
    ec = {c["id"]: c for c in e["components"]}
    ac = {c["id"]: c for c in a["components"]}
    points = {p["id"]: p for p in a["points"]}
    # Refuse missing measurements before any PASS/FAIL board claim.
    for cid in sorted(ec.keys() & ac.keys()):
        if "connector" in ec[cid] and "connector" not in ac[cid]:
            raise InputError(f"actual component {cid}: required connector measurement is missing")
    for item in e["maxDistances"]:
        for key in ("fromPoint", "toPoint"):
            if item[key] not in points:
                raise InputError(f"Distance {item['id']}: named point {item[key]} has no measurement")
    findings = []
    checks = 0

    def assess(ok, code, object_id, rule_id, expected_value, actual_value):
        nonlocal checks
        checks += 1
        if not ok:
            findings.append({"code": code, "objectId": object_id, "ruleId": rule_id,
                             "expected": expected_value, "actual": actual_value})

    assess(e["documentId"] == a["documentId"], "DOCUMENT_MISMATCH", "document", "documentId",
           e["documentId"], a["documentId"])
    board_delta = {k: abs(e["board"][k] - a["board"][k]) for k in sorted(RECT_KEYS)}
    assess(all(v <= e["board"]["toleranceMm"] for v in board_delta.values()), "BOARD_MISMATCH",
           "board", "board", e["board"], {**a["board"], "coordinateDeltaMm": board_delta})
    for cid in sorted(ec.keys() - ac.keys()):
        assess(False, "COMPONENT_MISSING", cid, "inventory", "present", "absent")
    for cid in sorted(ac.keys() - ec.keys()):
        assess(False, "COMPONENT_EXTRA", cid, "inventory", "absent", "present")
    checks += len(ec.keys() & ac.keys())
    for cid, item in sorted(ac.items()):
        overhang = ec.get(cid, {}).get("allowedOverhangMm", {"left": 0, "right": 0, "top": 0, "bottom": 0})
        envelope_limit = {"xMinMm": a["board"]["xMinMm"] - overhang["left"],
                          "xMaxMm": a["board"]["xMaxMm"] + overhang["right"],
                          "yMinMm": a["board"]["yMinMm"] - overhang["bottom"],
                          "yMaxMm": a["board"]["yMaxMm"] + overhang["top"]}
        assess(contained(item["envelope"], envelope_limit), "OUTSIDE_BOARD", cid, "containment",
               {"envelopeLimit": envelope_limit, "allowedOverhangMm": overhang}, item["envelope"])
        for keepout in sorted(e["keepouts"], key=lambda k: k["id"]):
            applies = item["throughBoard"] or item["side"] in keepout["sides"]
            if applies:
                assess(not intersects(item["envelope"], keepout["rect"]), "KEEPOUT_INTERSECTION",
                       cid, keepout["id"], {"rect": keepout["rect"], "sides": keepout["sides"]},
                       {"envelope": item["envelope"], "side": item["side"], "throughBoard": item["throughBoard"]})
        if cid not in ec:
            continue
        req = ec[cid]
        for key, code in (("side", "SIDE_MISMATCH"), ("throughBoard", "THROUGH_BOARD_MISMATCH")):
            if key in req:
                assess(item[key] == req[key], code, cid, key, req[key], item[key])
        if "fixedPosition" in req:
            d = distance(req["fixedPosition"], item["position"])
            assess(d <= req["fixedPosition"]["toleranceMm"], "FIXED_POSITION_MISMATCH", cid,
                   "fixedPosition", req["fixedPosition"], {**item["position"], "distanceMm": d})
        if "connector" in req:
            target, measured = req["connector"], item["connector"]
            d = distance(target["anchor"], measured["anchor"])
            assess(d <= target["anchor"]["toleranceMm"], "CONNECTOR_ANCHOR_MISMATCH", cid,
                   "connectorAnchor", target["anchor"], {**measured["anchor"], "distanceMm": d})
            delta = abs((measured["directionDeg"] - target["directionDeg"] + 180) % 360 - 180)
            assess(delta <= target["directionToleranceDeg"], "CONNECTOR_DIRECTION_MISMATCH", cid,
                   "connectorDirection", {"directionDeg": target["directionDeg"],
                                          "toleranceDeg": target["directionToleranceDeg"]},
                   {"directionDeg": measured["directionDeg"], "deltaDeg": delta})
    for rule in sorted(e["maxDistances"], key=lambda r: r["id"]):
        for endpoint in ("from", "to"):
            measured = points[rule[endpoint + "Point"]]
            owner = rule[endpoint + "ComponentId"]
            assess(measured["componentId"] == owner, "POINT_OWNER_MISMATCH", measured["id"],
                   rule["id"], owner, measured["componentId"])
        d = distance(points[rule["fromPoint"]], points[rule["toPoint"]])
        assess(d <= rule["maxMm"], "POINT_DISTANCE_EXCEEDED", rule["fromPoint"] + " -> " + rule["toPoint"],
               rule["id"], {"maxMm": rule["maxMm"]}, {"distanceMm": d})
    findings.sort(key=lambda f: (f["code"], f["objectId"], f["ruleId"]))
    return {"schema": SCHEMA, "status": "FAIL" if findings else "PASS", "passed": not findings,
            "checked": True, "scope": "declared-rectangular-layout-contract", "nativeMutation": False,
            "documentId": {"requirements": e["documentId"], "actual": a["documentId"]},
            "sources": {"requirements": e["source"], "actual": a["source"]},
            "counts": {"requirementsComponents": len(ec), "actualComponents": len(ac),
                       "keepouts": len(e["keepouts"]), "distanceRules": len(e["maxDistances"]),
                       "checksEvaluated": checks, "findings": len(findings)},
            "findings": findings, "limitations": list(LIMITATIONS)}


def protect_output(path, inputs):
    out = Path(path).resolve()
    for item in inputs:
        source = Path(item).resolve()
        if out == source or (out.exists() and source.exists() and out.samefile(source)):
            raise InputError("Report path aliases an input file; refusing to overwrite input")
    return out


def write_report(path, report, inputs):
    out = protect_output(path, inputs)
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    # One output file, staged beside its destination. Never write through a hardlink.
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=out.parent,
                                         prefix=".layout-report-", suffix=".tmp", delete=False) as handle:
            name = handle.name
            handle.write(payload)
        protect_output(path, inputs)
        os.replace(name, out)
        name = None
    finally:
        if name is not None:
            try:
                os.unlink(name)
            except OSError:
                pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(message)


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("requirements")
    parser.add_argument("actual")
    parser.add_argument("--out", required=True)
    args = None
    inputs = []
    proofs = {}
    try:
        args = parser.parse_args(argv)
        inputs = [args.requirements, args.actual]
        protect_output(args.out, inputs)
        e, proofs["requirements"] = load_document(args.requirements)
        a, proofs["actual"] = load_document(args.actual)
        report = check(e, a)
        report["inputs"] = proofs
        write_report(args.out, report, inputs)
        print(json.dumps({"status": report["status"], "checked": True,
                          "findings": len(report["findings"]), "report": str(Path(args.out).resolve())}))
        return 0 if report["passed"] else 1
    except (InputError, OSError, UnicodeError, OverflowError, RecursionError) as exc:
        report = {"schema": SCHEMA, "status": "ERROR", "passed": False, "checked": False,
                  "inputs": proofs, "findings": [], "error": {"code": "INVALID_OR_UNSUPPORTED_INPUT", "message": str(exc)},
                  "limitations": list(LIMITATIONS), "nativeMutation": False}
        if args is not None:
            try:
                write_report(args.out, report, inputs)
            except (InputError, OSError):
                pass
        print(json.dumps(report, ensure_ascii=False, allow_nan=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
