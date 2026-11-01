#!/usr/bin/env python3
"""Audit exported two-layer Gerber copper against independent physical pads.

Deliberately rejects unsupported rendering instead of inventing electrical links.
Coordinates are mm in the Gerber frame; see references/copper-tool.md.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_manufacturing import parse_gerber, parse_excellon

SCHEMA = 'easyeda-copper-report/v1'


class Unsupported(ValueError):
    pass


def geometry_dependency():
    try:
        import shapely
        if int(shapely.__version__.split('.')[0]) != 2:
            raise ImportError('requires Shapely 2.x')
    except ImportError as exc:
        raise Unsupported('Shapely >=2,<3 required') from exc


def number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Unsupported('Expected finite numeric geometry')
    if positive and value <= 0:
        raise Unsupported('Geometry dimensions must be positive')
    return float(value)


def pieces(g):
    if g.is_empty:
        return []
    if g.geom_type == 'Polygon':
        return [g]
    return [p for child in getattr(g, 'geoms', []) for p in pieces(child)]


def pad_shape(p):
    from shapely.geometry import Point, LineString, box
    from shapely.affinity import rotate, translate
    x, y, w, h = [number(p[k], k in ('width', 'height')) for k in ('x', 'y', 'width', 'height')]
    angle = number(p.get('rotation', 0))
    if p['shape'] == 'rect':
        g = box(-w / 2, -h / 2, w / 2, h / 2)
    elif p['shape'] == 'circle' and abs(w - h) < 1e-9:
        g = Point(0, 0).buffer(w / 2, quad_segs=128)
    elif p['shape'] == 'oval':
        if abs(w - h) < 1e-9:
            g = Point(0, 0).buffer(w / 2, quad_segs=128)
        else:
            a = ((w - h) / 2, 0) if w > h else (0, (h - w) / 2)
            g = LineString([(-a[0], -a[1]), a]).buffer(min(w, h) / 2, quad_segs=128)
    else:
        raise Unsupported('Unsupported pad shape: ' + str(p['shape']))
    return translate(rotate(g, angle, origin=(0, 0)), x, y)


def aperture(ap, pt, unit):
    vals = [float(v) * unit for v in ap['parameters'].split('X')]
    shape = ap['shape']
    expected = 1 if shape == 'C' else 2
    if shape not in ('C', 'R', 'O') or len(vals) != expected:
        raise Unsupported('Only solid C/R/O apertures without holes are rendered')
    return pad_shape({'x': pt[0], 'y': pt[1], 'width': vals[0],
                      'height': vals[0] if shape == 'C' else vals[1],
                      'shape': {'C': 'circle', 'R': 'rect', 'O': 'oval'}[shape]})


def render_gerber(text):
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union
    r = parse_gerber(text)
    if r['unknown'] or r['macros']:
        raise Unsupported('Incomplete Gerber rendering: ' + str(r['unknown'] or 'aperture macros'))
    if any(q['polarity'] != 'dark' for q in r['paths'] + r['flashRecords']):
        raise Unsupported('Mixed/clear polarity requires an ordered compositor')
    unit = 25.4 if r['unit'] == 'inch' else 1
    shapes, regions = [], defaultdict(list)
    for path in r['paths']:
        # Existing parser approximates arcs for outline checks. Connectivity at
        # a tangency needs a different error model, so do not silently reuse it.
        if path['mode'] != 1:
            raise Unsupported('Curved copper interpolation not supported by connectivity audit')
        if path['region']:
            regions[path['regionIndex']].append(path['points'])
            continue
        ap = r['apertures'][path['aperture']]
        if ap['shape'] == 'C' and len(ap['parameters'].split('X')) == 1:
            width = number(float(ap['parameters']) * unit, True)
            shapes.append(LineString(path['points']).buffer(width / 2, quad_segs=128))
        else:
            for a, b in zip(path['points'], path['points'][1:]):
                shapes.append(unary_union([aperture(ap, a, unit), aperture(ap, b, unit)]).convex_hull)
    for segments in regions.values():
        contours, current = [], []
        for segment in segments:
            if current and math.dist(current[-1], segment[0]) > 1e-7:
                contours.append(current)
                current = []
            current.extend(segment if not current else segment[1:])
        if current:
            contours.append(current)
        g = Polygon()
        for ring in contours:
            poly = Polygon(ring)
            if math.dist(ring[0], ring[-1]) > 1e-7 or not poly.is_valid or not poly.area:
                raise Unsupported('Open, empty or invalid copper region')
            g = g.symmetric_difference(poly)
        shapes.append(g)
    for flash in r['flashRecords']:
        shapes.append(aperture(r['apertures'][flash['aperture']], flash['point'], unit))
    return unary_union(shapes), r


def hole_geometry(feature):
    from shapely.geometry import Point, LineString
    radius = number(feature['diameterMm'], True) / 2
    if feature['b'] is None:
        return Point(feature['a']).buffer(radius, quad_segs=128)
    return LineString([feature['a'], feature['b']]).buffer(radius, quad_segs=128)


def audit(archive, expected):
    geometry_dependency()
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from shapely.strtree import STRtree
    if expected.get('schema') != 'easyeda-copper-expected/v1' or expected.get('unit') != 'mm' or expected.get('frame') != 'gerber':
        raise Unsupported('Expected easyeda-copper-expected/v1, unit mm, frame gerber')
    pads = expected['pads']
    if not pads or not any(p.get('net') for p in pads):
        raise Unsupported('Nonempty physical pads and named nets required')
    if expected.get('expectedPadCount', len(pads)) != len(pads):
        raise Unsupported('Physical pad count differs from independent count')
    ids = [p['id'] for p in pads]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise Unsupported('Unique nonempty physical pad ids required; ref.pin alone may repeat')
    for p in pads:
        if p['net'] is not None and (not isinstance(p['net'], str) or not p['net'].strip()):
            raise Unsupported('Use a nonempty net name or explicit null for NC')
        if not p['layers'] or any(type(l) is not int or l not in (1, 2) for l in p['layers']) or len(set(p['layers'])) != len(p['layers']):
            raise Unsupported('Pad layers must be a nonempty unique subset of [1,2]')
    coverage_min = number(expected.get('minPadCoverage', 0.98), True)
    if coverage_min > 1:
        raise Unsupported('minPadCoverage must be <=1')
    names = expected['files']
    if set(names) != {'top', 'bottom', 'pth', 'npth'} or not isinstance(names['pth'], list) or not isinstance(names['npth'], list):
        raise Unsupported('files requires top,bottom,pth[],npth[]; explicitly classify all drills')
    requested = [names['top'], names['bottom'], *names['pth'], *names['npth']]
    if len(set(requested)) != len(requested):
        raise Unsupported('Duplicate manufacturing member binding')
    with zipfile.ZipFile(archive) as z:
        if len(set(z.namelist())) != len(z.namelist()):
            raise Unsupported('Duplicate ZIP member names')
        if any(n not in requested for n in z.namelist() if n.lower().endswith(('.drl', '.xln'))):
            raise Unsupported('Unclassified drill member in ZIP')
        data = {n: z.read(n).decode('utf-8-sig') for n in requested}
        # X2-declared extra copper must not silently become an ignored inner layer.
        for n in z.namelist():
            if n not in requested and (b'TF.FileFunction,Copper' in re.sub(rb'\s+', b'', z.read(n))
                                       or re.search(r'\.(?:gtl|gbl|g\d+)$', n, re.I)):
                raise Unsupported('Extra declared/recognized copper layer; only two-layer through-hole boards supported')
    raw, layer_stats = {}, {}
    for layer, name in [(1, names['top']), (2, names['bottom'])]:
        raw[layer], parsed = render_gerber(data[name])
        fn = parsed['fileFunction']
        if fn and not fn.startswith('Copper,L' + str(layer) + ','):
            raise Unsupported('Copper file layer declaration disagrees with explicit binding')
        layer_stats[layer] = {'areaMm2BeforeDrill': raw[layer].area, 'paths': len(parsed['paths']), 'flashes': parsed['flashes']}
    pth, npth = [], []
    for key, destination in [('pth', pth), ('npth', npth)]:
        for name in names[key]:
            drill = parse_excellon(data[name])
            if drill['unknown']:
                raise Unsupported('Incomplete drill parsing: ' + str(drill['unknown']))
            plating = drill['plating']
            if plating and plating != ('PLATED' if key == 'pth' else 'NON_PLATED'):
                raise Unsupported('Drill plating disagrees with explicit binding: ' + str(plating))
            destination.extend(hole_geometry(f) for f in drill['features'])
    raw_drill_counts = {'pth': len(pth), 'npth': len(npth)}
    # Exporters can repeat all vias in both the main PTH file and a via subset.
    # Deduplicate identical physical geometry, never infer plating from a name.
    pth = list({g.wkb: g for g in pth}.values())
    npth = list({g.wkb: g for g in npth}.values())
    if any(p.intersection(n).area > 1e-10 for p in pth for n in npth):
        raise Unsupported('Overlapping PTH and NPTH classifications')
    removed = unary_union(pth + npth)
    copper = {l: pieces(g.difference(removed)) for l, g in raw.items()}
    trees = {l: STRtree(gs) for l, gs in copper.items()}
    parent = {(l, i): (l, i) for l, gs in copper.items() for i in range(len(gs))}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    def join(a, b):
        parent[find(a)] = find(b)

    # Point contacts in one face count as conductive, though they are not
    # acceptable current paths. Width/current checks remain separate.
    for l, gs in copper.items():
        for i, g in enumerate(gs):
            for j in trees[l].query(g, predicate='intersects'):
                join((l, i), (l, int(j)))
    for hole in pth:  # NPTH never contributes a cross-layer edge.
        hit = [(l, int(i)) for l in (1, 2) for i in trees[l].query(hole.buffer(1e-7))
               if copper[l][i].boundary.distance(hole.boundary) <= 1e-7]
        for n in hit[1:]:
            join(hit[0], n)
    pad_roots, coverage, missing, node_pads = {}, [], [], defaultdict(list)
    for p in pads:
        g = pad_shape(p).difference(removed)
        if g.area <= 1e-10:
            raise Unsupported('Expected pad has no copper remaining after drill: ' + p['id'])
        roots = set()
        for l in p['layers']:
            hits = [int(i) for i in trees[l].query(g, predicate='intersects') if copper[l][i].intersection(g).area > 1e-10]
            covered = unary_union([copper[l][i] for i in hits]).intersection(g).area / g.area
            coverage.append({'id': p['id'], 'layer': l, 'ratio': covered})
            if covered + 1e-6 < coverage_min:
                missing.append({'id': p['id'], 'layer': l, 'coverage': covered})
            roots.update(find((l, i)) for i in hits)
        pad_roots[p['id']] = roots
        for root in roots:
            node_pads[root].append(p)
    shorts, nc_contacts = [], []
    for root, connected_pads in node_pads.items():
        nets = sorted({p['net'] for p in connected_pads if p['net'] is not None})
        if len(nets) > 1:
            shorts.append({'nets': nets, 'pads': [p['id'] for p in connected_pads]})
        nc = [p['id'] for p in connected_pads if p['net'] is None]
        if nc and len(connected_pads) > 1:
            nc_contacts.append({'ncPads': nc, 'contactedPads': [p['id'] for p in connected_pads]})
    nets = {}
    for net in sorted({p['net'] for p in pads if p['net'] is not None}):
        required = [p['id'] for p in pads if p['net'] == net]
        groups = defaultdict(list)
        for ident in required:
            for root in pad_roots[ident]:
                groups[root].append(ident)
        nets[net] = {'connected': len(groups) == 1 and all(pad_roots[i] for i in required), 'groups': list(groups.values())}
    islands = [{'layer': l, 'areaMm2': g.area, 'boundsMm': list(g.bounds)} for l, gs in copper.items()
               for i, g in enumerate(gs) if find((l, i)) not in node_pads]
    passed = not missing and not shorts and not nc_contacts and all(n['connected'] for n in nets.values())
    if expected.get('rejectUnassignedIslands', False) and islands:
        passed = False
    return {'schema': SCHEMA, 'status': 'PASS' if passed else 'FAIL', 'passed': passed,
            'coverageScope': 'two-layer-solid-linear-copper',
            'scope': 'Two-layer solid C/R/O and linear-region Gerber copper with actual PTH/NPTH; independent physical-pad contract. No net-name shortcut.',
            'limits': ['No curved copper, macros, clear polarity, blind/buried vias or multilayer stack.', 'No current/thermal/EMC proof; no native DRC or round-trip claim.', 'Unassigned copper is reported; it has no independently established net.'],
            'padCount': len(pads), 'physicalPTHFeatures': len(pth), 'physicalNPTHFeatures': len(npth), 'rawDrillRecords': raw_drill_counts,
            'minPadCoverage': coverage_min, 'missingOrDamagedPads': missing, 'padCoverage': coverage,
            'copperShorts': shorts, 'ncContacts': nc_contacts, 'netConnectivity': nets,
            'unassignedIslands': islands, 'layers': layer_stats}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('archive', type=Path)
    ap.add_argument('--expected', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    args = ap.parse_args()
    inputs = [args.archive, args.expected]
    try:
        output = args.out.resolve()
        if output in [p.resolve() for p in inputs] or (output.exists() and output.stat().st_nlink > 1) or args.out.is_symlink():
            raise Unsupported('Report must not overwrite/alias inputs')
        if output.exists() and json.loads(output.read_text('utf-8')).get('schema') != SCHEMA:
            raise Unsupported('Refusing to overwrite unrelated file')
        hashes = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
        result = audit(args.archive, json.loads(args.expected.read_text('utf-8-sig')))
        if any(hashlib.sha256(p.read_bytes()).hexdigest() != hashes[str(p.resolve())] for p in inputs):
            raise Unsupported('Input changed during audit')
        result['inputs'] = {role: {'file': str(p.resolve()), 'sha256': hashes[str(p.resolve())]}
                            for role, p in [('export', args.archive), ('expected', args.expected)]}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
        print(json.dumps({'status': result['status'], 'pads': result['padCount'], 'shorts': len(result['copperShorts']),
                          'damagedPads': len(result['missingOrDamagedPads']), 'disconnectedNets': [n for n, r in result['netConnectivity'].items() if not r['connected']]}, ensure_ascii=False))
        return 0 if result['passed'] else 1
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
