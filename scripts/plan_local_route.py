#!/usr/bin/env python3
"""Bounded fixed-width two-layer A* candidate for ONE specified connection.

No electrical guesses, width fallback, live edits or claim of board completion.
Use a complete normalized ground-input geometry plus the route contract below.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import itertools
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_ground_plane import shape, polygon
from check_copper_connectivity import geometry_dependency, number, Unsupported

SCHEMA = 'easyeda-local-route-candidate/v1'


def plan(spec):
    geometry_dependency()
    from shapely.geometry import Point, LineString
    from shapely.ops import unary_union
    if spec.get('schema') != 'easyeda-local-route-input/v1' or spec.get('unit') != 'mm':
        raise Unsupported('Expected easyeda-local-route-input/v1 and mm')
    if not spec.get('sourceRevision') or not spec.get('net'):
        raise Unsupported('Explicit sourceRevision and net required')
    net, width = spec['net'], number(spec['width'], True)
    grid, edge = number(spec['grid'], True), number(spec['edgeClearance'])
    via_d, via_h = number(spec['viaDiameter'], True), number(spec['viaHole'], True)
    drill_pad_clearance = number(spec['viaDrillToSmdClearance'])
    drill_hole_clearance = number(spec['viaHoleToHoleClearance'])
    via_cost = number(spec['viaCost'], True)
    budget = spec['maxExpanded']
    if min(edge, drill_pad_clearance, drill_hole_clearance) < 0 or via_h >= via_d or type(budget) is not int or budget < 1:
        raise Unsupported('Invalid edge/via/budget')
    board = polygon(spec['board'])
    allowed = board.buffer(-edge)
    clearances = spec['clearanceByNet']
    for v in clearances.values():
        number(v, True)
    copper = spec['copper']
    byid = {o['id']: o for o in copper}
    if len(byid) != len(copper):
        raise Unsupported('Duplicate copper ids')
    parsed = {o['id']: shape(o) for o in copper}
    for o in copper:
        if not o['layers'] or any(type(l) is not int or l not in (1, 2) for l in o['layers']):
            raise Unsupported('Copper layer must be 1 or 2')
    terminals = []
    for ident in (spec['fromPadId'], spec['toPadId']):
        o = byid[ident]
        if o['kind'] != 'pad' or o['net'] != net:
            raise Unsupported('Terminals must be actual pads on specified net')
        terminals.append(o)
    start, goal = [(number(o['x']), number(o['y'])) for o in terminals]
    if start == goal:
        raise Unsupported('Coincident terminal centers require pad/barrel analysis, not a planar route')
    foreign = {1: [], 2: []}
    for o in copper:
        if o['net'] == net:
            continue
        key = o['net'] if o['net'] is not None else 'NC'
        if key not in clearances:
            raise Unsupported('Missing clearance for foreign net: ' + str(key))
        g = parsed[o['id']].buffer(clearances[key], quad_segs=32)
        for l in o['layers']:
            foreign[l].append(g)
    keepouts = []
    for o in spec['keepouts']:
        clearance = number(o['clearance'])
        if clearance < 0:
            raise Unsupported('Negative mechanical clearance')
        keepouts.append(shape(o).buffer(clearance))
    # Holes/slots are routing obstacles on BOTH faces even when a library
    # adapter forgot their annular pads. Own-net PTH is not a foreign obstacle.
    existing_holes = [shape(h) for h in spec['holes']]
    for h, hg in zip(spec['holes'], existing_holes):
        if type(h['plated']) is not bool:
            raise Unsupported('Explicit hole plating required')
        if h['plated'] and h['net'] == net:
            continue
        key = h['net'] if h['net'] is not None else 'NC'
        clearance = number(h['clearance']) if not h['plated'] else number(clearances[key], True)
        if clearance < 0:
            raise Unsupported('Negative hole clearance')
        keepouts.append(hg.buffer(clearance))
    obstacles = {l: unary_union(foreign[l] + keepouts) for l in (1, 2)}

    def safe_segment(a, b, layer):
        g = LineString([a, b]).buffer(width / 2, quad_segs=32) if a != b else Point(a).buffer(width / 2, quad_segs=32)
        return allowed.covers(g) and (obstacles[layer].is_empty or g.distance(obstacles[layer]) > 1e-8)

    def safe_via(pt):
        # Require the actual ring to fit; conservative disk collision also
        # protects the drill from adjacent SMD copper.
        g = Point(pt).buffer(via_d / 2, quad_segs=64)
        drill = Point(pt).buffer(via_h / 2, quad_segs=64)
        if any(drill.distance(parsed[o['id']]) <= drill_pad_clearance + 1e-8
               for o in copper if o['kind'] == 'pad' and len(o['layers']) == 1):
            return False
        if any(drill.distance(h) <= drill_hole_clearance + 1e-8 for h in existing_holes):
            return False
        return allowed.covers(g) and all(obstacles[l].is_empty or g.distance(obstacles[l]) > 1e-8 for l in (1, 2))

    left, bottom, right, top = board.bounds
    nx, ny = math.floor((right - left) / grid), math.floor((top - bottom) / grid)
    if (nx + 1) * (ny + 1) * 2 > spec.get('maxGridNodes', 2000000):
        raise Unsupported('Grid exceeds explicit/default 2M node cap; refine region or grid')

    def xy(n):
        return (left + n[0] * grid, bottom + n[1] * grid)

    def near(pt, layers):
        ix, iy = round((pt[0] - left) / grid), round((pt[1] - bottom) / grid)
        return [(i, j, l) for l in layers for i in range(max(0, ix - 1), min(nx, ix + 1) + 1)
                for j in range(max(0, iy - 1), min(ny, iy + 1) + 1)]

    starts = [n for n in near(start, terminals[0]['layers']) if safe_segment(start, xy(n), n[2])]
    ends = {n for n in near(goal, terminals[1]['layers']) if safe_segment(xy(n), goal, n[2])}
    if not starts or not ends:
        return result(spec, 'BLOCKED', [], [], 0, 'terminal_exit_blocked')
    count = itertools.count()
    heap, costs, parent = [], {}, {}
    for n in starts:
        cost = math.dist(start, xy(n))
        costs[n] = cost; parent[n] = None
        heapq.heappush(heap, (cost + math.dist(xy(n), goal), next(count), cost, n))
    expanded, found = 0, None
    while heap and expanded < budget:
        _, _, cost, n = heapq.heappop(heap)
        if cost != costs[n]:
            continue
        expanded += 1
        if n in ends:
            found = n
            break
        neighbors = [(n[0] + dx, n[1] + dy, n[2]) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))]
        if safe_via(xy(n)):
            neighbors.append((n[0], n[1], 3 - n[2]))
        for q in neighbors:
            if not (0 <= q[0] <= nx and 0 <= q[1] <= ny):
                continue
            switch = q[2] != n[2]
            if not switch and not safe_segment(xy(n), xy(q), n[2]):
                continue
            newcost = cost + (via_cost if switch else math.dist(xy(n), xy(q)))
            if newcost + 1e-10 >= costs.get(q, math.inf):
                continue
            costs[q] = newcost; parent[q] = n
            heapq.heappush(heap, (newcost + math.dist(xy(q), goal), next(count), newcost, q))
    if found is None:
        return result(spec, 'BLOCKED', [], [], expanded, 'budget_exhausted' if heap else 'no_path_at_fixed_width_and_grid')
    path = []
    n = found
    while n is not None:
        path.append(n); n = parent[n]
    path.reverse()
    tracks, vias = [], []
    pts, layer = [list(start)], path[0][2]
    for n in path:
        pt = list(xy(n))
        if n[2] != layer:
            if len(pts) >= 2:
                tracks.append({'net': net, 'layer': layer, 'width': width, 'points': pts})
            vias.append({'net': net, 'x': pt[0], 'y': pt[1], 'diameter': via_d, 'hole': via_h})
            pts, layer = [pt], n[2]
        elif pt != pts[-1]:
            pts.append(pt)
    if list(goal) != pts[-1]:
        pts.append(list(goal))
    if len(pts) >= 2:
        tracks.append({'net': net, 'layer': layer, 'width': width, 'points': pts})
    for i, a in enumerate(vias):
        for j, b in enumerate(vias[i + 1:], i + 1):
            gap = math.dist((a['x'], a['y']), (b['x'], b['y'])) - (a['hole'] + b['hole']) / 2
            if gap <= drill_hole_clearance + 1e-8:
                blocked = result(spec, 'BLOCKED', [], [], expanded, 'via_pair_clearance')
                blocked['conflict'] = {'viaIndices': [i, j], 'centers': [[a['x'], a['y']], [b['x'], b['y']]],
                                       'holeEdgeGap': gap, 'requiredGap': drill_hole_clearance}
                return blocked
    # Recheck complete exported candidate, not just the graph nodes.
    if any(not safe_segment(tuple(a), tuple(b), t['layer']) for t in tracks for a, b in zip(t['points'], t['points'][1:])) or any(not safe_via((v['x'], v['y'])) for v in vias):
        raise Unsupported('Candidate failed final geometric recheck')
    return result(spec, 'CANDIDATE', tracks, vias, expanded, None)


def result(spec, status, tracks, vias, expanded, reason):
    return {'schema': SCHEMA, 'status': status, 'sourceRevision': spec['sourceRevision'], 'unit': 'mm',
            'net': spec['net'], 'fromPadId': spec['fromPadId'], 'toPadId': spec['toPadId'],
            'requestedWidth': spec['width'], 'tracks': tracks, 'vias': vias, 'expanded': expanded, 'reason': reason,
            'widthFallbackUsed': False, 'limits': ['One fixed-width connection only; no layout, loop, impedance or current approval.',
             'Complete current geometry required; existing same-net copper is allowed but not implicitly considered connected.',
             'No via-in-SMD-pad or automatic existing-PTH reuse; new drills avoid all supplied holes.',
             'Grid failure does not prove no physical route exists. No automatic deletion, ripping or live write.',
             'New-via pair clearance is checked after search; a conflict returns BLOCKED rather than searching every path history.',
             'Write back only against sourceRevision, then rebuild planes and audit actual copper plus native DRC.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    try:
        if a.out.resolve() == a.input.resolve() or a.out.is_symlink() or (a.out.exists() and a.out.stat().st_nlink > 1):
            raise Unsupported('Output must not overwrite/alias input')
        if a.out.exists() and json.loads(a.out.read_text('utf-8')).get('schema') != SCHEMA:
            raise Unsupported('Refusing to overwrite unrelated file')
        data = a.input.read_bytes()
        r = plan(json.loads(data.decode('utf-8-sig')))
        if data != a.input.read_bytes():
            raise Unsupported('Input changed during routing')
        r['inputs'] = {str(a.input.resolve()): hashlib.sha256(data).hexdigest()}
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(r, ensure_ascii=False, indent=2), 'utf-8')
        print(json.dumps({'status': r['status'], 'tracks': len(r['tracks']), 'vias': len(r['vias']), 'reason': r['reason']}))
        return 0 if r['status'] == 'CANDIDATE' else 1
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
