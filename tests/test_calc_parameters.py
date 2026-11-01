"""Electrical decision boundaries; no fixture values promoted to universal rules."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
SCRIPT=Path(__file__).resolve().parents[1]/'scripts/calc_parameters.py'
spec=importlib.util.spec_from_file_location('calc_parameters',SCRIPT)
calc=importlib.util.module_from_spec(spec);spec.loader.exec_module(calc)

def targets(nets):
    return {'schema':'param-targets/v1','copper':{'weightOz':1},'riseLimitC':10,'networks':nets}

class CalcTests(unittest.TestCase):
    def test_steady_estimate_increases_with_current_and_decreases_with_copper(self):
        small=calc.ipc2221_trace_width_mm(1,10,1)
        big=calc.ipc2221_trace_width_mm(5,10,1)
        self.assertGreater(big,small)
        self.assertAlmostEqual(big/2,calc.ipc2221_trace_width_mm(5,10,2))

    def test_project_minimum_cannot_reduce_steady_estimate(self):
        n={'name':'BUS','class':'power','currentA':10,'minTraceWidthMm':.8}
        r=calc.build_spec(targets([n]))['networks'][0]
        self.assertGreater(r['traceWidthMm'],.8)
        self.assertGreaterEqual(r['traceWidthMm'],r['rawIpcWidthMm'])

    def test_peak_current_does_not_become_fake_four_mm_qualification(self):
        r=calc.build_spec(targets([{'name':'P','class':'pulse','currentA':70}]))
        n=r['networks'][0]
        self.assertIsNone(n['traceWidthMm']);self.assertIsNone(n['rawIpcWidthMm'])
        self.assertFalse(n['openSolder']);self.assertTrue(r['reviewRequired'])

    def test_explicit_pulse_candidate_is_still_unqualified(self):
        n={'name':'P','class':'pulse','currentA':70,'traceWidthMm':6,'widthBasis':'candidate waveform calculation'}
        r=calc.build_spec(targets([n]))['networks'][0]
        self.assertEqual(6,r['traceWidthMm']);self.assertTrue(r['reviewRequired'])

    def test_board_thickness_does_not_invent_differential_geometry(self):
        t=targets([{'name':'D','class':'differential'}]);t['copper']['boardThicknessMm']=1.6
        r=calc.build_spec(t)
        self.assertIsNone(r['networks'][0]['traceWidthMm']);self.assertIsNone(r['networks'][0]['gapMm'])
        self.assertFalse(r['differential']['impedanceSolved'])

    def test_clearance_is_not_creepage_and_no_unchecked_table_extrapolation(self):
        for voltage in [5,60,200,500,501,1000]:
            with self.assertRaises(calc.InputError):calc.ipc_clearance_mm(voltage)
        n={'name':'V','class':'hv','currentA':.1,'voltageV':200,'clearanceMm':3,'clearanceBasis':'board requirements'}
        r=calc.build_spec(targets([n]))['networks'][0]
        self.assertEqual(3,r['clearanceMm']);self.assertIsNone(r['creepageMm'])

    def test_unknown_current_not_assumed_zero(self):
        n=calc.build_spec(targets([{'name':'P','class':'power'}]))['networks'][0]
        self.assertIsNone(n['currentA']);self.assertIsNone(n['traceWidthMm'])

    def test_opto_does_not_automatically_create_slot(self):
        t=targets([{'name':'V','class':'signal'}]);t['optocouplers']=[{'name':'U1'}]
        r=calc.build_spec(t);self.assertEqual([],r['cutouts']);self.assertTrue(r['reviewRequired'])

    def test_negative_nonfinite_and_zero_divisors_rejected(self):
        for n in [-1,float('inf'),float('nan'),True]:
            with self.assertRaises(calc.InputError):calc.ipc2221_trace_width_mm(n,10,1)
        for rise,oz in [(0,1),(10,0)]:
            with self.assertRaises(calc.InputError):calc.ipc2221_trace_width_mm(1,rise,oz)

    def test_duplicate_names_rejected(self):
        with self.assertRaises(calc.InputError):calc.build_spec(targets([{'name':'A','class':'signal'}]*2))

    def test_cli_example_is_flagged_and_source_is_protected(self):
        with tempfile.TemporaryDirectory()as td:
            out=Path(td)/'report.json'
            p=subprocess.run([sys.executable,str(SCRIPT),'--out',str(out)],capture_output=True,text=True)
            self.assertEqual(1,p.returncode,p.stderr)
            self.assertTrue(json.loads(out.read_text())['exampleOnly'])
            src=Path(td)/'targets.json';src.write_text(json.dumps(calc.PRESET));original=src.read_bytes()
            p=subprocess.run([sys.executable,str(SCRIPT),str(src),'--out',str(src)],capture_output=True,text=True)
            self.assertEqual(2,p.returncode);self.assertEqual(original,src.read_bytes())

if __name__=='__main__':unittest.main()
