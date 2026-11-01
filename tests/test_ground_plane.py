import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_ground_plane as b
try:
    b.geometry_dependency()
    HAS_GEOMETRY = True
except b.Unsupported:
    HAS_GEOMETRY = False


def example():
    return {'schema': 'easyeda-ground-input/v1', 'unit': 'mm', 'sourceRevision': 'synthetic-1', 'net': 'GND',
            'board': [[0, 0], [20, 0], [20, 12], [0, 12]], 'edgeClearance': 0.5,
            'minBridgeWidth': 0.3, 'minIslandArea': 0.01, 'clearanceByNet': {'5V': 0.4, 'NC': 0.4},
            'anchorPadIds': ['U1.GND'],
            'copper': [{'id': 'U1.GND', 'kind': 'pad', 'net': 'GND', 'layers': [1], 'x': 2, 'y': 3,
                        'width': 1, 'height': 1, 'shape': 'rect'},
                       {'id': 'J1.5V', 'kind': 'circle', 'net': '5V', 'layers': [1, 2], 'x': 8, 'y': 6, 'diameter': 2}],
            'holes': [], 'keepouts': [{'kind': 'circle', 'x': 14, 'y': 6, 'diameter': 3, 'clearance': 0.2}]}


@unittest.skipUnless(HAS_GEOMETRY, 'Shapely 2.x unavailable')
class GroundPlane(unittest.TestCase):
    def union(self, r, layer):
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        return unary_union([Polygon(f['points']) for f in r['fills'] if f['layer'] == layer])

    def test_void_foreign_clearance_and_anchor(self):
        from shapely.geometry import Point
        spec = example()
        r = b.build(spec)
        top, bottom = self.union(r, 1), self.union(r, 2)
        self.assertEqual(r['status'], 'CANDIDATE')
        self.assertTrue(top.contains(Point(2, 3)))
        self.assertFalse(top.contains(Point(8, 6)))
        self.assertFalse(top.contains(Point(14, 6)))
        self.assertTrue(bottom.is_empty)  # no plated path to an actual anchor
        self.assertGreaterEqual(top.distance(Point(8, 6)), 1.4 - 0.001)
        self.assertGreaterEqual(top.bounds[0], 0.5 - 1e-8)
        self.assertAlmostEqual(top.area, r['areaMm2ByLayer']['1'])

    def test_floating_ground_via_does_not_seed_plane(self):
        spec = example()
        spec['keepouts'].append({'kind': 'polygon', 'points': [[9, 0], [11, 0], [11, 12], [9, 12]], 'clearance': 0})
        spec['copper'].append({'id': 'floating', 'kind': 'circle', 'net': 'GND', 'layers': [1, 2], 'x': 17, 'y': 2, 'diameter': 1})
        from shapely.geometry import Point
        self.assertFalse(self.union(b.build(spec), 1).contains(Point(17, 2)))

    def test_only_plated_target_hole_can_join_layers(self):
        spec = example()
        spec['copper'][0]['layers'] = [1, 2]
        for plated in (True, False):
            spec['holes'] = [{'kind': 'circle', 'x': 2, 'y': 3, 'diameter': 0.3, 'plated': plated, 'net': 'GND', 'clearance': 0}]
            r = b.build(spec)
            # Anchor contract explicitly lists this physical pad on both faces,
            # so either face is seeded. Test through a separate via instead.
            self.assertTrue(r['fills'])
        spec['copper'][0]['layers'] = [1]
        spec['copper'].append({'id': 'via', 'kind': 'circle', 'net': 'GND', 'layers': [1, 2], 'x': 3, 'y': 3, 'diameter': 0.7})
        for plated in (True, False):
            spec['holes'] = [{'kind': 'circle', 'x': 3, 'y': 3, 'diameter': 0.3, 'plated': plated, 'net': 'GND', 'clearance': 0}]
            self.assertEqual(not self.union(b.build(spec), 2).is_empty, plated)

    def test_hole_partition_is_area_equivalent(self):
        from shapely.geometry import box
        from shapely.ops import unary_union
        g = box(0, 0, 20, 20).difference(box(2, 2, 4, 4).union(box(7, 7, 10, 10)))
        rows = b.holefree(g)
        self.assertTrue(all(not p.interiors for p in rows))
        self.assertLess(g.symmetric_difference(unary_union(rows)).area, 1e-10)

    def test_npth_slot_cuts_nominal_ground_track(self):
        from shapely.geometry import Point
        spec = example()
        spec['copper'] = [spec['copper'][0], {'id': 'ground-track', 'kind': 'track', 'net': 'GND', 'layers': [1], 'width': 0.8, 'points': [[2, 3], [18, 3]]}]
        spec['keepouts'] = []
        spec['holes'] = [{'kind': 'track', 'points': [[10, -1], [10, 13]], 'width': 2, 'plated': False, 'net': None, 'clearance': 0}]
        candidate = self.union(b.build(spec), 1)
        self.assertTrue(candidate.contains(Point(2, 3)))
        self.assertFalse(candidate.contains(Point(18, 3)))

    def test_negative_hole_clearance_rejected(self):
        spec = example()
        spec['holes'] = [{'kind': 'circle', 'x': 5, 'y': 4, 'diameter': 1, 'plated': False, 'net': None, 'clearance': -0.6}]
        with self.assertRaises(b.Unsupported):
            b.build(spec)

    def test_nc_plated_hole_is_foreign_obstacle(self):
        from shapely.geometry import Point
        spec = example()
        spec['holes'] = [{'kind': 'circle', 'x': 5, 'y': 4, 'diameter': 1, 'plated': True, 'net': None, 'clearance': 0}]
        top = self.union(b.build(spec), 1)
        self.assertFalse(top.contains(Point(5, 4)))
        self.assertGreaterEqual(top.distance(Point(5, 4)), 0.9 - 0.001)

    def test_missing_clearance_fake_anchor_nonfinite_rejected(self):
        for change in ['clearance', 'anchor', 'nan', 'negative']:
            s = example()
            if change == 'clearance':
                s['clearanceByNet'].pop('5V')
            elif change == 'anchor':
                s['anchorPadIds'] = ['J1.5V']
            elif change == 'nan':
                s['board'][0][0] = float('nan')
            else:
                s['edgeClearance'] = -1
            with self.subTest(change=change), self.assertRaises(b.Unsupported):
                b.build(s)


if __name__ == '__main__':
    unittest.main()
