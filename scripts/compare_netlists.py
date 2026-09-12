#!/usr/bin/env python3
"""Read-only logical-net comparison. Python 3.10+, standard library only.

Supported: explicit net maps, eda-netlist/v1 components, and observed EasyEDA
components/props/pinInfoMap exports. No geometry or native DRC is inferred.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import sys
from pathlib import Path


class InputError(ValueError):
    pass


def reject_constant(value):
    raise InputError("Non-finite JSON value: " + value)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def parse_json(text):
    try:
        return json.loads(text, object_pairs_hook=unique_keys,
                          parse_constant=reject_constant)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise InputError(str(exc)) from exc


def load_json(path):
    return parse_json(Path(path).read_text(encoding="utf-8-sig"))


def label(value, field, integer=False):
    if integer and isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value or value != value.strip():
        raise InputError(field + " must be a nonempty string without outer whitespace")
    return value


def net_name(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InputError("Net must be a string, empty string, or null")
    # Network spelling, case, quotes, and whitespace are never normalized silently.
    return value


def select_document(value, index=None):
    if isinstance(value, list):
        if not value:
            raise InputError("Netlist document collection is empty")
        if index is None:
            if len(value) != 1:
                raise InputError("Multiple documents: select an explicit zero-based document index")
            index = 0
        if index < 0 or index >= len(value):
            raise InputError("Document index is out of range")
        value = value[index]
    elif index is not None:
        raise InputError("Document index supplied for a single object")
    if isinstance(value, str):
        value = parse_json(value)
    if not isinstance(value, dict):
        raise InputError("Selected netlist must be a JSON object")
    return value


def normalize(value, index=None, aliases=None):
    obj = select_document(value, index)
    if "schema" in obj and obj["schema"] != "eda-netlist/v1":
        raise InputError("Unsupported netlist schema: " + str(obj["schema"]))
    aliases = {} if aliases is None else aliases
    if not isinstance(aliases, dict):
        raise InputError("Aliases must be an object of exact source-name to target-name pairs")
    for a, b in aliases.items():
        label(a, "Alias source"); label(b, "Alias destination")
    endpoints, nc, components = {}, {}, set()

    def add(ref, pin, net, no_connect=None):
        ref, pin = label(ref, "Component ref"), label(pin, "Pin number", integer=True)
        key = (ref, pin)
        if key in endpoints:
            raise InputError("Duplicate logical endpoint: " + ref + "." + pin)
        net = net_name(net)
        net = aliases.get(net, net) if net is not None else None
        if no_connect is not None and not isinstance(no_connect, bool):
            raise InputError("no_connect must be boolean when supplied")
        if no_connect and net is not None:
            raise InputError("Connected endpoint cannot also be NC: " + ref + "." + pin)
        endpoints[key] = net
        nc[key] = no_connect
        components.add(ref)

    if "nets" in obj:
        if "components" in obj:
            raise InputError("Ambiguous input: use nets or components, not both")
        if not isinstance(obj["nets"], dict):
            raise InputError("nets must map exact network names to endpoint arrays")
        for net, members in obj["nets"].items():
            if not isinstance(members, list) or not members:
                raise InputError("Each declared net needs a nonempty endpoint array")
            for member in members:
                if not isinstance(member, dict):
                    raise InputError("Net member must contain ref and pin")
                add(member.get("ref"), member.get("pin"), net, member.get("no_connect"))
        if "unconnected" in obj:
            if not isinstance(obj["unconnected"], list):
                raise InputError("unconnected must be an endpoint array")
            for member in obj["unconnected"]:
                if not isinstance(member, dict):
                    raise InputError("Unconnected member must be an object")
                add(member.get("ref"), member.get("pin"), None, member.get("no_connect"))
        format_name = "explicit-net-map"
    elif isinstance(obj.get("components"), dict):
        seen_refs = set()
        for uid, component in obj["components"].items():
            if not isinstance(component, dict) or not isinstance(component.get("props"), dict):
                raise InputError("EasyEDA component requires props")
            ref = label(component["props"].get("Designator"), "props.Designator")
            if ref in seen_refs:
                raise InputError("Duplicate component designator: " + ref)
            seen_refs.add(ref); components.add(ref)
            pins = component.get("pinInfoMap")
            if not isinstance(pins, dict):
                raise InputError("Unsupported component pins; expected pinInfoMap")
            for key, pin in pins.items():
                if not isinstance(pin, dict) or "net" not in pin:
                    raise InputError("pinInfoMap item requires explicit net")
                number = label(pin.get("number", key), "Pin number", integer=True)
                if number != str(key):
                    raise InputError("pinInfoMap key/number mismatch at " + ref + "." + str(key))
                add(ref, number, pin["net"], pin.get("no_connect"))
        format_name = "easyeda-components-pinInfoMap"
    elif isinstance(obj.get("components"), list) and obj.get("schema") == "eda-netlist/v1":
        seen_refs = set()
        for component in obj["components"]:
            if not isinstance(component, dict):
                raise InputError("Component must be an object")
            ref = label(component.get("ref"), "Component ref")
            if ref in seen_refs:
                raise InputError("Duplicate component designator: " + ref)
            seen_refs.add(ref); components.add(ref)
            if not isinstance(component.get("pins"), list):
                raise InputError("Component pins must be an array")
            for pin in component["pins"]:
                if not isinstance(pin, dict) or "net" not in pin:
                    raise InputError("Pin requires number and explicit net")
                add(ref, pin.get("number"), pin["net"], pin.get("no_connect"))
        format_name = "eda-netlist/v1-components"
    else:
        raise InputError("Unsupported netlist structure; no recognized nets or components")
    if not endpoints:
        raise InputError("No logical endpoints; empty input cannot certify connectivity")
    pads, pad_findings, seen_ids = obj.get("physicalPads"), [], set()
    if pads is not None:
        if not isinstance(pads, list) or not pads:
            raise InputError("physicalPads must be a nonempty complete physical inventory")
        for pad in pads:
            if not isinstance(pad, dict) or "net" not in pad:
                raise InputError("Physical pad requires id, ref, pin and explicit net")
            pid = label(pad.get("id"), "Physical pad id")
            if pid in seen_ids:
                raise InputError("Duplicate physical pad id: " + pid)
            seen_ids.add(pid)
            key = (label(pad.get("ref"), "Pad ref"), label(pad.get("pin"), "Pad pin", True))
            net = net_name(pad["net"])
            net = aliases.get(net, net) if net is not None else None
            if key not in endpoints or endpoints[key] != net:
                pad_findings.append({"id": pid, "ref": key[0], "pin": key[1],
                                     "net": net, "logicalNet": endpoints.get(key),
                                     "logicalEndpointPresent": key in endpoints})
        present = {(p["ref"], str(p["pin"])) for p in pads}
        for key in sorted(set(endpoints) - present):
            pad_findings.append({"ref": key[0], "pin": key[1], "missingPhysicalPad": True})
    return {"format": format_name, "endpoints": endpoints, "nc": nc,
            "components": components, "physicalPads": pads, "physicalFindings": pad_findings,
            "documentIndex": index, "aliases": aliases}


def counts(doc):
    endpoints = doc["endpoints"]
    return {"components": len(doc["components"]), "logicalEndpoints": len(endpoints),
            "connectedLogicalEndpoints": sum(n is not None for n in endpoints.values()),
            "unconnectedLogicalEndpoints": sum(n is None for n in endpoints.values()),
            "explicitNc": sum(n is True for n in doc["nc"].values()),
            "nets": len({n for n in endpoints.values() if n is not None}),
            "physicalPads": None if doc["physicalPads"] is None else len(doc["physicalPads"])}


def compare(expected, actual, scope="all", require_physical=False):
    if scope not in ("all", "connected"):
        raise InputError("scope must be all or connected")
    if (require_physical or expected["physicalPads"] is not None) and actual["physicalPads"] is None:
        raise InputError("Actual physicalPads inventory required but unavailable")
    # A contradictory expected manifest is an invalid specification, not an actual-board fault.
    if expected["physicalFindings"]:
        raise InputError("Expected physicalPads contradict expected logical endpoints")
    physical_inventory_differences = []
    if expected["physicalPads"] is not None:
        # Exporters may regenerate entity IDs. Compare physical multiplicity per
        # logical endpoint, retaining duplicate-number pads instead of collapsing them.
        def multiplicity(doc):
            return Counter((p["ref"], str(p["pin"])) for p in doc["physicalPads"])
        pe, pa = multiplicity(expected), multiplicity(actual)
        physical_inventory_differences = [
            {"ref": key[0], "pin": key[1], "expectedCount": pe[key], "actualCount": pa[key]}
            for key in sorted(pe.keys() | pa.keys()) if pe[key] != pa[key]]
    e = {k: v for k, v in expected["endpoints"].items() if scope == "all" or v is not None}
    a = {k: v for k, v in actual["endpoints"].items() if scope == "all" or v is not None}
    row = lambda k, n: {"ref": k[0], "pin": k[1], "net": n}
    missing = [row(k, e[k]) for k in sorted(e.keys() - a.keys())]
    extra = [row(k, a[k]) for k in sorted(a.keys() - e.keys())]
    wrong = [{"ref": k[0], "pin": k[1], "expected": e[k], "actual": a[k]}
             for k in sorted(e.keys() & a.keys()) if e[k] != a[k]]
    nc_differences = []
    if scope == "all":
        for key in sorted(e.keys() & a.keys()):
            target = expected["nc"][key]
            if target is not None and target != actual["nc"][key]:
                nc_differences.append({"ref": key[0], "pin": key[1],
                                       "expected": target, "actual": actual["nc"][key]})
    passed = not (missing or extra or wrong or nc_differences or actual["physicalFindings"]
                  or physical_inventory_differences)
    return {"schema": "eda-netlist-comparison/v1", "status": "PASS" if passed else "FAIL",
            "passed": passed, "scope": scope, "nativeMutation": False,
            "expected": {"format": expected["format"], "counts": counts(expected)},
            "actual": {"format": actual["format"], "counts": counts(actual)},
            "comparedEndpoints": {"expected": len(e), "actual": len(a)},
            "missing": missing, "extra": extra, "wrongNet": wrong,
            "ncDifferences": nc_differences, "physicalPadAssignmentFindings": actual["physicalFindings"],
            "physicalPadInventoryDifferences": physical_inventory_differences,
            "physicalMultiplicityCompared": expected["physicalPads"] is not None,
            "actualNetAliases": actual["aliases"],
            "limitations": ["Compares supplied logical assignments; not native ERC/DRC or copper geometry.",
                            "Component identity, Channel ID, footprint/BOM and electrical function are not certified.",
                            "Netless pins are independent endpoints, never an implicit common net.",
                            "Connected scope deliberately excludes NC and netless endpoint completeness.",
                            "Without expected physicalPads, physical multiplicity is not independently verified.",
                            "Physical entity IDs are not compared between exports; geometry and identity are outside scope.",
                            "Physical pad assignment checks, when supplied, do not prove physical copper contact."]}


def file_proof(path):
    p = Path(path)
    return {"file": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}


def write_report(path, report, inputs):
    out = Path(path).resolve()
    for item in inputs:
        if item is not None:
            source = Path(item).resolve()
            if out == source or (out.exists() and source.exists() and out.samefile(source)):
                raise InputError("Report path aliases an input file; refusing to overwrite input")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected"); parser.add_argument("actual")
    parser.add_argument("--out", required=True)
    parser.add_argument("--scope", choices=["all", "connected"], default="all")
    parser.add_argument("--expected-index", type=int); parser.add_argument("--actual-index", type=int)
    parser.add_argument("--actual-net-aliases")
    parser.add_argument("--require-physical", action="store_true")
    args = parser.parse_args(argv)
    inputs = [args.expected, args.actual, args.actual_net_aliases]
    try:
        aliases = load_json(args.actual_net_aliases) if args.actual_net_aliases else None
        e = normalize(load_json(args.expected), args.expected_index)
        a = normalize(load_json(args.actual), args.actual_index, aliases)
        report = compare(e, a, args.scope, args.require_physical)
        report["inputs"] = {"expected": file_proof(args.expected), "actual": file_proof(args.actual)}
        if args.actual_net_aliases:
            report["inputs"]["aliases"] = file_proof(args.actual_net_aliases)
        report["selection"] = {"expectedIndex": args.expected_index, "actualIndex": args.actual_index}
        write_report(args.out, report, inputs)
        print(json.dumps({"status": report["status"], "scope": args.scope,
                          "missing": len(report["missing"]), "extra": len(report["extra"]),
                          "wrongNet": len(report["wrongNet"]), "ncDifferences": len(report["ncDifferences"]),
                          "physicalPadFindings": len(report["physicalPadAssignmentFindings"]),
                          "physicalInventoryDifferences": len(report["physicalPadInventoryDifferences"]),
                          "report": str(Path(args.out).resolve())}, ensure_ascii=False))
        return 0 if report["passed"] else 1
    except (InputError, OSError, UnicodeError) as exc:
        error = {"schema": "eda-check-error/v1", "status": "INVALID_INPUT", "passed": False, "error": str(exc)}
        try:
            write_report(args.out, error, inputs)
        except (InputError, OSError):
            pass
        print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
