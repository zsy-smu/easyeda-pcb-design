import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'check_manufacturing.py'
SPEC = importlib.util.spec_from_file_location('manufacturing_checker', SCRIPT)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
try:
    m.require_geometry()
    HAS_GEOMETRY = True
except m.InputError:
    HAS_GEOMETRY = False


def expected(layers=2):
    # The notched 20 x 12 board and 31 x 18 board share no project fixture.
    width, height = (20, 12) if layers == 2 else (31, 18)
    return {'schema': 'easyeda-manufacturing-expected/v1', 'board': {'widthMm': width, 'heightMm': height, 'expectedCopperLayers': layers, 'outlinePointsMm': [[0, 0], [6, 0], [6, 2], [9, 2], [9, 0], [width, 0], [width, height], [0, height], [0, 0]]},
            'holes': [{'id': 'v1', 'kind': 'via', 'shape': 'ROUND', 'xMm': 3, 'yMm': 4, 'diameterMm': 0.3, 'totalLengthMm': 0.3, 'rotationCanvasDeg': 0, 'plated': True},
                      {'id': 'p1', 'shape': 'ROUND', 'xMm': 10, 'yMm': 5, 'diameterMm': 1, 'totalLengthMm': 1, 'rotationCanvasDeg': 0, 'plated': True},
                      {'id': 's1', 'shape': 'SLOT', 'xMm': 15, 'yMm': 8, 'diameterMm': 1, 'totalLengthMm': 2, 'rotationCanvasDeg': 45, 'plated': True}]}


def outline(manifest):
    points = manifest['board']['outlinePointsMm']
    coordinates = [f'X{round((x + 7) * 1e6)}Y{round((40 - y) * 1e6)}D{2 if i == 0 else 1:02d}*' for i, (x, y) in enumerate(points)]
    return '%FSLAX46Y46*%\n%MOMM*%\n%TF.FileFunction,Profile,NP*%\n%ADD10C,0.1*%\nD10*\nG01*\n' + '\n'.join(coordinates) + '\nM02*'


def copper(layer, count):
    position = 'Top' if layer == 1 else 'Bot' if layer == count else 'Inr'
    return f'%FSLAX46Y46*%%MOMM*%%TF.FileFunction,Copper,L{layer},{position}*%%ADD10C,0.3*%D10*X10000000Y36000000D03*M02*'


def drill():
    delta = 0.5 / math.sqrt(2)
    return f'M48\n;TYPE=PLATED\nMETRIC,LZ,000.000\nT01C0.300\nT02C1.000\n%\nG05\nG90\nT01\nX10.000Y36.000\nT02\nX17.000Y35.000\nX{22-delta:.6f}Y{32+delta:.6f}G85X{22+delta:.6f}Y{32-delta:.6f}\nM30\n'


def fixture(manifest):
    count = manifest['board']['expectedCopperLayers']
    return {'outline.gko': outline(manifest), 'drill.drl': drill(), **{f'copper-{layer}.gbr': copper(layer, count) for layer in range(1, count + 1)}}


def write_zip(path, files):
    with zipfile.ZipFile(path, 'w') as archive:
        for name, text in files.items():
            archive.writestr(name, text)


@unittest.skipUnless(HAS_GEOMETRY, 'Shapely >=2,<3 unavailable; parser/CLI dependency tests still run')
class GeometryChecks(unittest.TestCase):
    def run_check(self, mutate=None, layers=2, manifest_mutate=None):
        manifest = expected(layers)
        files = fixture(manifest)
        if mutate:
            mutate(files)
        if manifest_mutate:
            manifest_mutate(manifest)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'export.zip'
            write_zip(path, files)
            return m.audit(path, manifest)

    def test_two_layer_small_board(self):
        result = self.run_check()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['drills']['matched'], 3)
        self.assertTrue(result['drills']['platingClassification']['verified'])
        self.assertFalse(result['nativeSaveVerified'])

    def test_four_layer_different_board(self):
        result = self.run_check(layers=4)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['outline']['sizeMm'], [31, 18])

    def test_six_layers(self):
        self.assertTrue(self.run_check(layers=6)['passed'])

    def test_missing_layer(self):
        self.assertFalse(self.run_check(lambda f: f.pop('copper-2.gbr'))['copperLayers']['passed'])

    def test_open_outline(self):
        result = self.run_check(lambda f: f.update({'outline.gko': f['outline.gko'].replace('X7000000Y40000000D01*', '')}))
        self.assertFalse(result['outline']['passed'])

    def test_changed_notch(self):
        self.assertFalse(self.run_check(lambda f: f.update({'outline.gko': f['outline.gko'].replace('Y38000000', 'Y37500000')}))['outline']['passed'])

    def test_clear_outline(self):
        self.assertFalse(self.run_check(lambda f: f.update({'outline.gko': f['outline.gko'].replace('D10*', '%LPC*%D10*')}))['outline']['passed'])

    def test_shifted_hole(self):
        self.assertFalse(self.run_check(lambda f: f.update({'drill.drl': f['drill.drl'].replace('X10.000Y36.000', 'X10.010Y36.000')}))['drills']['passed'])

    def test_missing_hole(self):
        self.assertFalse(self.run_check(lambda f: f.update({'drill.drl': f['drill.drl'].replace('X17.000Y35.000\n', '')}))['drills']['passed'])

    def test_extra_hole(self):
        self.assertFalse(self.run_check(lambda f: f.update({'drill.drl': f['drill.drl'].replace('M30', 'X18.000Y35.000\nM30')}))['drills']['passed'])

    def test_duplicate_hole(self):
        result = self.run_check(lambda f: f.update({'drill.drl': f['drill.drl'].replace('X10.000Y36.000', 'X10.000Y36.000\nX10.000Y36.000')}))
        self.assertFalse(result['drills']['passed'])
        self.assertEqual(result['drills']['actual'], 4)
        self.assertTrue(result['drills']['ambiguous'])

    def test_wrong_diameter(self):
        self.assertFalse(self.run_check(lambda f: f.update({'drill.drl': f['drill.drl'].replace('C0.300', 'C0.500')}))['drills']['passed'])

    def test_slot_rotation(self):
        self.assertFalse(self.run_check(manifest_mutate=lambda e: e['holes'][2].update(rotationCanvasDeg=0))['drills']['passed'])

    def test_plating_mismatch_fails(self):
        result = self.run_check(lambda f: f.update({'drill.drl': f['drill.drl'].replace(';TYPE=PLATED', ';TYPE=NON_PLATED')}))
        self.assertFalse(result['passed'])
        self.assertEqual(result['drills']['platingClassification']['status'], 'MISMATCH')

    def test_unknown_plating_never_verified(self):
        result = self.run_check(lambda f: f.update({'drill.drl': f['drill.drl'].replace(';TYPE=PLATED\n', '')}))
        self.assertTrue(result['passed'])
        self.assertFalse(result['drills']['platingClassification']['verified'])
        self.assertEqual(len(result['drills']['platingClassification']['unknown']), 3)

    def test_unsupported_commands_raise(self):
        for filename, command in [('drill.drl', 'G91\n'), ('outline.gko', '%LS2*%'), ('copper-1.gbr', '%IPNEG*%')]:
            with self.subTest(filename=filename), self.assertRaises(m.InputError):
                self.run_check(lambda f: f.update({filename: command + f[filename]}))

    def test_missing_unit_rejected(self):
        with self.assertRaises(m.InputError):
            self.run_check(lambda f: f.update({'drill.drl': f['drill.drl'].replace('METRIC,LZ,000.000\n', '')}))

    def test_zero_drill_rejected(self):
        with self.assertRaisesRegex(m.InputError, 'zero-drill'):
            self.run_check(manifest_mutate=lambda e: e.update(holes=[]))

    def test_duplicate_expected_hole_rejected(self):
        with self.assertRaisesRegex(m.InputError, 'Duplicate expected'):
            self.run_check(manifest_mutate=lambda e: e['holes'].append(dict(e['holes'][0])))

    def test_duplicate_zip_entries_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'duplicate.zip'
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                with zipfile.ZipFile(path, 'w') as archive:
                    archive.writestr('same.gbr', 'M02*')
                    archive.writestr('same.gbr', 'M02*')
            with self.assertRaisesRegex(m.InputError, 'Duplicate ZIP'):
                m.audit(path, expected())

    def test_supplied_evidence_does_not_claim_save(self):
        result = self.run_check(manifest_mutate=lambda e: e.update(provenance={'source': 'fixture'}, reviewEvidence={'saved': True}))
        self.assertFalse(result['nativeSaveVerified'])
        self.assertEqual(result['suppliedEvidence']['values']['provenance']['source'], 'fixture')

    def subset_files(self, files, damage=False):
        files['Drill_PTH_Through.DRL'] = ';Layer: PTH_Through\n' + files.pop('drill.drl')
        files['Drill_PTH_Through_Via.DRL'] = ';Layer: PTH_Through_Via\nM48\n;TYPE=PLATED\nMETRIC,LZ,000.000\nT01C0.300\n%\nT01\nX10.000Y36.000\n' + ('X10.000Y36.000\n' if damage else '') + 'M30\n'

    def test_proven_subset_role(self):
        result = self.run_check(self.subset_files)
        self.assertTrue(result['passed'])
        self.assertEqual(result['drills']['fileRoles']['status'], 'FULL_PTH_AND_PROVEN_VIA_SUBSET')
        self.assertEqual(result['drills']['fileRoles']['rawFeatureOccurrences'], 4)
        self.assertEqual(result['drills']['actual'], 3)

    def test_duplicate_in_subset_cannot_be_deduplicated(self):
        result = self.run_check(lambda f: self.subset_files(f, damage=True))
        self.assertFalse(result['passed'])
        self.assertEqual(result['drills']['actual'], 5)

    def test_subset_requires_expected_plating_and_via_kind(self):
        result = self.run_check(self.subset_files, manifest_mutate=lambda e: e['holes'][0].pop('kind'))
        self.assertFalse(result['passed'])
        self.assertFalse(result['drills']['fileRoles']['roleProofPassed'])

    def test_custom_declared_subset_role(self):
        def mutate(files):
            self.subset_files(files)
            files['all-pth.drl'] = files.pop('Drill_PTH_Through.DRL')
            files['vias.drl'] = files.pop('Drill_PTH_Through_Via.DRL')
        result = self.run_check(mutate, manifest_mutate=lambda e: e.update(drillFileRoles={'fullPTH': 'all-pth.drl', 'viaSubset': 'vias.drl'}))
        self.assertTrue(result['passed'])

    def test_cli_exit_codes_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source, manifest, report = directory / 'input.zip', directory / 'expected.json', directory / 'report.json'
            manifest.write_text(json.dumps(expected()), encoding='utf-8')
            for case, expected_code in [('pass', 0), ('fail', 1), ('error', 2)]:
                files = fixture(expected())
                if case == 'fail':
                    files.pop('copper-2.gbr')
                elif case == 'error':
                    files['drill.drl'] = 'G91\n' + files['drill.drl']
                write_zip(source, files)
                result = subprocess.run([sys.executable, str(SCRIPT), str(source), '--expected', str(manifest), '--out', str(report)], capture_output=True, text=True)
                self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
                self.assertEqual(json.loads(report.read_text())['status'], {'pass': 'PASS', 'fail': 'FAIL', 'error': 'ERROR'}[case])


class ParserAndErrorChecks(unittest.TestCase):
    def test_nonfinite_provenance_json_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'expected.json'
            for value in ('NaN', 'Infinity', '-Infinity'):
                with self.subTest(value=value):
                    source.write_text('{"provenance":{"value":' + value + '}}', encoding='utf-8')
                    with self.assertRaisesRegex(m.InputError, 'Nonfinite JSON'):
                        m.load_expected(source)

    def test_multiquadrant_gerber_arc(self):
        text = '%FSLAX46Y46*%%MOMM*%%ADD10C,0.1*%D10*G75*X1000000Y0D02*G03X0Y1000000I-1000000J0D01*M02*'
        result = m.parse_gerber(text)
        self.assertFalse(result['unknown'])
        self.assertEqual(result['paths'][0]['points'][-1], [0, 1])
        self.assertGreater(len(result['paths'][0]['points']), 2)

    def test_strict_gerber_unknown_and_malformed_commands(self):
        base = copper(1, 2)
        mutations = [
            '%AMbad*99,1,2,3*%' + base,
            base.replace('ADD10C,0.3', 'ADD10Unknown,0.3'),
            base.replace('M02*', 'D04*M02*'),
            base.replace('M02*', 'M02'),
            base + '%',
            base.replace('%MOMM*%', '%MOMM*%%MOIN*%'),
        ]
        for mutated in mutations:
            with self.subTest(mutated=mutated):
                self.assertTrue(m.parse_gerber(mutated)['unknown'])

    def test_cli_does_not_overwrite_input(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'expected.json'
            source.write_text(json.dumps(expected()), encoding='utf-8')
            original = source.read_bytes()
            result = subprocess.run([sys.executable, str(SCRIPT), 'missing.zip', '--expected', str(source), '--out', str(source)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(source.read_bytes(), original)
            self.assertIn('overwrite', json.loads(result.stdout)['error']['message'])

    def test_cli_does_not_overwrite_hardlinked_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            manifest, export = directory / 'expected.json', directory / 'export.zip'
            manifest.write_text(json.dumps(expected()), encoding='utf-8')
            write_zip(export, fixture(expected()))
            for source in (manifest, export):
                with self.subTest(source=source.name):
                    alias = directory / ('alias-' + source.name)
                    os.link(source, alias)
                    original = source.read_bytes()
                    result = subprocess.run([sys.executable, str(SCRIPT), str(export), '--expected', str(manifest), '--out', str(alias)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(source.read_bytes(), original)
                    self.assertIn('overwrite', json.loads(result.stdout)['error']['message'])

    def test_inch_implicit_format(self):
        result = m.parse_excellon('M48\nINCH,LZ,00.0000\nT01C0.0100\n%\nT01\nX10000Y20000\nM30')
        self.assertFalse(result['unknown'])
        self.assertAlmostEqual(result['features'][0]['a'][0], 25.4)

    def test_unknown_coordinate_format(self):
        self.assertTrue(m.parse_excellon('M48\nMETRIC\nT01C0.3\n%\nT01\nX100Y200\nM30')['unknown'])

    def test_g85_modal_endpoint(self):
        result = m.parse_excellon('M48\nMETRIC,LZ,000.000\nT01C1\n%\nT01\nX10.0Y20.0G85X12.0\nM30')
        self.assertFalse(result['unknown'])
        self.assertEqual(result['features'][0]['b'], [12, 20])

    def test_slot_route_mode(self):
        result = m.parse_excellon('M48\nMETRIC,LZ,000.000\nT01C1\n%\nT01\nG00X10.0Y20.0\nM15\nG01X12.0Y20.0\nM16\nM30')
        self.assertFalse(result['unknown'])
        self.assertEqual(result['features'][0]['shape'], 'SLOT')

    def test_unknown_unit_suffix(self):
        self.assertTrue(m.parse_excellon('M48\nMETRIC,BOGUS\nM30')['unknown'])

    def test_changed_units(self):
        self.assertTrue(m.parse_excellon('M48\nMETRIC\nINCH\nM30')['unknown'])

    def test_g85_junk_rejected(self):
        self.assertTrue(m.parse_excellon('M48\nMETRIC\nT01C1\n%\nT01\nX1.0Y2.0BOGUSG85X3.0Y4.0\nM30')['unknown'])

    def test_bad_zip_json_error(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'report.json'
            result = subprocess.run([sys.executable, str(SCRIPT), 'missing.zip', '--expected', 'missing.json', '--out', str(out)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(out.read_text())['status'], 'ERROR')

    def test_dependency_missing_has_machine_readable_error(self):
        # -S disables site and an isolated launch ignores any inherited PYTHONPATH.
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            manifest = directory / 'expected.json'
            manifest.write_text(json.dumps(expected()))
            report = directory / 'out.json'
            result = subprocess.run([sys.executable, '-I', '-S', str(SCRIPT), str(directory / 'missing.zip'), '--expected', str(manifest), '--out', str(report)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('Shapely', json.loads(report.read_text())['error']['message'])


if __name__ == '__main__':
    unittest.main()
