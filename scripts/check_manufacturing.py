"""Read-only Gerber/Excellon geometry checks against a declared expected manifest.
Python 3.10+. Shapely >=2,<3 is required only when running geometry checks.
No EDA calls, extraction, installs, or implicit manufacturing qualification.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import zipfile


class InputError(ValueError):
    """The requested check cannot be completed for this input."""


def require_geometry():
    try:
        import shapely
        from shapely.geometry import LineString, Polygon
        from shapely.ops import unary_union
    except ImportError as exc:
        raise InputError('Dependency missing: Shapely >=2,<3 must be available in this Python environment; no installation was attempted.') from exc
    if not re.match(r'^2\.', shapely.__version__):
        raise InputError('Unsupported Shapely version: require >=2,<3; found ' + shapely.__version__)
    return LineString, Polygon, unary_union

NUM = '[+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)'

def sha(b):
    return hashlib.sha256(b).hexdigest()

def xy_fields(text):
    return {k: v for (k, v) in re.findall('([XYIJ])(' + NUM + ')', text)}

def coordinate(s, unit, digits, zero='L'):
    if '.' in s:
        value = float(s) * unit
        if not math.isfinite(value):
            raise ValueError('Nonfinite coordinate')
        return value
    if digits is None:
        raise ValueError('Implicit coordinate without declared format')
    sign = -1 if s.startswith('-') else 1
    s = s.lstrip('+-')
    (integer, decimal) = digits
    if zero == 'T':
        s = s.ljust(integer + decimal, '0')
    return sign * int(s) * 10 ** (-decimal) * unit

def parse_gerber(text):
    r = {'format': None, 'unit': None, 'fileFunction': None, 'paths': [], 'flashes': 0, 'flashRecords': [], 'regions': 0, 'unknown': [], 'apertures': {}, 'macros': {}, 'ended': False}
    unit = None
    fmt = None
    zero = 'L'
    absolute = True
    pos = None
    mode = 1
    op = 2
    ap = None
    quadrant = 'multi'
    region = False
    polarity = 'dark'

    def bad(s):
        r['unknown'].append(s[:160])
    if text.count('%') % 2:
        bad('Unbalanced Gerber extended-command delimiters')
    for block in re.findall('%[^%]*%|[^%]+', text, re.S):
        extended = block.startswith('%')
        commands = block.strip('%').split('*')
        if block.strip('%').strip() and not block.strip('%').rstrip().endswith('*'):
            bad('Missing Gerber command terminator')
        if extended and commands[0].strip().startswith('AM'):
            macro_name = commands[0].strip()[2:]
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*', macro_name) or macro_name in r['macros']:
                bad('Invalid or duplicate aperture macro name')
            if r['ended']:
                bad('Aperture macro after M02')
            macro = [v.strip() for v in commands[1:] if v.strip()]
            for primitive in macro:
                if primitive.startswith('0 '):
                    continue
                # Macro expressions are syntax checked and retained for inventory;
                # this checker never renders aperture macro copper geometry.
                compact = re.sub(r'\s+', '', primitive)
                if re.fullmatch(r'\$\d+=[\d.$+*/xX()\-]+', compact):
                    continue
                parts = compact.split(',')
                if parts[0] not in ('1', '2', '4', '5', '6', '7', '20', '21', '22') or len(parts) < 2 or any(not re.fullmatch(r'[\d.$+*/xX()\-]+', part) for part in parts[1:]):
                    bad('Unsupported aperture macro primitive: ' + primitive)
            r['macros'][macro_name] = macro
            continue
        for raw in commands:
            s = raw.strip()
            if not s:
                continue
            if s.startswith('G04'):
                continue
            if r['ended']:
                bad('Command after M02')
                continue
            s = re.sub('\\s+', '', s)
            if extended:
                m = re.fullmatch('FS([LT])([AI])X(\\d)(\\d)Y(\\d)(\\d)', s)
                if m:
                    if r['format'] is not None and r['format'] != s:
                        bad('Gerber coordinate format changed within file')
                    (zero, absflag, xi, xd, yi, yd) = m.groups()
                    absolute = absflag == 'A'
                    fmt = ((int(xi), int(xd)), (int(yi), int(yd)))
                    r['format'] = s
                    if not absolute:
                        bad('Incremental Gerber unsupported')
                elif s in ['MOMM', 'MOIN']:
                    new_unit = 1 if s == 'MOMM' else 25.4
                    if unit is not None and new_unit != unit:
                        bad('Gerber unit changed within file')
                    unit = new_unit
                    r['unit'] = 'mm' if unit == 1 else 'inch'
                elif s.startswith('TF.FileFunction,'):
                    if r['fileFunction'] is not None and r['fileFunction'] != s.split(',', 1)[1]:
                        bad('Contradictory Gerber FileFunction declarations')
                    r['fileFunction'] = s.split(',', 1)[1]
                elif s.startswith(('TF.', 'TA.', 'TO.')) or s.startswith('TD'):
                    pass
                elif s.startswith('ADD'):
                    m = re.fullmatch('ADD(\\d+)([^,]+)(?:,(.*))?', s)
                    if m:
                        if int(m[1]) in r['apertures']:
                            bad('Duplicate aperture definition')
                        r['apertures'][int(m[1])] = {'shape': m[2], 'parameters': m[3]}
                        if m[2] not in ('C', 'R', 'O', 'P') and m[2] not in r['macros']:
                            bad('Unknown aperture shape or undeclared macro')
                        if m[3] is not None and not re.fullmatch(NUM + r'(?:X' + NUM + r')*', m[3]):
                            bad('Invalid aperture parameters')
                        if m[2] in ('C', 'R', 'O', 'P') and (m[3] is None or float(m[3].split('X')[0]) <= 0):
                            bad('Standard aperture requires a positive size')
                    else:
                        bad(s)
                elif s in ['LPD', 'LPC']:
                    polarity = 'dark' if s == 'LPD' else 'clear'
                elif s in ['IPPOS', 'SR', 'LMN', 'LR0', 'LS1']:
                    pass
                else:
                    bad(s)
                continue
            if s == 'M02':
                r['ended'] = True
                continue
            s = re.sub('^G0?54', '', s)
            while s.startswith('G'):
                m = re.match('G0*(\\d+)', s)
                if not m:
                    break
                g = int(m[1])
                s = s[m.end():]
                if g in [1, 2, 3]:
                    mode = g
                elif g == 75:
                    quadrant = 'multi'
                elif g == 74:
                    quadrant = 'single'
                elif g == 36:
                    if region:
                        bad('Nested Gerber region')
                    region = True
                    r['regions'] += 1
                elif g == 37:
                    if not region:
                        bad('Gerber region end without start')
                    region = False
                elif g == 90:
                    pass
                else:
                    bad('G' + str(g))
            if not s:
                continue
            if re.fullmatch('D\\d+', s) and int(s[1:]) >= 10:
                ap = int(s[1:])
                continue
            dm = re.search('D0?(\\d+)$', s)
            if dm:
                op = int(dm[1])
                s = s[:dm.start()]
                if op not in (1, 2, 3):
                    bad('Unsupported Gerber D operation')
            vals = xy_fields(s)
            if len(re.findall('[XYIJ]' + NUM, s)) != len(vals):
                bad('Duplicate coordinate axis')
                continue
            residue = re.sub('[XYIJ]' + NUM, '', s)
            if residue:
                bad(residue)
                continue
            if not vals:
                continue
            if unit is None or fmt is None:
                bad('Missing MO/FS before coordinates')
                continue
            try:
                end = [coordinate(vals['X'], unit, fmt[0], zero) if 'X' in vals else pos[0] if pos else None, coordinate(vals['Y'], unit, fmt[1], zero) if 'Y' in vals else pos[1] if pos else None]
                if None in end:
                    raise ValueError('Modal coordinate not initialized')
                if op == 2:
                    pos = end
                    continue
                if op in (1, 3) and (not region) and (ap not in r['apertures']):
                    raise ValueError('Undeclared aperture')
                if op == 3:
                    r['flashes'] += 1
                    r['flashRecords'].append({'point': end, 'aperture': ap, 'polarity': polarity})
                    pos = end
                    continue
                if op != 1 or pos is None:
                    raise ValueError('Draw without start or unsupported operation')
                points = [pos, end]
                if mode in [2, 3]:
                    if quadrant != 'multi':
                        raise ValueError('Single-quadrant arc not inferred')
                    cc = [pos[0] + coordinate(vals.get('I', '0'), unit, fmt[0], zero), pos[1] + coordinate(vals.get('J', '0'), unit, fmt[1], zero)]
                    rad = math.dist(pos, cc)
                    if rad <= 0 or abs(math.dist(end, cc) - rad) > 0.003:
                        raise ValueError('Invalid arc radius')
                    a = math.atan2(pos[1] - cc[1], pos[0] - cc[0])
                    b = math.atan2(end[1] - cc[1], end[0] - cc[0])
                    sweep = (b - a) % (2 * math.pi) if mode == 3 else -((a - b) % (2 * math.pi))
                    if math.dist(pos, end) < 1e-09:
                        sweep = 2 * math.pi if mode == 3 else -2 * math.pi
                    max_angle = 2 * math.acos(max(-1, min(1, 1 - 0.0002 / rad)))
                    n = max(2, math.ceil(abs(sweep) / max(max_angle, 1e-05)))
                    points = [[cc[0] + rad * math.cos(a + sweep * j / n), cc[1] + rad * math.sin(a + sweep * j / n)] for j in range(n + 1)]
                    points[0] = pos
                    points[-1] = end
                r['paths'].append({'points': points, 'aperture': ap, 'mode': mode, 'region': region, 'regionIndex': r['regions'] if region else None, 'polarity': polarity})
                pos = end
            except (ValueError, TypeError) as e:
                bad(str(e))
    if region:
        bad('Unclosed Gerber region')
    if unit is None or fmt is None:
        bad('Missing Gerber units or coordinate format')
    if not r['ended']:
        bad('Missing M02 end')
    return r

def parse_excellon(text):
    r = {'unit': None, 'format': None, 'tools': {}, 'features': [], 'unknown': [], 'ended': False, 'plating': None, 'layerComment': None}
    unit = None
    digits = None
    zero = 'L'
    tool = None
    pos = None
    route = False
    down = False
    header = False
    quantum = 0

    def decode(s):
        return coordinate(s, unit, digits, zero)

    def point(s, previous):
        nonlocal quantum
        vs = xy_fields(s)
        p = []
        if not re.fullmatch('(?:[XY]' + NUM + ')+', s) or len(re.findall('[XY]' + NUM, s)) != len(vs):
            raise ValueError('Invalid or repeated drill coordinate axis')
        for (idx, axis) in enumerate('XY'):
            if axis in vs:
                token = vs[axis]
                p.append(decode(token))
                q = unit * 10 ** (-len(token.split('.')[1])) if '.' in token else unit * 10 ** (-digits[1])
                quantum = max(quantum, q)
            elif previous is not None:
                p.append(previous[idx])
            else:
                raise ValueError('Uninitialized modal drill coordinate')
        return p

    def feature(a, b=None):
        if tool not in r['tools']:
            raise ValueError('Unknown selected drill tool')
        diameter = r['tools'][tool]
        if a is None or (b is not None and math.dist(a, b) <= 1e-12):
            raise ValueError('Uninitialized or zero length slot')
        r['features'].append({'shape': 'SLOT' if b is not None else 'ROUND', 'a': a, 'b': b, 'diameterMm': diameter, 'tool': tool})
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith(';'):
            if s.startswith(';TYPE='):
                kind = s.split('=', 1)[1]
                if r['plating'] is not None and kind != r['plating']:
                    r['unknown'].append({'reason': 'Contradictory plating declarations'})
                r['plating'] = kind
            if s.startswith(';Layer:'):
                r['layerComment'] = s.split(':', 1)[1].strip()
            m = re.search('FILE_FORMAT\\s*=\\s*(\\d)\\s*[:.]\\s*(\\d)', s)
            if m:
                digits = (int(m[1]), int(m[2]))
                r['format'] = digits
            continue
        try:
            if r['ended']:
                raise ValueError('Command after M30')
            if s == 'M48':
                header = True
                continue
            if s in ['%', 'M95']:
                header = False
                continue
            if s in ['M30', 'M00']:
                r['ended'] = s == 'M30'
                continue
            if s.startswith(('METRIC', 'INCH')):
                if not re.fullmatch('(METRIC|INCH)(?:,(LZ|TZ))?(?:,0+\\.0+)?', s):
                    raise ValueError('Unsupported unit declaration ' + s)
                new_unit = 1 if s.startswith('METRIC') else 25.4
                if unit is not None and unit != new_unit:
                    raise ValueError('Drill unit changed within file')
                unit = new_unit
                r['unit'] = 'mm' if unit == 1 else 'inch'
                zero = 'T' if ',TZ' in s else 'L'
                m = re.search('(0+)\\.(0+)', s)
                if m:
                    digits = (len(m[1]), len(m[2]))
                    r['format'] = digits
                continue
            if s in ['M71', 'M72']:
                new_unit = 1 if s == 'M71' else 25.4
                if unit is not None and unit != new_unit:
                    raise ValueError('Drill unit changed within file')
                unit = new_unit
                r['unit'] = 'mm' if unit == 1 else 'inch'
                continue
            if s in ['G90', 'FMAT,2', 'VER,1']:
                continue
            if s == 'G91':
                raise ValueError('Incremental drill unsupported')
            if s in ['G05', 'G5']:
                route = False
                down = False
                continue
            if s == 'M15':
                down = True
                continue
            if s in ['M16', 'M17']:
                down = False
                continue
            m = re.fullmatch('T(\\d+)(?:C(' + NUM + '))?', s)
            if m:
                t = int(m[1])
                if m[2] is not None:
                    if unit is None:
                        raise ValueError('Tool unit undeclared')
                    if t in r['tools']:
                        raise ValueError('Duplicate drill tool definition')
                    if float(m[2]) <= 0:
                        raise ValueError('Drill diameter must be positive')
                    r['tools'][t] = float(m[2]) * unit
                else:
                    tool = t
                continue
            if s.startswith('R'):
                m = re.fullmatch('R(\\d+)((?:[XY]' + NUM + ')*)', s)
                if not m or pos is None:
                    raise ValueError('Unsupported repeat command or uninitialized position')
                if int(m[1]) > 100000:
                    raise ValueError('Repeat exceeds supported feature limit')
                vs = xy_fields(m[2])
                dx = decode(vs.get('X', '0.0'))
                dy = decode(vs.get('Y', '0.0'))
                for _ in range(int(m[1])):
                    pos = [pos[0] + dx, pos[1] + dy]
                    feature(pos)
                continue
            if unit is None:
                raise ValueError('Missing drill units')
            if 'G85' in s:
                (aa, bb) = s.split('G85')
                a = point(aa, pos) if aa else pos
                if a is None:
                    raise ValueError('Uninitialized G85 start')
                b = point(bb, a)
                feature(a, b)
                pos = b
                continue
            m = re.match('G0*(0|1)(?=[XY])', s)
            if m:
                g = int(m[1])
                s = s[m.end():]
                end = point(s, pos)
                if g == 1 and down:
                    feature(pos, end)
                elif g == 1:
                    raise ValueError('Routed drill line without M15')
                pos = end
                route = True
                continue
            if re.fullmatch('(?:[XY]' + NUM + ')+', s):
                end = point(s, pos)
                if route and down:
                    feature(pos, end)
                elif route:
                    raise ValueError('Ambiguous routed drill coordinate')
                else:
                    feature(end)
                pos = end
                continue
            raise ValueError('Unsupported Excellon command ' + s[:80])
        except (ValueError, TypeError, AssertionError, KeyError, OverflowError) as e:
            r['unknown'].append({'line': raw, 'reason': str(e)})
    r['coordinateQuantumMm'] = quantum
    if not r['ended']:
        r['unknown'].append({'reason': 'Missing M30 end'})
    return r


def finite_number(value, field, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InputError(field + ' must be a finite number')
    if positive and value <= 0:
        raise InputError(field + ' must be positive')
    return value


def validate_expected(expected):
    if not isinstance(expected, dict):
        raise InputError('Expected manifest must be an object')
    board = expected.get('board')
    if not isinstance(board, dict):
        raise InputError('Expected manifest requires board object')
    for field in ('widthMm', 'heightMm'):
        finite_number(board.get(field), 'board.' + field, positive=True)
    tolerance = expected.get('toleranceMm', 0.003)
    finite_number(tolerance, 'toleranceMm', positive=True)
    if tolerance > 0.01:
        raise InputError('toleranceMm above 0.01 is outside the supported geometry comparison range')
    points = board.get('outlinePointsMm')
    if not isinstance(points, list) or len(points) < 4:
        raise InputError('board.outlinePointsMm needs an explicitly closed ring with at least four points')
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            raise InputError('Outline points must be [xMm, yMm] pairs')
        for value in point:
            finite_number(value, 'outline coordinate')
    if points[0] != points[-1]:
        raise InputError('Expected outline must be explicitly closed')
    _, Polygon, _ = require_geometry()
    polygon = Polygon(points)
    if not polygon.is_valid or polygon.area <= 0 or not polygon.exterior.is_simple:
        raise InputError('Expected outline must be one simple closed outer ring')
    bounds = polygon.bounds
    if abs(bounds[2] - bounds[0] - board['widthMm']) > tolerance or abs(bounds[3] - bounds[1] - board['heightMm']) > tolerance:
        raise InputError('Expected widthMm/heightMm must equal the outline envelope dimensions')
    count = board.get('expectedCopperLayers', expected.get('expectedCopperLayers'))
    if isinstance(count, bool) or not isinstance(count, int) or not 2 <= count <= 64:
        raise InputError('board.expectedCopperLayers must be an integer from 2 to 64')
    holes = expected.get('holes')
    if not isinstance(holes, list) or not holes:
        raise InputError('A nonempty holes list is required; zero-drill expectations are unsupported')
    ids = set()
    geometry = set()
    for hole in holes:
        if not isinstance(hole, dict) or not isinstance(hole.get('id'), str) or not hole['id']:
            raise InputError('Each hole requires a nonempty string id')
        if hole['id'] in ids:
            raise InputError('Duplicate expected hole id: ' + hole['id'])
        ids.add(hole['id'])
        if hole.get('shape') not in ('ROUND', 'SLOT'):
            raise InputError('Hole shape must be ROUND or SLOT: ' + hole['id'])
        for field in ('xMm', 'yMm', 'diameterMm', 'totalLengthMm', 'rotationCanvasDeg'):
            finite_number(hole.get(field), 'holes.' + hole['id'] + '.' + field, positive=field in ('diameterMm', 'totalLengthMm'))
        if hole['shape'] == 'ROUND' and abs(hole['totalLengthMm'] - hole['diameterMm']) > 1e-9:
            raise InputError('ROUND totalLengthMm must equal diameterMm: ' + hole['id'])
        if hole['shape'] == 'SLOT' and hole['totalLengthMm'] <= hole['diameterMm']:
            raise InputError('SLOT totalLengthMm must exceed diameterMm: ' + hole['id'])
        if 'plated' in hole and not isinstance(hole['plated'], bool):
            raise InputError('plated must be boolean when supplied: ' + hole['id'])
        if 'kind' in hole and not isinstance(hole['kind'], str):
            raise InputError('kind must be a string when supplied')
        key = (hole['shape'], hole['xMm'], hole['yMm'], hole['diameterMm'], hole['totalLengthMm'], 0 if hole['shape'] == 'ROUND' else hole['rotationCanvasDeg'] % 180)
        if key in geometry:
            raise InputError('Duplicate expected hole geometry: ' + hole['id'])
        geometry.add(key)
    roles = expected.get('drillFileRoles')
    if roles is not None:
        if not isinstance(roles, dict) or set(roles) != {'fullPTH', 'viaSubset'} or not all(isinstance(v, str) and v for v in roles.values()):
            raise InputError('drillFileRoles requires distinct fullPTH and viaSubset exact ZIP entry names')
        if roles['fullPTH'] == roles['viaSubset']:
            raise InputError('fullPTH and viaSubset must name different files')
    return count, tolerance


def outline_check(gerber, expected, tolerance):
    LineString, Polygon, unary_union = require_geometry()
    issues = []
    paths = gerber['paths']
    nodes, edges, seen_edges = [], [], set()

    def node(point):
        hits = [index for index, other in enumerate(nodes) if math.dist(point, other) <= tolerance]
        if len(hits) > 1:
            issues.append('Ambiguous close outline endpoints')
        if hits:
            return hits[0]
        nodes.append(point)
        return len(nodes) - 1

    for path in paths:
        a, b = node(path['points'][0]), node(path['points'][-1])
        canonical = tuple(tuple(round(v / tolerance) for v in p) for p in path['points'])
        key = min(canonical, tuple(reversed(canonical)))
        if key in seen_edges:
            issues.append('Duplicate outline edge')
        seen_edges.add(key)
        edges.append((a, b))
    if not edges:
        return {'passed': False, 'issues': ['Empty outline']}
    degree = [0] * len(nodes)
    adjacency = [[] for _ in nodes]
    for a, b in edges:
        degree[a] += 1
        degree[b] += 1
        adjacency[a].append(b)
        adjacency[b].append(a)
    groups, unseen = [], set(range(len(nodes)))
    while unseen:
        stack, visited = [next(iter(unseen))], set()
        while stack:
            index = stack.pop()
            if index not in visited:
                visited.add(index)
                stack.extend(adjacency[index])
        unseen -= visited
        groups.append(sorted(visited))
    if len(groups) != 1:
        issues.append('Expected exactly one connected outline loop')
    if any(value != 2 for value in degree):
        issues.append('Open or branched outline: endpoint degree is not 2')
    if gerber['flashes'] or gerber['regions']:
        raise InputError('Flashed or region profile is unsupported by the outline centerline check')
    if any(path['polarity'] != 'dark' for path in paths):
        issues.append('Clear-polarity profile is not a positive outline centerline')
    if any(gerber['apertures'].get(path['aperture'], {}).get('shape') != 'C' for path in paths):
        raise InputError('Only declared circular apertures are supported for outline centerlines')
    actual = unary_union([LineString(path['points']) for path in paths])
    bounds = actual.bounds
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    if abs(width - expected['widthMm']) > tolerance or abs(height - expected['heightMm']) > tolerance:
        issues.append('Outline dimensions differ from declared expected envelope')
    points = expected['outlinePointsMm']
    canvas_min_x = min(p[0] for p in points)
    canvas_min_y = min(p[1] for p in points)
    x0, y0 = bounds[0] - canvas_min_x, bounds[3] + canvas_min_y
    target = LineString([(x + x0, y0 - y) for x, y in points])
    distance = actual.hausdorff_distance(target)
    if distance > tolerance:
        issues.append('Outline differs from declared expected contour')
    # Check intersections on the original paths: union would hide overlapping edges.
    for i, path in enumerate(paths):
        line = LineString(path['points'])
        if not line.is_simple:
            issues.append('Self-intersecting outline path')
        for other in paths[:i]:
            intersection = line.intersection(LineString(other['points']))
            if not intersection.is_empty:
                allowed = [p for p in (path['points'][0], path['points'][-1]) if any(math.dist(p, q) <= tolerance for q in (other['points'][0], other['points'][-1]))]
                if intersection.geom_type == 'Point':
                    if not any(math.dist(list(intersection.coords)[0], p) <= tolerance for p in allowed):
                        issues.append('Outline paths cross away from shared endpoints')
                elif intersection.geom_type == 'MultiPoint':
                    if any(not any(math.dist(list(pt.coords)[0], p) <= tolerance for p in allowed) for pt in intersection.geoms):
                        issues.append('Outline paths cross away from shared endpoints')
                else:
                    issues.append('Overlapping outline segments')
    return {'passed': not issues, 'issues': issues, 'boundsMm': list(bounds), 'sizeMm': [width, height], 'connectedLoops': len(groups), 'endpointDegrees': degree, 'hausdorffToExpectedMm': distance, 'toleranceMm': tolerance,
            'coordinateTransform': {'translationX': x0, 'translationY': y0, 'x': 'canvasX + translationX', 'y': 'translationY - canvasY', 'basis': 'Declared canvas axes and full outline comparison; no rotation, scale or drill best fit'}}


def match_holes(features, expected, transform, tolerance):
    x0, y0 = transform['translationX'], transform['translationY']
    remaining = set(range(len(features)))
    matches, missing, ambiguous = [], [], []
    for hole in expected:
        center = [hole['xMm'] + x0, y0 - hole['yMm']]
        half = (hole['totalLengthMm'] - hole['diameterMm']) / 2
        angle = -math.radians(hole['rotationCanvasDeg'])
        a = [center[0] - half * math.cos(angle), center[1] - half * math.sin(angle)]
        b = [center[0] + half * math.cos(angle), center[1] + half * math.sin(angle)]
        hits = []
        for index in sorted(remaining):
            feature = features[index]
            if feature['shape'] != hole['shape'] or abs(feature['diameterMm'] - hole['diameterMm']) > tolerance:
                continue
            if hole['shape'] == 'ROUND':
                distance = math.dist(center, feature['a'])
            else:
                distance = min(max(math.dist(a, feature['a']), math.dist(b, feature['b'])), max(math.dist(a, feature['b']), math.dist(b, feature['a'])))
            if distance <= tolerance:
                hits.append((index, distance))
        if len(hits) == 1:
            index, distance = hits[0]
            remaining.remove(index)
            matches.append({'expectedId': hole['id'], 'featureIndex': index, 'file': features[index].get('file'), 'tool': features[index]['tool'], 'coordinateErrorMm': distance})
        elif not hits:
            missing.append(hole['id'])
        else:
            ambiguous.append({'expectedId': hole['id'], 'candidateFeatureIndices': [index for index, _ in hits]})
    return {'passed': not missing and not ambiguous and not remaining, 'expected': len(expected), 'actual': len(features), 'matched': len(matches), 'missing': missing, 'unmatchedExportedFeatures': [{'index': index, **features[index]} for index in sorted(remaining)], 'ambiguous': ambiguous, 'matches': matches, 'coordinateAndDiameterToleranceMm': tolerance, 'counts': {'round': sum(f['shape'] == 'ROUND' for f in features), 'slots': sum(f['shape'] == 'SLOT' for f in features)}}


def inventory(export):
    path = Path(export)
    if not path.is_file() or not zipfile.is_zipfile(path):
        raise InputError('Export must be a readable ZIP file')
    with zipfile.ZipFile(path) as archive:
        infos = [entry for entry in archive.infolist() if not entry.is_dir()]
        names = [entry.filename for entry in archive.infolist()]
        normalized = [name.replace('\\', '/').casefold() for name in names]
        if len(names) != len(set(names)) or len(normalized) != len(set(normalized)):
            raise InputError('Duplicate ZIP entry names (including case or separator aliases)')
        if len(infos) > 10000 or sum(entry.file_size for entry in infos) > 512 * 1024 * 1024:
            raise InputError('ZIP exceeds supported 10000-file / 512 MiB uncompressed limit')
        if not infos:
            raise InputError('ZIP is empty')
        if archive.testzip() is not None:
            raise InputError('ZIP CRC failure')
        return [(entry.filename, archive.read(entry)) for entry in infos]


def select_drill_features(drills, expected, transform, tolerance, declaration=None):
    features = [{**feature, 'file': name} for name, drill in drills for feature in drill['features']]
    roles = {'status': 'ALL_FILES_COMBINED', 'rawFeatureOccurrences': len(features)}
    by_name = dict(drills)
    if declaration:
        primary_name, subset_name = declaration['fullPTH'], declaration['viaSubset']
        if primary_name not in by_name or subset_name not in by_name:
            raise InputError('drillFileRoles names must resolve to parsed Excellon ZIP entries')
        basis = 'Explicit expected manifest file-role declaration'
    else:
        primary = [name for name, drill in drills if Path(name).name == 'Drill_PTH_Through.DRL' and drill['layerComment'] == 'PTH_Through']
        subset = [name for name, drill in drills if Path(name).name == 'Drill_PTH_Through_Via.DRL' and drill['layerComment'] == 'PTH_Through_Via']
        if len(primary) != 1 or len(subset) != 1:
            return features, roles
        primary_name, subset_name = primary[0], subset[0]
        basis = 'Recognized exporter names plus matching Layer comments'
    primary = [{**f, 'file': primary_name} for f in by_name[primary_name]['features']]
    subset = [{**f, 'file': subset_name} for f in by_name[subset_name]['features']]
    expected_pth = [hole for hole in expected if hole.get('plated') is True]
    expected_vias = [hole for hole in expected_pth if hole.get('kind') in ('via', 'via-drill')]
    primary_proof = match_holes(primary, expected_pth, transform, tolerance)
    subset_proof = match_holes(subset, expected_vias, transform, tolerance)
    def key(feature):
        a, b = feature['a'], feature['b']
        endpoints = sorted([a, b]) if b is not None else [a]
        return json.dumps([feature['shape'], endpoints, feature['diameterMm']], sort_keys=True)
    primary_counts, subset_counts = Counter(map(key, primary)), Counter(map(key, subset))
    exact = bool(subset_counts) and all(primary_counts[k] >= count for k, count in subset_counts.items())
    plating_known = by_name[primary_name]['plating'] == by_name[subset_name]['plating'] == 'PLATED'
    proof = bool(expected_pth and expected_vias and primary_proof['passed'] and subset_proof['passed'] and exact and plating_known)
    roles.update({'basis': basis, 'fullPTH': primary_name, 'viaSubset': subset_name, 'roleProofPassed': proof, 'fullPTHProof': primary_proof, 'viaSubsetProof': subset_proof, 'exactGeometryMultisetSubset': exact, 'explicitPlatedTypes': plating_known})
    if not proof:
        roles['status'] = 'ROLE_PROOF_FAILED_ALL_OCCURRENCES_RETAINED'
        return features, roles
    features = [f for f in features if f['file'] != subset_name]
    roles.update({'status': 'FULL_PTH_AND_PROVEN_VIA_SUBSET', 'physicalDrillFeatures': len(features), 'excludedRepeatedOccurrences': len(subset), 'interpretation': 'The viaSubset file repeats holes in fullPTH; it is not an additional drilling pass. Other files and all duplicates within a file remain counted.'})
    return features, roles


def audit(export, expected):
    count, tolerance = validate_expected(expected)
    entries = inventory(export)
    files, gerbers, drills = [], [], []
    for name, data in entries:
        item = {'name': name, 'bytes': len(data), 'sha256': sha(data), 'kind': 'ancillary-unchecked'}
        files.append(item)
        suffix = Path(name).suffix.lower()
        candidate = suffix in ('.drl', '.xln', '.exc', '.gko', '.gtl', '.gbl', '.gbr', '.grb', '.gm1', '.gts', '.gbs', '.gto', '.gbo', '.gtp', '.gbp') or bool(re.fullmatch(r'\.g\d+', suffix))
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            if candidate:
                raise InputError('Manufacturing text file is not UTF-8/ASCII: ' + name)
            continue
        if 'M48' in text[:3000] or suffix in ('.drl', '.xln', '.exc'):
            parsed = parse_excellon(text)
            drills.append((name, parsed))
            item.update({'kind': 'Excellon', 'featureCount': len(parsed['features'])})
        elif '%FS' in text[:5000] or '%MO' in text[:5000] or candidate:
            parsed = parse_gerber(text)
            gerbers.append((name, parsed))
            item.update({'kind': 'Gerber', 'fileFunction': parsed['fileFunction'], 'drawSegments': len(parsed['paths']), 'flashes': parsed['flashes'], 'regions': parsed['regions']})
        else:
            continue
        if parsed['unknown']:
            raise InputError('Unsupported or invalid syntax in ' + name + ': ' + json.dumps(parsed['unknown'][:10], ensure_ascii=False) + f" ({len(parsed['unknown'])} issues)")
    if not gerbers and not drills:
        raise InputError('ZIP contains no recognized manufacturing files')
    profiles = [(name, g) for name, g in gerbers if (g['fileFunction'] or '').startswith('Profile') or (not g['fileFunction'] and re.search(r'(outline|\.gko$)', name, re.I))]
    outline = {'passed': False, 'issues': ['Expected exactly one profile Gerber'], 'profileFiles': [name for name, _ in profiles]}
    if len(profiles) == 1:
        outline.update(outline_check(profiles[0][1], expected['board'], tolerance))
    copper = []
    for name, gerber in gerbers:
        function = gerber['fileFunction'] or ''
        match = re.fullmatch(r'Copper,L(\d+),(Top|Bot|Inr)(?:,.*)?', function)
        role = None
        if match:
            role = {'layerNumber': int(match[1]), 'position': match[2], 'basis': 'X2 FileFunction'}
        elif function.startswith('Copper'):
            raise InputError('Unsupported copper FileFunction: ' + function)
        elif not function:
            suffix = Path(name).suffix.lower()
            if suffix in ('.gtl', '.gbl'):
                role = {'layerNumber': 1 if suffix == '.gtl' else count, 'position': 'Top' if suffix == '.gtl' else 'Bot', 'basis': 'Conventional suffix and declared layer count'}
            else:
                inner = re.fullmatch(r'\.g(\d+)', suffix)
                if inner:
                    role = {'layerNumber': int(inner[1]) + 1, 'position': 'Inr', 'basis': 'Conventional .gN internal-layer suffix and declared layer count'}
        if role:
            copper.append({'file': name, **role, 'nonempty': bool(gerber['paths'] or gerber['flashes'] or gerber['regions'])})
    copper_pass = len(copper) == count and sorted(layer['layerNumber'] for layer in copper) == list(range(1, count + 1)) and all(layer['nonempty'] and layer['position'] == ('Top' if layer['layerNumber'] == 1 else 'Bot' if layer['layerNumber'] == count else 'Inr') for layer in copper)
    if outline['passed']:
        features, roles = select_drill_features(drills, expected['holes'], outline['coordinateTransform'], tolerance, expected.get('drillFileRoles'))
        holes = match_holes(features, expected['holes'], outline['coordinateTransform'], tolerance)
    else:
        roles = {'status': 'OUTLINE_FRAME_UNESTABLISHED'}
        holes = {'passed': False, 'matches': [], 'blocked': 'Board coordinate transform was not established by the declared closed outline'}
    plating = {'status': 'NOT_VERIFIED', 'mismatches': [], 'unknown': []}
    expected_by_id = {hole['id']: hole for hole in expected['holes']}
    drill_types = {name: drill['plating'] for name, drill in drills}
    for match in holes['matches']:
        want = expected_by_id[match['expectedId']].get('plated')
        actual_type = drill_types[match['file']]
        if want is None or actual_type not in ('PLATED', 'NON_PLATED'):
            plating['unknown'].append(match['expectedId'])
        elif want != (actual_type == 'PLATED'):
            plating['mismatches'].append(match['expectedId'])
    if plating['mismatches']:
        plating['status'] = 'MISMATCH'
    elif holes['passed'] and not plating['unknown']:
        plating['status'] = 'VERIFIED_AGAINST_DECLARED_EXPECTATION'
    elif holes['matches']:
        plating['status'] = 'UNKNOWN_INCOMPLETE_EVIDENCE'
    plating['verified'] = plating['status'] == 'VERIFIED_AGAINST_DECLARED_EXPECTATION'
    passed = bool(outline['passed'] and copper_pass and holes['passed'] and not plating['mismatches'])
    evidence = {key: expected[key] for key in ('provenance', 'reviewEvidence', 'sourceFiles', 'binding') if key in expected}
    return {'schema': 'easyeda-manufacturing-check/v1', 'status': 'PASS' if passed else 'FAIL', 'checked': True, 'passed': passed, 'passedSpecifiedExportGeometryChecks': passed,
            'qualification': 'Only supported export geometry and inventory checks against the declared expected manifest. Plating verification is reported separately; known mismatch fails.',
            'exportPath': str(Path(export).resolve()), 'exportSha256': sha(Path(export).read_bytes()), 'archiveInventory': files,
            'expectedCounts': {'copperLayers': count, 'drilledFeatures': len(expected['holes'])}, 'outline': outline,
            'copperLayers': {'passed': copper_pass, 'expectedCount': count, 'layers': copper, 'scope': 'Layer identity, unique presence and nonempty inventory only; copper image rendering and connectivity are not evaluated'},
            'drills': {**holes, 'fileRoles': roles, 'platingClassification': plating, 'files': [{'file': name, 'unit': drill['unit'], 'format': drill['format'], 'tools': drill['tools'], 'features': len(drill['features']), 'declaredPlating': drill['plating']} for name, drill in drills]},
            'suppliedEvidence': {'values': evidence, 'status': 'SUPPLIED_NOT_INDEPENDENTLY_VERIFIED'}, 'nativeMutation': False, 'nativeSaveVerified': False, 'functionalQualification': False, 'manufacturingRelease': False,
            'limitations': ['No full copper geometry, connectivity, mask registration, native DRC, impedance, current or thermal qualification.', 'No source/native-save timing, functional qualification or fab acceptance is inferred from manifest provenance.', 'One simple exterior outline only; cutouts, flashed/region profiles and undeclared coordinate transforms are unsupported.', 'A geometric PASS may have unknown plating: inspect drills.platingClassification. Ancillary files are hashed but not interpreted.']}


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError('CLI arguments: ' + message)


def load_expected(path):
    def reject_constant(value):
        raise InputError('Nonfinite JSON constant is unsupported: ' + value)

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InputError('Duplicate JSON object key: ' + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=unique_pairs, parse_constant=reject_constant)


def output_is_input(out, arguments):
    for name in (arguments.export, arguments.expected):
        source = Path(name).resolve()
        if out == source or (out.exists() and source.exists() and out.samefile(source)):
            return True
    return False


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument('export', help='Gerber/Excellon ZIP; read only')
    parser.add_argument('--expected', required=True, help='Declared expected geometry JSON')
    parser.add_argument('--out', required=True, help='Output JSON report (only file written)')
    arguments = None
    try:
        arguments = parser.parse_args(argv)
        out = Path(arguments.out).resolve()
        if output_is_input(out, arguments):
            raise InputError('Report output must not overwrite either input')
        expected = load_expected(arguments.expected)
        result = audit(arguments.export, expected)
        result['expectedManifest'] = {'file': str(Path(arguments.expected).resolve()), 'sha256': sha(Path(arguments.expected).read_bytes())}
        code = 0 if result['passed'] else 1
    except (InputError, OSError, ValueError, KeyError, TypeError, RuntimeError, zipfile.BadZipFile) as exc:
        result = {'schema': 'easyeda-manufacturing-check/v1', 'status': 'ERROR', 'checked': False, 'passed': False, 'error': {'type': type(exc).__name__, 'message': str(exc)}, 'nativeMutation': False, 'manufacturingRelease': False}
        code = 2
    if arguments:
        out = Path(arguments.out).resolve()
        if not output_is_input(out, arguments):
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
            except OSError as exc:
                result = {'schema': 'easyeda-manufacturing-check/v1', 'status': 'ERROR', 'checked': False, 'passed': False, 'error': {'type': 'OutputError', 'message': str(exc)}}
                code = 2
    summary = {key: result[key] for key in ('schema', 'status', 'checked', 'passed', 'error') if key in result}
    if arguments:
        summary['out'] = arguments.out
    print(json.dumps(summary, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
