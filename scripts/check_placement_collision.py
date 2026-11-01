#!/usr/bin/env python3
"""Placement gate: 2D AABB bounding-box collision detection, no EDA access.

Python 3.10+, standard library only.

Each component's bounding box is built from the extremes of ALL its pads
(min/max X, Y) — or an explicit outline — then expanded by a margin. Any two
components that occupy the same side (or a through-hole part) must have zero
box overlap. For every collision the minimum separation vector is reported so
the agent can move one part via the bridge before routing.

Use explicit maximum body/courtyard outlines for assembly checks; pad bounds
alone cannot detect a can/body overhang. Copper crossings are out of scope.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile


SCHEMA = "placement-collision-report/v1"
SIDES = ("TOP", "BOTTOM")
DEFAULT_MARGIN_MM = 0.3

LIMITATIONS = [
    "Only axis-aligned 2D bounding boxes from pad extremes (or explicit outline) plus a margin are tested; "
    "true irregular courtyard/3D body collision is not modelled.",
    "Pad coordinates must be global board coordinates; the script does not transform footprint-local points.",
    "Through-hole parts are treated as occupying both sides; same-side red-line crossings needing a via are a "
    "routing concern reported separately, not inferred here.",
    "Separation vectors are suggested minimum pushes; the actual move is performed by the bridge and must be "
    "re-checked afterwards.",
    "No copper, netlist, electrical, native DRC or manufacturing qualification is performed.",
]


class InputError(ValueError):
    pass


def point_xy(p, where):
    if not isinstance(p, dict) or "xMm" not in p or "yMm" not in p:
        raise InputError(f"{where}: expected xMm/yMm")
    x, y = p["xMm"], p["yMm"]
    if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) \
            or not isinstance(y, (int, float)) or not math.isfinite(x) or not math.isfinite(y):
        raise InputError(f"{where}: coordinates must be numbers")
    return float(x), float(y)


def pad_extents(pad, where):
    """AABB of one pad from its centre plus diameterMm or widthMm/heightMm."""
    x, y = point_xy(pad, where)
    hw = hh = 0.0
    if "diameterMm" in pad:
        d = pad["diameterMm"]
        if isinstance(d, bool) or not isinstance(d, (int, float)) or not math.isfinite(d) or d < 0:
            raise InputError(f"{where}.diameterMm: expected a number >= 0")
        hw = hh = float(d) / 2
    if "widthMm" in pad or "heightMm" in pad:
        for k in ("widthMm", "heightMm"):
            if k not in pad or isinstance(pad[k], bool) or not isinstance(pad[k], (int, float)) or not math.isfinite(pad[k]) or pad[k] < 0:
                raise InputError(f"{where}.{k}: expected a number >= 0")
        hw = float(pad["widthMm"]) / 2
        hh = float(pad["heightMm"]) / 2
    return x - hw, y - hh, x + hw, y + hh


def build_box(comp, default_margin):
    cid = comp["id"]
    where = f"components[{cid}]"
    extents = []
    if "pads" in comp:
        pads = comp["pads"]
        if not isinstance(pads, list) or not pads:
            raise InputError(f"{where}.pads: expected a nonempty array")
        xmin = ymin = float("inf")
        xmax = ymax = float("-inf")
        for i, pad in enumerate(pads):
            px0, py0, px1, py1 = pad_extents(pad, f"{where}.pads[{i}]")
            xmin, ymin = min(xmin, px0), min(ymin, py0)
            xmax, ymax = max(xmax, px1), max(ymax, py1)
        extents.append((xmin, ymin, xmax, ymax))
    if "outline" in comp:
        o = comp["outline"]
        for k in ("xMinMm", "yMinMm", "xMaxMm", "yMaxMm"):
            if k not in o or isinstance(o[k], bool) or not isinstance(o[k], (int, float)) or not math.isfinite(o[k]):
                raise InputError(f"{where}.outline.{k}: expected a number")
        xmin, ymin, xmax, ymax = (float(o["xMinMm"]), float(o["yMinMm"]),
                                  float(o["xMaxMm"]), float(o["yMaxMm"]))
        if xmax <= xmin or ymax <= ymin:
            raise InputError(f"{where}: outline must have positive width/height")
        extents.append((xmin, ymin, xmax, ymax))
    if not extents:
        raise InputError(f"{where}: provide either pads or outline")

    xmin = min(e[0] for e in extents); ymin = min(e[1] for e in extents)
    xmax = max(e[2] for e in extents); ymax = max(e[3] for e in extents)

    margin = float(comp.get("marginMm", default_margin))
    if not math.isfinite(margin) or margin < 0:
        raise InputError(f"{where}.marginMm: must be >= 0")
    # Pads may be collinear (e.g. axial part); the margin still gives a courtyard.
    return {
        "xMinMm": xmin - margin,
        "yMinMm": ymin - margin,
        "xMaxMm": xmax + margin,
        "yMaxMm": ymax + margin,
    }


def same_plane(a, b):
    # Through-hole occupies both sides; otherwise sides must match.
    if a.get("throughBoard") or b.get("throughBoard"):
        return True
    return a.get("side", "TOP") == b.get("side", "TOP")


def overlap(a, b):
    """Strict overlap: touching edges (zero gap) is allowed."""
    ox = min(a["xMaxMm"], b["xMaxMm"]) - max(a["xMinMm"], b["xMinMm"])
    oy = min(a["yMaxMm"], b["yMaxMm"]) - max(a["yMinMm"], b["yMinMm"])
    if ox > 0 and oy > 0:
        return ox, oy
    return None


def separation(a_box, b_box, b_id):
    """Shortest axis-aligned translation that fully separates b, including containment."""
    choices = [(a_box['xMaxMm']-b_box['xMinMm'], 'x', '+x'),
               (b_box['xMaxMm']-a_box['xMinMm'], 'x', '-x'),
               (a_box['yMaxMm']-b_box['yMinMm'], 'y', '+y'),
               (b_box['yMaxMm']-a_box['yMinMm'], 'y', '-y')]
    distance, axis, direction = min(choices, key=lambda v:v[0])
    return {'move':b_id, 'axis':axis, 'distanceMm':math.ceil(distance*1000)/1000,
            'direction':direction}


def check(doc):
    if doc.get("schema") != "placement-collision/v1":
        raise InputError("input schema must be placement-collision/v1")
    default_margin = float(doc.get("defaultMarginMm", DEFAULT_MARGIN_MM))
    if not math.isfinite(default_margin) or default_margin < 0:
        raise InputError('defaultMarginMm: expected a finite nonnegative number')
    components = doc.get("components")
    if not isinstance(components, list) or not components:
        raise InputError("components: expected a nonempty array")

    boxes = {}
    seen = set()
    for comp in components:
        if not isinstance(comp, dict) or not isinstance(comp.get("id"), str) or not comp["id"].strip():
            raise InputError("components[].id: expected a nonempty string")
        cid = comp["id"]
        if cid in seen:
            raise InputError(f"components: duplicate id {cid}")
        seen.add(cid)
        side = comp.get("side", "TOP")
        if side not in SIDES:
            raise InputError(f"{cid}.side: must be TOP or BOTTOM")
        boxes[cid] = build_box(comp, default_margin)

    ids = list(boxes.keys())
    collisions = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a_id, b_id = ids[i], ids[j]
            ca = next(c for c in components if c["id"] == a_id)
            cb = next(c for c in components if c["id"] == b_id)
            if not same_plane(ca, cb):
                continue
            hit = overlap(boxes[a_id], boxes[b_id])
            if hit:
                ox, oy = hit
                collisions.append({
                    "a": a_id, "b": b_id,
                    "aBox": boxes[a_id], "bBox": boxes[b_id],
                    "overlapMm": {"x": round(ox, 3), "y": round(oy, 3)},
                    "separation": separation(boxes[a_id], boxes[b_id], b_id),
                })

    collisions.sort(key=lambda c: (c["a"], c["b"]))
    return {
        "schema": SCHEMA,
        "status": "FAIL" if collisions else "PASS",
        "passed": not collisions,
        "checked": True,
        "scope": "2d-aabb-placement-collision",
        "defaultMarginMm": default_margin,
        "counts": {"components": len(ids), "collisions": len(collisions)},
        "collisions": collisions,
        "limitations": list(LIMITATIONS),
        "nativeMutation": False,
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
                                         prefix=".collision-report-", suffix=".tmp", delete=False) as h:
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
    parser.add_argument("actual")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    inputs = [args.actual]
    try:
        data = Path(args.actual).read_bytes()
        try:
            doc = json.loads(data.decode("utf-8-sig"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise InputError(f"{args.actual}: invalid UTF-8 JSON: {exc}") from exc
        report = check(doc)
        write_report(args.out, report, inputs)
        print(json.dumps({"status": report["status"], "checked": True,
                          "collisions": len(report["collisions"]),
                          "report": str(Path(args.out).resolve())}))
        return 0 if report["passed"] else 1
    except (InputError, OSError, UnicodeError, OverflowError) as exc:
        print(json.dumps({"status": "ERROR", "error": {"message": str(exc)}},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
