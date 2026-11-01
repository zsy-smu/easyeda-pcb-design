#!/usr/bin/env python3
"""Connector orientation gate: edge parts must sit on the edge band, mate
outward, align their pin row to the edge, and USB connectors straddle the edge.

No EDA access; writes a standalone report. Catches the failures native DRC
cannot: a debug header buried under the MCU, a board-edge pin header whose
mating face points inward (blocked by a capacitor), and a USB connector that
does not cross the board outline.

Rules per connector (J*/CN*/SB*/RV* and USB):
  - its box must reach the edge band (within edgeSetbackMm of one edge);
  - its mating normal must point outward, away from the board centre;
  - the pin-row axis must be parallel to the edge it sits on (vertical edge ->
    pads stacked along Y, horizontal edge -> pads along X);
  - kind "usb" must straddle the edge line (part inside, part outside).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


SCHEMA = "connector-orientation-report/v1"
EDGES = ("left", "right", "bottom", "top")
KINDS = ("header", "terminal", "usb", "button", "pot", "other")

LIMITATIONS = [
    "Only axis-aligned boards/boxes and a single nearest edge per connector are modelled.",
    "The mating normal is a declared unit vector; the script does not verify the actual 3D connector model.",
    "Pin-row alignment uses pad extremes; collinear pads without a declared size cannot show an axis.",
    "No copper, netlist, silkscreen, 3D clearance, native DRC or manufacturing qualification is performed.",
]


class InputError(ValueError):
    pass


def num(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{where}: expected a number")
    return float(value)


def box_from_pads(pads, where):
    if not isinstance(pads, list) or not pads:
        raise InputError(f"{where}: expected a nonempty pads array")
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    for i, pad in enumerate(pads):
        pwhere = f"{where}[{i}]"
        x, y = num(pad.get("xMm"), pwhere + ".xMm"), num(pad.get("yMm"), pwhere + ".yMm")
        hw = hh = 0.0
        if "diameterMm" in pad:
            hw = hh = num(pad["diameterMm"], pwhere + ".diameterMm") / 2
        if "widthMm" in pad or "heightMm" in pad:
            hw = num(pad["widthMm"], pwhere + ".widthMm") / 2
            hh = num(pad["heightMm"], pwhere + ".heightMm") / 2
        xmin, ymin = min(xmin, x - hw), min(ymin, y - hh)
        xmax, ymax = max(xmax, x + hw), max(ymax, y + hh)
    return {"xMinMm": xmin, "yMinMm": ymin, "xMaxMm": xmax, "yMaxMm": ymax}


def nearest_edge(box, board):
    distances = {
        "left": box["xMinMm"] - board["xMinMm"],
        "right": board["xMaxMm"] - box["xMaxMm"],
        "bottom": box["yMinMm"] - board["yMinMm"],
        "top": board["yMaxMm"] - box["yMaxMm"],
    }
    edge = min(distances, key=distances.get)
    return edge, distances[edge]


def outward_normal(edge):
    return {"left": (-1, 0), "right": (1, 0),
            "bottom": (0, -1), "top": (0, 1)}[edge]


def check_connector(conn, board, setback):
    cid = conn["id"]
    where = f"connectors[{cid}]"
    kind = conn.get("kind", "header")
    if kind not in KINDS:
        raise InputError(f"{where}.kind: must be one of {', '.join(KINDS)}")
    if "pads" in conn:
        box = box_from_pads(conn["pads"], where + ".pads")
    elif "outline" in conn:
        o = conn["outline"]
        box = {k: num(o.get(k), where + ".outline." + k)
               for k in ("xMinMm", "yMinMm", "xMaxMm", "yMaxMm")}
    else:
        raise InputError(f"{where}: provide pads or outline")

    edge, gap = nearest_edge(box, board)
    if conn.get("edge") and conn["edge"] in EDGES:
        edge = conn["edge"]
        gap = {
            "left": box["xMinMm"] - board["xMinMm"],
            "right": board["xMaxMm"] - box["xMaxMm"],
            "bottom": box["yMinMm"] - board["yMinMm"],
            "top": board["yMaxMm"] - box["yMaxMm"],
        }[edge]

    findings = []

    def add(code, expected, actual):
        findings.append({"code": code, "expected": expected, "actual": actual})

    # 1. Edge band.
    if gap > setback:
        add("NOT_ON_EDGE_BAND", {"edge": edge, "maxGapMm": setback},
            {"edge": edge, "gapMm": round(gap, 3)})

    # Orientation checks only make sense when the part is actually on the edge band.
    on_band = gap <= setback

    # 2. Mating normal outward.
    if on_band and "matingNormal" in conn:
        n = conn["matingNormal"]
        nx, ny = num(n.get("x"), where + ".matingNormal.x"), num(n.get("y"), where + ".matingNormal.y")
        ex, ey = outward_normal(edge)
        if nx * ex + ny * ey <= 0:
            add("MATING_FACE_INWARD", {"normal": {"x": ex, "y": ey}},
                {"normal": {"x": nx, "y": ny}})

    # 3. Pin-row axis parallel to the edge. Multi-pin headers/terminals stack pins
    # along the edge; USB is exempt (its pads run along the insertion axis and it is
    # gated by the straddle rule instead).
    span_x = box["xMaxMm"] - box["xMinMm"]
    span_y = box["yMaxMm"] - box["yMinMm"]
    if on_band and kind != "usb" and (span_x > 0 or span_y > 0):
        vertical_edge = edge in ("left", "right")
        pads_vertical = span_y > span_x
        if vertical_edge and not pads_vertical:
            add("PIN_ROW_NOT_PARALLEL", "pads along Y", {"spanX": round(span_x, 3),
                                                          "spanY": round(span_y, 3)})
        if not vertical_edge and pads_vertical:
            add("PIN_ROW_NOT_PARALLEL", "pads along X", {"spanX": round(span_x, 3),
                                                          "spanY": round(span_y, 3)})

    # 4. USB straddles the edge line.
    if kind == "usb":
        if edge == "left" and not (box["xMinMm"] < board["xMinMm"] < box["xMaxMm"]):
            add("USB_NOT_STRADDLING_EDGE", "box crosses left edge", box)
        elif edge == "right" and not (box["xMinMm"] < board["xMaxMm"] < box["xMaxMm"]):
            add("USB_NOT_STRADDLING_EDGE", "box crosses right edge", box)
        elif edge == "bottom" and not (box["yMinMm"] < board["yMinMm"] < box["yMaxMm"]):
            add("USB_NOT_STRADDLING_EDGE", "box crosses bottom edge", box)
        elif edge == "top" and not (box["yMinMm"] < board["yMaxMm"] < box["yMaxMm"]):
            add("USB_NOT_STRADDLING_EDGE", "box crosses top edge", box)

    return {"id": cid, "kind": kind, "edge": edge, "edgeGapMm": round(gap, 3),
            "box": {k: round(v, 3) for k, v in box.items()}, "findings": findings}


def check(doc):
    if doc.get("schema") != "connector-orientation/v1":
        raise InputError("input schema must be connector-orientation/v1")
    b = doc.get("board")
    if not isinstance(b, dict):
        raise InputError("board: expected an object")
    board = {k: num(b.get(k), "board." + k) for k in ("xMinMm", "yMinMm", "xMaxMm", "yMaxMm")}
    if board["xMaxMm"] <= board["xMinMm"] or board["yMaxMm"] <= board["yMinMm"]:
        raise InputError("board: positive width/height required")
    setback = float(doc.get("edgeSetbackMm", 5.0))
    connectors = doc.get("connectors")
    if not isinstance(connectors, list) or not connectors:
        raise InputError("connectors: expected a nonempty array")

    seen = set()
    results = []
    all_findings = []
    for conn in connectors:
        if not isinstance(conn, dict) or not isinstance(conn.get("id"), str) or not conn["id"].strip():
            raise InputError("connectors[].id: expected a nonempty string")
        if conn["id"] in seen:
            raise InputError(f"connectors: duplicate id {conn['id']}")
        seen.add(conn["id"])
        r = check_connector(conn, board, setback)
        results.append(r)
        for f in r["findings"]:
            all_findings.append({"objectId": r["id"], **f})

    all_findings.sort(key=lambda f: (f["code"], f["objectId"]))
    return {
        "schema": SCHEMA,
        "status": "FAIL" if all_findings else "PASS",
        "passed": not all_findings,
        "checked": True,
        "scope": "connector-edge-orientation",
        "edgeSetbackMm": setback,
        "counts": {"connectors": len(results), "findings": len(all_findings)},
        "connectors": results,
        "findings": all_findings,
        "limitations": list(LIMITATIONS),
        "nativeMutation": False,
    }


PRESET = {
    "schema": "connector-orientation/v1",
    "board": {"xMinMm": 0, "yMinMm": 0, "xMaxMm": 100, "yMaxMm": 80},
    "edgeSetbackMm": 5.0,
    "connectors": [
        {"id": "J1_GOOD", "kind": "header",
         "pads": [{"xMm": 2, "yMm": 30, "diameterMm": 1.2},
                  {"xMm": 2, "yMm": 34, "diameterMm": 1.2}],
         "matingNormal": {"x": -1, "y": 0}},
        {"id": "J2_BURIED", "kind": "header",
         "pads": [{"xMm": 48, "yMm": 38, "diameterMm": 1.2},
                  {"xMm": 52, "yMm": 38, "diameterMm": 1.2}],
         "matingNormal": {"x": 1, "y": 0}},
        {"id": "USB1", "kind": "usb",
         "pads": [{"xMm": -2, "yMm": 40, "widthMm": 1.5, "heightMm": 1.0},
                  {"xMm": 2, "yMm": 40, "widthMm": 1.5, "heightMm": 1.0}],
         "matingNormal": {"x": -1, "y": 0}},
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
                                         prefix=".conn-orient-", suffix=".tmp", delete=False) as h:
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
        report = check(doc)
        write_report(args.out, report, inputs)
        print(json.dumps({"status": report["status"], "checked": True,
                          "findings": len(report["findings"]),
                          "report": str(Path(args.out).resolve())}))
        return 0 if report["passed"] else 1
    except (InputError, OSError, UnicodeError, OverflowError) as exc:
        print(json.dumps({"status": "ERROR", "error": {"message": str(exc)}},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
