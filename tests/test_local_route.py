import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import plan_local_route as r
try:
    r.geometry_dependency()
    HAS_GEOMETRY = True
except r.Unsupported:
    HAS_GEOMETRY = False


def example():
    return {'schema': 'easyeda-local-route-input/v1', 'unit': 'mm', 'sourceRevision': 'synthetic-1',
            'net': 'POWER', 'fromPadId': 'J1.1', 'toPadId': 'J2.1', 'board': [[0, 0], [12, 0], [12, 8], [0, 8]],
            'width': 0.8, 'grid': 0.5, 'edgeClearance': 0.3, 'viaDiameter': 1.2, 'viaHole': 0.5, 'viaCost': 3,
            'viaDrillToSmdClearance': 0.2, 'viaHoleToHoleClearance': 0.2,
            'maxExpanded': 5000, 'clearanceByNet': {'OTHER': 0.4},
            'copper': [{'kind': 'pad', 'id': 'J1.1', 'net': 'POWER', 'layers': [1], 'x': 2, 'y': 4, 'shape': 'circle', 'width': 1.5, 'height': 1.5},
                       {'kind': 'pad', 'id': 'J2.1', 'net': 'POWER', 'layers': [1], 'x': 10, 'y': 4, 'shape': 'circle', 'width': 1.5, 'height': 1.5},
                       {'kind': 'track', 'id': 'barrier', 'net': 'OTHER', 'layers': [1], 'points': [[6, 0], [6, 8]], 'width': 0.8}],
            'holes': [], 'keepouts': []}


@unittest.skipUnless(HAS_GEOMETRY, 'Shapely 2.x unavailable')
class LocalRoute(unittest.TestCase):
    def test_crossing_uses_real_vias_and_preserves_width(self):
        result = r.plan(example())
        self.assertEqual(result['status'], 'CANDIDATE')
        self.assertEqual(len(result['vias']), 2)
        self.assertTrue(all(t['width'] == 0.8 for t in result['tracks']))
        self.assertFalse(result['widthFallbackUsed'])
        self.assertEqual(result['tracks'][0]['points'][0], [2, 4])
        self.assertEqual(result['tracks'][-1]['points'][-1], [10, 4])
        # Independent check: top segments never intersect physical foreign copper.
        from shapely.geometry import LineString, Point
        foreign = LineString([[6, 0], [6, 8]]).buffer(0.4)
        for t in result['tracks']:
            if t['layer'] == 1:
                self.assertGreaterEqual(LineString(t['points']).buffer(0.4).distance(foreign), 0.4 - 0.001)
        for v in result['vias']:
            self.assertGreaterEqual(Point(v['x'], v['y']).buffer(0.6).distance(foreign), 0.4 - 0.001)

    def test_two_face_barrier_does_not_narrow_or_delete(self):
        spec = example()
        spec['copper'][-1]['layers'] = [1, 2]
        before = copy.deepcopy(spec)
        result = r.plan(spec)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['tracks'], [])
        self.assertEqual(spec, before)

    def test_off_grid_smd_endpoint_and_opposite_face(self):
        spec = example()
        spec['copper'] = spec['copper'][:2]
        spec['copper'][0]['x'] = 2.13
        spec['copper'][1]['layers'] = [2]
        result = r.plan(spec)
        self.assertEqual(result['status'], 'CANDIDATE')
        self.assertEqual(len(result['vias']), 1)
        self.assertEqual(result['tracks'][0]['points'][0], [2.13, 4])
        self.assertEqual(result['tracks'][-1]['layer'], 2)

    def test_explicit_budget(self):
        spec = example()
        spec['maxExpanded'] = 1
        result = r.plan(spec)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['reason'], 'budget_exhausted')

    def test_new_via_pair_clearance_is_not_ignored(self):
        spec = example()
        spec['viaHoleToHoleClearance'] = 4
        result = r.plan(spec)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['reason'], 'via_pair_clearance')
        self.assertEqual(result['vias'], [])
        self.assertLess(result['conflict']['holeEdgeGap'], result['conflict']['requiredGap'])

    def test_npth_obstacle_on_both_faces(self):
        spec = example()
        spec['copper'] = spec['copper'][:2]
        spec['holes'] = [{'kind': 'circle', 'x': 6, 'y': 4, 'diameter': 3, 'clearance': 0.4, 'plated': False, 'net': None}]
        result = r.plan(spec)
        self.assertEqual(result['status'], 'CANDIDATE')
        from shapely.geometry import Point, LineString
        hole = Point(6, 4).buffer(1.5)
        for t in result['tracks']:
            self.assertGreaterEqual(LineString(t['points']).buffer(0.4).distance(hole), 0.4 - 0.001)

    def test_reject_invalid_same_net_terminal_geometry(self):
        spec = example()
        spec['copper'][0].update(shape='nonsense', width=-1, height=-1)
        with self.assertRaises(r.Unsupported):
            r.plan(spec)

    def test_new_drills_avoid_own_smd_and_existing_holes(self):
        from shapely.geometry import Point
        spec = example()
        spec['copper'] = spec['copper'][:2]
        spec['copper'][1]['layers'] = [2]
        spec['holes'] = [{'kind': 'circle', 'x': 6, 'y': 4, 'diameter': 0.3, 'clearance': 0, 'plated': True, 'net': 'POWER'}]
        result = r.plan(spec)
        self.assertEqual(result['status'], 'CANDIDATE')
        for v in result['vias']:
            drill = Point(v['x'], v['y']).buffer(v['hole'] / 2)
            for pad in spec['copper']:
                self.assertGreater(drill.distance(Point(pad['x'], pad['y']).buffer(0.75)), 0.2)
            self.assertGreater(drill.distance(Point(6, 4).buffer(0.15)), 0.2)


if __name__ == '__main__':
    unittest.main()
