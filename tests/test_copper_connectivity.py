import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import check_copper_connectivity as c
import run_copper_benchmarks as b
try:
    c.geometry_dependency()
    HAS_GEOMETRY = True
except c.Unsupported:
    HAS_GEOMETRY = False


@unittest.skipUnless(HAS_GEOMETRY, 'Shapely 2.x unavailable')
class CopperChecks(unittest.TestCase):
    def audit(self, spec, files):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'export.zip'
            with zipfile.ZipFile(path, 'w') as z:
                for name, data in files.items():
                    z.writestr(name, data)
            return c.audit(path, spec)

    def test_physical_failure_and_repair_scenarios(self):
        for name, spec, files, expected in b.cases():
            with self.subTest(case=name):
                r = self.audit(spec, files)
                self.assertEqual(r['passed'], expected)
                if name == 'cut_to_hide_short_broken':
                    self.assertFalse(r['netConnectivity']['A']['connected'])
                    self.assertEqual(r['copperShorts'], [])
                if name == 'pad_replaced_by_sliver_broken':
                    self.assertTrue(r['missingOrDamagedPads'])
                if name == 'foreign_pth_after_layer_change_broken':
                    self.assertTrue(r['copperShorts'])
                if name == 'nc_contact_broken':
                    self.assertTrue(r['ncContacts'])

    def test_benchmark_exports_reviewable_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'cases'
            self.assertTrue(b.run(output)['passed'])
            self.assertTrue((output / 'cut_to_hide_short_broken' / 'manufacturing.zip').is_file())
            with self.assertRaises(c.Unsupported):
                b.run(output)

    def test_region_hole_not_filled(self):
        from shapely.geometry import Point
        cmds = 'G36*' + b.track([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]) + b.track([(3, 3), (7, 3), (7, 7), (3, 7), (3, 3)]) + 'G37*'
        g, _ = c.render_gerber(b.gerber(cmds))
        self.assertAlmostEqual(g.area, 84)
        self.assertFalse(g.contains(Point(5, 5)))

    def test_inch_aperture_and_coordinates(self):
        text = '%FSLAX46Y46*%%MOIN*%%ADD10C,0.1*%D10*X1000000Y2000000D03*M02*'
        g, _ = c.render_gerber(text)
        self.assertAlmostEqual(g.centroid.x, 25.4)
        self.assertAlmostEqual(g.bounds[2] - g.bounds[0], 2.54)

    def test_reject_clear_macro_arc_hole_aperture(self):
        examples = [b.gerber('%LPC*%' + b.flash(2, 2)),
                    b.gerber(b.flash(2, 2), '%AMROUND*1,1,1,0,0*%%ADD10ROUND*%'),
                    b.gerber('D10*X1000000Y1000000D02*G02X2000000Y1000000I500000J0D01*'),
                    b.gerber(b.flash(2, 2), '%ADD10C,1X0.3*%')]
        for text in examples:
            with self.subTest(text=text), self.assertRaises(c.Unsupported):
                c.render_gerber(text)

    def test_reject_omitted_drill_duplicate_pad_and_nonfinite(self):
        _, s, f, _ = b.cases()[0]
        for modify in ['drill', 'duplicate', 'nan', 'layers', 'count', 'inner', 'arbitrary_inner_name']:
            spec, files = copy.deepcopy(s), f.copy()
            if modify == 'drill':
                files['forgotten.drl'] = b.drill([(2, 5)], True)
            elif modify == 'duplicate':
                spec['pads'].append(copy.deepcopy(spec['pads'][0]))
            elif modify == 'nan':
                spec['pads'][0]['x'] = float('nan')
            elif modify == 'layers':
                spec['pads'][0]['layers'] = [3]
            elif modify in ('inner', 'arbitrary_inner_name'):
                files['inner.g5' if modify == 'inner' else 'unusual.data'] = b.gerber('%TF.FileFunction,Copper,L3,Inr*%' + b.flash(3, 3))
            else:
                spec['expectedPadCount'] = 100
            with self.subTest(modify=modify), self.assertRaises(c.Unsupported):
                self.audit(spec, files)

    def test_unknown_floating_copper_is_reported_not_net_guessed(self):
        _, spec, files, _ = b.cases()[-1]
        spec = copy.deepcopy(spec)
        spec['rejectUnassignedIslands'] = False
        report = self.audit(spec, files)
        self.assertTrue(report['passed'])
        self.assertTrue(report['unassignedIslands'])


if __name__ == '__main__':
    unittest.main()
