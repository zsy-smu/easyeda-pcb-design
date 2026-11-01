#!/usr/bin/env python3
"""Generate offline plane polygons, preserving voids; never writes a live PCB.

Input is a complete, already-normalized geometry snapshot in mm. Output is a
candidate, not native/export validation. See references/ground-tool.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_copper_connectivity import Unsupported, geometry_dependency, number, pad_shape, pieces

SCHEMA = 'easyeda-ground-candidate/v1'


def polygon(points):
    from shapely.geometry import Polygon
    if not isinstance(points, list) or len(points) < 3:
        raise Unsupported('Polygon needs at least three points')
    g = Polygon([[number(v) for v in p] for p in points])
    if not g.is_valid or not g.area:
        raise Unsupported('Invalid or empty polygon; do not silently repair input')
    return g


def shape(obj):
    from shapely.geometry import Point, LineString
    kind = obj['kind']
    if kind == 'pad':
        return pad_shape(obj)
    if kind == 'polygon':
        return polygon(obj['points'])
    if kind == 'circle':
        return Point(number(obj['x']), number(obj['y'])).buffer(number(obj['diameter'], True) / 2, quad_segs=64)
    if kind == 'track':
        pts = [[number(v) for v in p] for p in obj['points']]
        if len(pts) < 2 or any(len(p) != 2 for p in pts):
            raise Unsupported('Track needs at least two XY points')
        return LineString(pts).buffer(number(obj['width'], True) / 2, quad_segs=64)
    raise Unsupported('Unsupported normalized shape: ' + str(kind))


def holefree(p, depth=0):
    """Partition, never fill a hole. Shared cut edges retain the same region."""
    from shapely.geometry import Polygon, LineString
    from shapely.ops import split
    if not p.interiors:
        return [p]
    if depth >= 200:
        raise Unsupported('Hole partition budget exceeded')
    xs = sorted(Polygon(r).representative_point().x for r in p.interiors)
    x = xs[len(xs) // 2]
    left, bottom, right, top = p.bounds
    children = pieces(split(p, LineString([(x, bottom - 1), (x, top + 1)])))
    if len(children) < 2 or max(len(c.interiors) for c in children) >= len(p.interiors):
        raise Unsupported('Hole partition made no progress')
    return [q for c in children for q in holefree(c, depth + 1)]


def build(spec):
    geometry_dependency()
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    if spec.get('schema') != 'easyeda-ground-input/v1' or spec.get('unit') != 'mm':
        raise Unsupported('Expected easyeda-ground-input/v1 and mm')
    if not spec.get('sourceRevision') or not spec.get('net'):
        raise Unsupported('Explicit sourceRevision and target net required')
    net = spec['net']
    board = polygon(spec['board'])
    edge = number(spec['edgeClearance'])
    bridge = number(spec['minBridgeWidth'])
    minarea = number(spec['minIslandArea'])
    if min(edge, bridge, minarea) < 0:
        raise Unsupported('Plane clearances/width/area cannot be negative')
    clearances = spec['clearanceByNet']
    if not isinstance(clearances, dict):
        raise Unsupported('Explicit clearanceByNet object required, including NC')
    for value in clearances.values():
        number(value, True)
    objects = spec['copper']
    ids = [o['id'] for o in objects]
    if len(set(ids)) != len(ids):
        raise Unsupported('Duplicate copper ids')
    for o in objects:
        if not o['layers'] or any(type(l) is not int or l not in (1, 2) for l in o['layers']):
            raise Unsupported('Only two copper layers supported')
        if o['net'] != net and (o['net'] if o['net'] is not None else 'NC') not in clearances:
            raise Unsupported('Missing clearance for foreign net: ' + str(o['net']))
    anchored = set(spec['anchorPadIds'])
    byid = {o['id']: o for o in objects}
    if not anchored or any(i not in byid or byid[i]['kind'] != 'pad' or byid[i]['net'] != net for i in anchored):
        raise Unsupported('Anchor ids must identify actual target-net pads, not floating vias or tracks')
    parsed = {i: shape(o) for i, o in byid.items()}
    allholes = []
    holes = spec['holes']
    for h in holes:
        if type(h['plated']) is not bool:
            raise Unsupported('Explicit plated bool required')
        if number(h['clearance']) < 0:
            raise Unsupported('Hole clearance cannot be negative')
        if 'net' not in h or (h['net'] is not None and (not isinstance(h['net'], str) or not h['net'])):
            raise Unsupported('Hole net must be an explicit name or null for NC')
        allholes.append(shape(h))
    removed = unary_union(allholes)
    keepouts = [shape(o).buffer(number(o['clearance'])) for o in spec['keepouts']]
    if any(number(o['clearance']) < 0 for o in spec['keepouts']):
        raise Unsupported('Keepout clearance cannot be negative')
    candidate, retained, discarded = {}, {}, []
    for layer in (1, 2):
        obs = [g.buffer(clearances[o['net'] if o['net'] is not None else 'NC'], quad_segs=64)
               for i, o in byid.items() if layer in o['layers'] and o['net'] != net for g in [parsed[i]]]
        for h, g in zip(holes, allholes):
            if h['plated'] and h['net'] != net:
                key = h['net'] if h['net'] is not None else 'NC'
                if key not in clearances:
                    raise Unsupported('Missing foreign plated hole clearance: ' + str(key))
                obs.append(g.buffer(clearances[key], quad_segs=64))
            else:
                obs.append(g.buffer(number(h['clearance'])))
        allowed = board.buffer(-edge).difference(unary_union(obs + keepouts))
        # Opening suppresses candidate slivers, not a guaranteed minimum-neck
        # theorem. Final width and actual return-path checks remain necessary.
        if bridge:
            allowed = allowed.buffer(-bridge / 2, quad_segs=32).buffer(bridge / 2, quad_segs=32).intersection(allowed)
        candidate[layer] = []
        for g in pieces(allowed):
            if g.area >= minarea:
                candidate[layer].append(g)
            else:
                discarded.append({'layer': layer, 'areaMm2': g.area, 'reason': 'small'})
    # Build a graph of candidate regions and existing SAME-NET copper. Seed it
    # only from specified pads, and bridge layers only at explicit target PTH.
    nodes, geometry, layers = [], {}, {}
    for layer, gs in candidate.items():
        for i, g in enumerate(gs):
            n = ('plane', layer, i)
            nodes.append(n); geometry[n] = g; layers[n] = {layer}
    for ident, o in byid.items():
        if o['net'] == net:
            for layer in o['layers']:
                for i, g in enumerate(pieces(parsed[ident].difference(removed))):
                    n = ('copper', layer, ident, i)
                    nodes.append(n); geometry[n] = g; layers[n] = {layer}
    parent = {n: n for n in nodes}

    def root(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]; n = parent[n]
        return n

    def join(a, b):
        parent[root(a)] = root(b)

    for k, n in enumerate(nodes):
        for other in nodes[k + 1:]:
            if layers[n] & layers[other] and geometry[n].intersects(geometry[other]):
                join(n, other)
    for h, g in zip(holes, allholes):
        if h['plated'] and h['net'] == net:
            hits = [n for n in nodes if geometry[n].boundary.distance(g.boundary) <= 1e-7]
            for n in hits[1:]:
                join(hits[0], n)
    seeds = {root(n) for n in nodes if n[0] == 'copper' and n[2] in anchored}
    rows = []
    for layer, gs in candidate.items():
        selected = []
        for i, g in enumerate(gs):
            n = ('plane', layer, i)
            if g.area >= minarea and root(n) in seeds:
                selected.append(g)
            else:
                discarded.append({'layer': layer, 'areaMm2': g.area, 'reason': 'small' if g.area < minarea else 'not_connected_to_anchor_pad'})
        retained[layer] = unary_union(selected)
        parts = [p for g in selected for p in holefree(g)]
        error = retained[layer].symmetric_difference(unary_union(parts)).area
        if error > 1e-7:
            raise Unsupported('Hole partition changed copper area')
        rows.extend({'layer': layer, 'net': net, 'points': [list(pt) for pt in p.exterior.coords]} for p in parts)
    return {'schema': SCHEMA, 'status': 'CANDIDATE', 'sourceRevision': spec['sourceRevision'], 'unit': 'mm',
            'net': net, 'anchorPadIds': sorted(anchored), 'fills': rows, 'discarded': discarded,
            'areaMm2ByLayer': {str(l): g.area for l, g in retained.items()},
            'limits': ['Candidate only; no live edit, native DRC, manufacturing or current/EMC claim.',
                       'Requires complete current geometry. Anchor connectivity uses supplied nominal copper; actual export must be audited.',
                       'Opening reduces slivers but does not certify every neck or reference return path.',
                       'Solid fill polygons require regeneration after copper changes. Preserve holes when rendering or writing.']}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', type=Path)
    ap.add_argument('--out', required=True, type=Path)
    a = ap.parse_args()
    try:
        if a.out.resolve() == a.input.resolve() or a.out.is_symlink() or (a.out.exists() and a.out.stat().st_nlink > 1):
            raise Unsupported('Output must not overwrite/alias input')
        if a.out.exists() and json.loads(a.out.read_text('utf-8')).get('schema') != SCHEMA:
            raise Unsupported('Refusing to overwrite unrelated file')
        data = a.input.read_bytes()
        result = build(json.loads(data.decode('utf-8-sig')))
        result['inputs'] = {str(a.input.resolve()): hashlib.sha256(data).hexdigest()}
        if a.input.read_bytes() != data:
            raise Unsupported('Input changed during computation')
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
        print(json.dumps({'status': 'CANDIDATE', 'fills': len(result['fills']), 'areaMm2ByLayer': result['areaMm2ByLayer']}))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
