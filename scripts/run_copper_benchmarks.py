#!/usr/bin/env python3
"""Generate synthetic broken/fixed manufacturing examples and audit them.

The fixtures encode expected behavior, not a reproduction of a private board.
Output is only written to a NEW directory to preserve earlier evidence.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_copper_connectivity import audit, Unsupported


def gerber(commands='', apertures='%ADD10C,1*%%ADD11C,0.4*%%ADD12C,0.15*%'):
    return '%FSLAX46Y46*%%MOMM*%' + apertures + '\nG01*\n' + commands + '\nM02*'


def flash(x, y, ap=10):
    return f'D{ap}*X{round(x * 1e6)}Y{round(y * 1e6)}D03*'


def track(points, ap=11):
    return f'D{ap}*' + ''.join(f'X{round(x * 1e6)}Y{round(y * 1e6)}D{2 if i == 0 else 1:02d}*' for i, (x, y) in enumerate(points))


def drill(points, plated):
    return ('M48\n;TYPE=' + ('PLATED' if plated else 'NON_PLATED') + '\nMETRIC,LZ,000.000\nT01C0.300\n%\nG05\nG90\nT01\n' +
            ''.join(f'X{x:.6f}Y{y:.6f}\n' for x, y in points) + 'M30\n')


def pad(ident, net, x, y, layers=None):
    return {'id': ident, 'ref': ident.split('.')[0], 'pin': ident.split('.')[-1], 'net': net,
            'x': x, 'y': y, 'width': 1, 'height': 1, 'shape': 'circle', 'layers': layers or [1]}


def base():
    pads = [pad('J1.1', 'A', 2, 5), pad('J2.1', 'A', 8, 5),
            pad('J3.1', 'B', 5, 2), pad('J4.1', 'B', 5, 8)]
    spec = {'schema': 'easyeda-copper-expected/v1', 'unit': 'mm', 'frame': 'gerber', 'pads': pads,
            'files': {'top': 'top.gtl', 'bottom': 'bottom.gbl', 'pth': ['pth.drl'], 'npth': ['npth.drl']}}
    af = flash(2, 5) + flash(8, 5)
    bf = flash(5, 2) + flash(5, 8)
    return spec, {'top.gtl': gerber(af + track([(2, 5), (8, 5)])),
                  'bottom.gbl': gerber(bf + track([(5, 2), (5, 8)])),
                  'pth.drl': drill([], True), 'npth.drl': drill([], False)}, af, bf


def cases():
    spec, files, af, bf = base()
    for p in spec['pads'][2:]:
        p['layers'] = [2]
    result = [('separate_faces_fixed', spec, files, True)]
    s, f = copy.deepcopy(spec), files.copy()
    s['pads'][2]['layers'] = s['pads'][3]['layers'] = [1]
    f['top.gtl'] = gerber(af + bf + track([(2, 5), (8, 5)]) + track([(5, 2), (5, 8)]))
    f['bottom.gbl'] = gerber()
    result.append(('same_face_crossing_broken', s, f, False))
    s, f = copy.deepcopy(spec), files.copy()
    f['top.gtl'] = gerber(af + track([(2, 5), (4.5, 5)]) + track([(5.5, 5), (8, 5)]))
    result.append(('cut_to_hide_short_broken', s, f, False))
    s, f = copy.deepcopy(spec), files.copy()
    # A foreign plated pad on the bottom-face trace connects that trace to top A.
    s['pads'].append(pad('D1.1', 'A', 5, 5, [1, 2]))
    f['top.gtl'] = gerber(af + flash(5, 5) + track([(2, 5), (8, 5)]))
    f['bottom.gbl'] = gerber(bf + flash(5, 5) + track([(5, 2), (5, 8)]))
    f['pth.drl'] = drill([(5, 5)], True)
    result.append(('foreign_pth_after_layer_change_broken', s, f, False))
    s, f = copy.deepcopy(s), f.copy()
    f['bottom.gbl'] = gerber(bf + flash(5, 5) + track([(5, 2), (6.5, 2), (6.5, 8), (5, 8)]))
    result.append(('foreign_pth_detour_fixed', s, f, True))
    s, f = copy.deepcopy(spec), files.copy()
    # Trace alone overlaps the expected pad center: old any-overlap tests pass.
    f['top.gtl'] = gerber(flash(2, 5) + track([(2, 5), (8, 5)], 12))
    result.append(('pad_replaced_by_sliver_broken', s, f, False))
    # Two pads on opposite faces with a common hole. Only actual plating joins them.
    for plated in (True, False):
        s, f = copy.deepcopy(spec), files.copy()
        s['pads'] = [pad('J1.1', 'A', 3, 3, [1]), pad('J2.1', 'A', 3, 3, [2])]
        f['top.gtl'] = f['bottom.gbl'] = gerber(flash(3, 3))
        f['pth.drl'], f['npth.drl'] = drill([(3, 3)] if plated else [], True), drill([] if plated else [(3, 3)], False)
        result.append(('pth_barrel_fixed' if plated else 'npth_is_not_via_broken', s, f, plated))
    s, f = copy.deepcopy(spec), files.copy()
    s['pads'].append(pad('U1.NC', None, 3.5, 5))
    f['top.gtl'] = gerber(af + flash(3.5, 5) + track([(2, 5), (8, 5)]))
    result.append(('nc_contact_broken', s, f, False))
    s, f = copy.deepcopy(spec), files.copy()
    s['rejectUnassignedIslands'] = True
    f['top.gtl'] = gerber(af + track([(2, 5), (8, 5)]) + flash(12, 12))
    result.append(('floating_copper_broken', s, f, False))
    return result


def run(directory):
    if directory.exists():
        raise Unsupported('Benchmark output directory must be new')
    directory.mkdir(parents=True)
    rows = []
    for name, spec, files, expected_pass in cases():
        dest = directory / name
        dest.mkdir()
        archive = dest / 'manufacturing.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            for n, data in files.items():
                z.writestr(n, data)
        (dest / 'expected.json').write_text(json.dumps(spec, indent=2), 'utf-8')
        report = audit(archive, spec)
        (dest / 'report.json').write_text(json.dumps(report, indent=2), 'utf-8')
        rows.append({'case': name, 'expectedPass': expected_pass, 'observedPass': report['passed'],
                     'matched': report['passed'] == expected_pass})
    result = {'schema': 'easyeda-copper-benchmarks/v1', 'passed': all(r['matched'] for r in rows), 'cases': rows}
    (directory / 'benchmark.json').write_text(json.dumps(result, indent=2), 'utf-8')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out-dir', type=Path, required=True)
    a = p.parse_args()
    try:
        r = run(a.out_dir)
        print(json.dumps(r))
        return 0 if r['passed'] else 1
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
