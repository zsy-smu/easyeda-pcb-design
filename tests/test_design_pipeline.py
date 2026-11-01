"""Cross-tool test: route -> plane -> manufacturing copper, then a broken drill.

Uses synthetic dimensions and writes no native/live EDA objects.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import plan_local_route as route
import build_ground_plane as plane
import check_copper_connectivity as copper
import check_evidence as evidence
import run_copper_benchmarks as fixtures
try:
    copper.geometry_dependency()
    HAS_GEOMETRY = True
except copper.Unsupported:
    HAS_GEOMETRY = False


@unittest.skipUnless(HAS_GEOMETRY, 'Shapely 2.x unavailable')
class DesignPipeline(unittest.TestCase):
    def test_candidate_export_physical_connectivity_and_staleness(self):
        pads = [dict(kind='pad', id='J1.1', net='RET', layers=[1], x=2, y=3, width=1.5, height=1.5, shape='circle'),
                dict(kind='pad', id='J2.1', net='RET', layers=[2], x=10, y=3, width=1.5, height=1.5, shape='circle')]
        source = {'schema': 'easyeda-local-route-input/v1', 'unit': 'mm', 'sourceRevision': 'pipeline-fixture',
                  'net': 'RET', 'fromPadId': 'J1.1', 'toPadId': 'J2.1', 'board': [[0, 0], [12, 0], [12, 8], [0, 8]],
                  'width': 0.4, 'grid': 0.5, 'edgeClearance': 0.4, 'viaDiameter': 0.8, 'viaHole': 0.3,
                  'viaDrillToSmdClearance': 0.2, 'viaHoleToHoleClearance': 0.2, 'viaCost': 2, 'maxExpanded': 2000,
                  'clearanceByNet': {}, 'copper': pads, 'holes': [], 'keepouts': []}
        candidate = route.plan(source)
        self.assertEqual(candidate['status'], 'CANDIDATE')
        self.assertEqual(len(candidate['vias']), 1)
        g = copy.deepcopy(source)
        g.update(schema='easyeda-ground-input/v1', anchorPadIds=['J1.1'], minBridgeWidth=0.2, minIslandArea=0.01)
        for i, t in enumerate(candidate['tracks']):
            g['copper'].append(dict(kind='track', id=f't{i}', net='RET', layers=[t['layer']], points=t['points'], width=t['width']))
        for i, v in enumerate(candidate['vias']):
            g['copper'].append(dict(kind='circle', id=f'v{i}', net='RET', layers=[1, 2], x=v['x'], y=v['y'], diameter=v['diameter']))
            g['holes'].append(dict(kind='circle', net='RET', plated=True, x=v['x'], y=v['y'], diameter=v['hole'], clearance=0))
        fills = plane.build(g)
        self.assertTrue(all(fills['areaMm2ByLayer'][str(l)] > 0 for l in (1, 2)))
        files = {}
        for layer, name in [(1, 'top.gtl'), (2, 'bottom.gbl')]:
            cmds = ''.join(fixtures.flash(p['x'], p['y']) for p in pads if layer in p['layers'])
            cmds += ''.join(fixtures.track(t['points'], 11) for t in candidate['tracks'] if t['layer'] == layer)
            cmds += ''.join(fixtures.flash(v['x'], v['y'], 12) for v in candidate['vias'])
            cmds += ''.join('G36*' + fixtures.track(f['points']) + 'G37*' for f in fills['fills'] if f['layer'] == layer)
            files[name] = fixtures.gerber(cmds, '%ADD10C,1.5*%%ADD11C,0.4*%%ADD12C,0.8*%')
        files['pth.drl'] = fixtures.drill([(v['x'], v['y']) for v in candidate['vias']], True)
        files['npth.drl'] = fixtures.drill([], False)
        spec = dict(schema='easyeda-copper-expected/v1', unit='mm', frame='gerber', pads=pads,
                    files=dict(top='top.gtl', bottom='bottom.gbl', pth=['pth.drl'], npth=['npth.drl']))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, expected, report = [root / n for n in ('manufacturing.zip', 'expected.json', 'copper.json')]
            with zipfile.ZipFile(archive, 'w') as z:
                for n, text in files.items():
                    z.writestr(n, text)
            expected.write_text(json.dumps(spec), 'utf-8')
            proc = subprocess.run([sys.executable, str(Path(copper.__file__)), str(archive), '--expected', str(expected), '--out', str(report)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(report.read_text('utf-8'))
            self.assertTrue(data['passed'])
            item = dict(id='copper', report=str(report), reportSha256=hashlib.sha256(report.read_bytes()).hexdigest(), expectedSchema=evidence.COPPER,
                        requiredScope=['actual-two-layer-copper'], inputs=dict(export=str(archive), expected=str(expected)), selection=None, context=None)
            self.assertEqual(evidence.audit_check(item)['findings'], [])
            # Same geometry without a plated barrel must fail cross-face connection.
            files['npth.drl'] = fixtures.drill([(v['x'], v['y']) for v in candidate['vias']], False)
            files['pth.drl'] = fixtures.drill([], True)
            with zipfile.ZipFile(archive, 'w') as z:
                for n, text in files.items():
                    z.writestr(n, text)
            broken = copper.audit(archive, spec)
            self.assertFalse(broken['passed'])
            self.assertFalse(broken['netConnectivity']['RET']['connected'])
            self.assertIn('STALE_INPUT', {f['code'] for f in evidence.audit_check(item)['findings']})


if __name__ == '__main__':
    unittest.main()
