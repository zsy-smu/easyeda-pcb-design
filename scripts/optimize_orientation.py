#!/usr/bin/env python3
"""Orientation sweep for two-pin parts: pick the rotation that keeps ratlines
short and uncrossed. No EDA access; writes a standalone recommendation.

For every two-pin part all FOUR rotations are evaluated. Rotating a footprint
is a rigid-body move: each pad keeps its bound net (and the silkscreen polarity
mark rotates with it), so a diode/capacitor rotated 180 degrees is NOT reversed.
But 0 vs 180 are not equivalent: Pad1 and Pad2 bind different fixed targets, so
the rotation changes which pad sits near its target.
  - 0/180 place the pads on the X axis (Pad1 left/right),
  - 90/270 place them on the Y axis (Pad1 bottom/top).
Cost is the sum of Manhattan distances pad1->target1 + pad2->target2; a same-layer
crossing of the two ratlines is a hard penalty. The best rotation is reported for
the bridge to apply (modify rotation), then re-checked.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


SCHEMA = "orientation-spec/v1"
ROTATIONS = (0, 90, 180, 270)

LIMITATIONS = [
    "Only two-pin parts with a single pitch and a global target point per pad are modelled.",
    "Cost is Manhattan distance plus a pairwise ratline crossing; it is not a full router and ignores "
    "obstacles, vias, copper and other nets.",
    "The output is a recommended rotation to apply via the bridge and re-check; it does not mutate the EDA.",
    "Net binding is assumed to follow the pad number; the script never swaps pins or edits the netlist.",
]


class InputError(ValueError):
    pass


def xy(value, where):
    if not isinstance(value, dict) or "xMm" not in value or "yMm" not in value:
        raise InputError(f"{where}: expected xMm/yMm")
    x, y = value["xMm"], value["yMm"]
    if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) \
            or not isinstance(y, (int, float)):
        raise InputError(f"{where}: coordinates must be numbers")
    return float(x), float(y)


def pad_positions(cx, cy, pitch, rotation):
    h = pitch / 2.0
    if rotation == 0:
        return (cx - h, cy), (cx + h, cy)
    if rotation == 90:
        return (cx, cy - h), (cx, cy + h)
    if rotation == 180:
        return (cx + h, cy), (cx - h, cy)
    return (cx, cy + h), (cx, cy - h)  # 270


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_cross(p1, q1, p2, q2):
    """Proper intersection of two open segments (shared endpoint/collinear ignored)."""
    o1, o2 = orient(p1, q1, p2), orient(p1, q1, q2)
    o3, o4 = orient(p2, q2, p1), orient(p2, q2, q1)
    return ((o1 > 0 and o2 < 0) or (o1 < 0 and o2 > 0)) and \
           ((o3 > 0 and o4 < 0) or (o3 < 0 and o4 > 0))


def evaluate(comp):
    cid = comp["id"]
    where = f"components[{cid}]"
    cx, cy = xy(comp["center"], where + ".center")
    pitch = comp.get("pitchMm")
    if isinstance(pitch, bool) or not isinstance(pitch, (int, float)) or pitch <= 0:
        raise InputError(f"{where}.pitchMm: expected a number > 0")
    t1 = xy(comp["pad1"]["target"], where + ".pad1.target")
    t2 = xy(comp["pad2"]["target"], where + ".pad2.target")
    current = comp.get("currentRotationDeg", 0)
    if current not in ROTATIONS:
        raise InputError(f"{where}.currentRotationDeg: must be one of 0/90/180/270")

    evaluations = []
    for rot in ROTATIONS:
        p1, p2 = pad_positions(cx, cy, float(pitch), rot)
        cost = manhattan(p1, t1) + manhattan(p2, t2)
        crossing = segments_cross(p1, t1, p2, t2)
        evaluations.append({"rotationDeg": rot, "costMm": round(cost, 3),
                            "crossing": crossing,
                            "pad1": {"xMm": round(p1[0], 3), "yMm": round(p1[1], 3)},
                            "pad2": {"xMm": round(p2[0], 3), "yMm": round(p2[1], 3)}})

    def rank(e):
        return (1 if e["crossing"] else 0, e["costMm"], e["rotationDeg"])

    best = min(evaluations, key=rank)
    current_eval = next(e for e in evaluations if e["rotationDeg"] == current)
    return {
        "id": cid,
        "currentRotationDeg": current,
        "recommendedRotationDeg": best["rotationDeg"],
        "improved": rank(best) < rank(current_eval),
        "currentCostMm": current_eval["costMm"],
        "bestCostMm": best["costMm"],
        "costDeltaMm": round(current_eval["costMm"] - best["costMm"], 3),
        "removesCrossing": current_eval["crossing"] and not best["crossing"],
        "evaluations": evaluations,
    }


def build_spec(doc):
    if doc.get("schema") != "orientation-targets/v1":
        raise InputError("input schema must be orientation-targets/v1")
    components = doc.get("components")
    if not isinstance(components, list) or not components:
        raise InputError("components: expected a nonempty array")
    seen = set()
    results = []
    for comp in components:
        if not isinstance(comp, dict) or not isinstance(comp.get("id"), str) or not comp["id"].strip():
            raise InputError("components[].id: expected a nonempty string")
        if comp["id"] in seen:
            raise InputError(f"components: duplicate id {comp['id']}")
        seen.add(comp["id"])
        results.append(evaluate(comp))
    improved = sum(1 for r in results if r["improved"])
    return {
        "schema": SCHEMA,
        "counts": {"components": len(results), "improved": improved},
        "components": results,
        "limitations": list(LIMITATIONS),
        "nativeMutation": False,
    }


PRESET = {
    "schema": "orientation-targets/v1",
    "components": [
        {"id": "R13", "center": {"xMm": 5, "yMm": 5}, "pitchMm": 2.0,
         "currentRotationDeg": 180,
         "pad1": {"target": {"xMm": 0, "yMm": 5}},
         "pad2": {"target": {"xMm": 10, "yMm": 5}}},
        {"id": "D2", "center": {"xMm": 20, "yMm": 8}, "pitchMm": 3.0,
         "currentRotationDeg": 0,
         "pad1": {"target": {"xMm": 20, "yMm": 0}},
         "pad2": {"target": {"xMm": 20, "yMm": 16}}},
    ],
}


def protect_output(path, inputs):
    out = Path(path).resolve()
    for item in inputs:
        src = Path(item).resolve()
        if out == src or (out.exists() and src.exists() and out.samefile(src)):
            raise InputError("Report path aliases an input file; refusing to overwrite input")
    return out


def write_report(path, report, inputs):
    out = protect_output(path, inputs)
    name = None
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=out.parent,
                                         prefix=".orientation-", suffix=".tmp", delete=False) as h:
            name = h.name
            h.write(payload)
        protect_output(path, inputs)
        os.replace(name, out)
        name = None
    finally:
        if name is not None:
            try:
                os.unlink(name)
            except OSError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="?")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    inputs = [args.targets] if args.targets else []
    try:
        if args.targets:
            data = Path(args.targets).read_bytes()
            try:
                doc = json.loads(data.decode("utf-8-sig"))
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise InputError(f"{args.targets}: invalid UTF-8 JSON: {exc}") from exc
        else:
            doc = PRESET
        spec = build_spec(doc)
        write_report(args.out, spec, inputs)
        print(json.dumps({"status": "OK", "components": len(spec["components"]),
                          "improved": spec["counts"]["improved"],
                          "report": str(Path(args.out).resolve())}))
        return 0
    except (InputError, OSError, UnicodeError, OverflowError) as exc:
        print(json.dumps({"status": "ERROR", "error": {"message": str(exc)}},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
