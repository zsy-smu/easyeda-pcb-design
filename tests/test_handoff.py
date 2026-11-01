"""Archive identity, real native-report parsing and fail-closed coverage tests."""
import importlib.util
import io
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
SCRIPT=Path(__file__).resolve().parents[1]/'scripts/check_project_handoff.py'
spec=importlib.util.spec_from_file_location('handoff',SCRIPT)
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)

def lines(rr):return '\n'.join(json.dumps(r)for r in rr).encode()
def fixture(folder,multi=False,copy_fp=False):
    pr={'boards':{'BOARD':{'pcb':'p','schematic':'s'}},'pcbs':{'p':'PCB1'},'schematics':{'s':{}},
        'devices':{'d':{'attributes':{'Symbol':'sym','Footprint':'f'}}},'symbols':{'sym':{}},'footprints':{'f':{}}}
    pcb=[['DOCTYPE','PCB','1.8'],['COMPONENT','c',0,1,0,0,0,{'Unique ID':'uid','Channel ID':'$1s1'},0]]
    pcb +=[['ATTR','a1',0,'c',3,None,None,'Designator','U1'],['ATTR','a2',0,'c',3,None,None,'Device','d'],
           ['ATTR','a3',0,'c',3,None,None,'Footprint','f2'if copy_fp else'f']]
    sch=[['DOCTYPE','SCH','1.1']]
    for i in range(1,3 if multi else 2):
        sid='s'+str(i);sch.append(['COMPONENT',sid,'CHIP.'+str(i),0,0,0,0,{},0])
        for key,value in [('Designator','U1'),('Unique ID','uid'),('Device','d'),('Symbol','sym')]:
            sch.append(['ATTR',sid+key,sid,key,value])
    fp=lines([['DOCTYPE','FOOTPRINT','1.8']])
    data={'project.json':json.dumps(pr).encode(),'PCB/p.epcb':lines(pcb),'SHEET/s/1.esch':lines(sch),'FOOTPRINT/f.efoo':fp,
          'SYMBOL/sym.esym':lines([['DOCTYPE','SYMBOL','1.1']])}
    if copy_fp:pr['footprints']['f2']={};data['project.json']=json.dumps(pr).encode();data['FOOTPRINT/f2.efoo']=fp
    return data

def write(folder,data):
    p=Path(folder)/'project.epro'
    with zipfile.ZipFile(p,'w')as z:
        for n,b in data.items():z.writestr(n,b)
    return p

class HandoffTests(unittest.TestCase):
    def test_zero_board_coverage_cannot_pass(self):
        with tempfile.TemporaryDirectory()as td:
            data=fixture(td);pr=json.loads(data['project.json']);pr['boards']={};pr['pcbs']={};data['project.json']=json.dumps(pr).encode()
            with self.assertRaises(h.InputError):h.audit(write(td,data))

    def test_symbol_source_and_reference_checked(self):
        with tempfile.TemporaryDirectory()as td:
            data=fixture(td);del data['SYMBOL/sym.esym']
            with self.assertRaises(h.InputError):h.audit(write(td,data))
            data=fixture(td);rr=[json.loads(l)for l in data['SHEET/s/1.esch'].decode().splitlines()]
            next(r for r in rr if r[0]=='ATTR'and r[3]=='Symbol')[4]='unresolved'
            data['SHEET/s/1.esch']=lines(rr)
            self.assertFalse(h.audit(write(td,data))['passed'])

    def test_schematic_only_instances_not_required_in_pcb(self):
        with tempfile.TemporaryDirectory()as td:
            for inheritance in [False,True]:
                data=fixture(td);pr=json.loads(data['project.json']);pr['devices']['d2']={'attributes':{'Symbol':'sym','Convert to PCB':'no'if inheritance else'yes'}};data['project.json']=json.dumps(pr).encode()
                extra=[['COMPONENT','s2','NONPHYSICAL.1',0,0,0,0,{},0]]
                for k,v in [('Designator','X1'),('Unique ID','virtual'),('Device','d2'),('Symbol','sym')]:extra.append(['ATTR','s2'+k,'s2',k,v])
                if not inheritance:extra.append(['ATTR','s2convert','s2','Convert to PCB','no'])
                data['SHEET/s/1.esch']+=b'\n'+lines(extra)
                r=h.audit(write(td,data));self.assertTrue(r['passed']);self.assertEqual(['X1'],r['boards']['BOARD']['schematicOnlyDesignators'])

    def test_real_library_empty_placeholders_supported(self):
        with tempfile.TemporaryDirectory()as td:
            data=fixture(td);data['FOOTPRINT/f.efoo']+=b'\n[]'
            self.assertTrue(h.audit(write(td,data))['passed'])

    def test_valid_group_accepts_primary_channel_without_fake_native_pass(self):
        with tempfile.TemporaryDirectory()as td:
            r=h.audit(write(td,fixture(td,multi=True)))
            self.assertTrue(r['passed']);self.assertFalse(r['nativeRoundTripVerified'])
            self.assertEqual(1,r['boards']['BOARD']['pcbComponents'])
            self.assertEqual(2,len(r['boards']['BOARD']['multiUnitGroups']['uid']))

    def test_second_symbol_cannot_overwrite_first_and_legitimate_second_channel_supported(self):
        with tempfile.TemporaryDirectory()as td:
            data=fixture(td,True);rr=[json.loads(l)for l in data['PCB/p.epcb'].decode().splitlines()];rr[1][7]['Channel ID']='$1s2';data['PCB/p.epcb']=lines(rr)
            self.assertTrue(h.audit(write(td,data))['passed'])

    def test_repeated_same_unit_is_not_accepted_as_multi_unit(self):
        with tempfile.TemporaryDirectory()as td:
            data=fixture(td,True);data['SHEET/s/1.esch']=data['SHEET/s/1.esch'].replace(b'CHIP.2',b'CHIP.1')
            r=h.audit(write(td,data));self.assertIn('INVALID_MULTI_UNIT_GROUP',[v['code']for v in r['issues']])

    def test_equal_footprint_bytes_do_not_erase_reference_mismatch(self):
        with tempfile.TemporaryDirectory()as td:
            r=h.audit(write(td,fixture(td,copy_fp=True)))
            self.assertFalse(r['passed']);self.assertTrue(r['boards']['BOARD']['footprints'][0]['byteIdentical'])

    def test_different_footprint_content_not_called_equal(self):
        with tempfile.TemporaryDirectory()as td:
            data=fixture(td,copy_fp=True);data['FOOTPRINT/f2.efoo']+=b'\n["PAD","p1"]'
            r=h.audit(write(td,data));self.assertFalse(r['boards']['BOARD']['footprints'][0]['byteIdentical'])

    def test_orphan_attributes_are_not_schematic_instances(self):
        with tempfile.TemporaryDirectory()as td:
            data=fixture(td);data['SHEET/s/1.esch']+=b'\n["ATTR","orphan","removed","Designator","U9"]'
            self.assertTrue(h.audit(write(td,data))['passed'])

    def test_missing_library_and_bad_channel_detected(self):
        with tempfile.TemporaryDirectory()as td:
            data=fixture(td);rr=[json.loads(l)for l in data['PCB/p.epcb'].decode().splitlines()];rr[1][7]['Channel ID']='$1other';rr[-1][-1]='missing';data['PCB/p.epcb']=lines(rr)
            codes={v['code']for v in h.audit(write(td,data))['issues']}
            self.assertIn('CHANNEL_NOT_IN_SCHEMATIC_UNITS',codes);self.assertIn('LIBRARY_REFERENCE_MISSING',codes)

    def test_outer_delivery_zip_not_native_project(self):
        with tempfile.TemporaryDirectory()as td:
            with self.assertRaises(h.InputError):h.audit(write(td,{'Native/other.epro':b'irrelevant'}))

    def test_unsupported_version_does_not_become_empty_pass(self):
        with tempfile.TemporaryDirectory()as td:
            data=fixture(td);data['PCB/p.epcb']=b'["DOCTYPE","PCB","3.0"]'
            with self.assertRaises(h.InputError):h.audit(write(td,data))

    def test_native_utf16_tab_grouping_and_multiline_fields(self):
        with tempfile.TemporaryDirectory()as td:
            s=io.StringIO();w=csv.writer(s,delimiter='\t');w.writerow(['变更','动作','对象','导入前','导入后']);w.writerow([]);w.writerow(['U1','','','','']);w.writerow(['✓','新增属性','U1:描述','','line1\nline2'])
            f=Path(td)/'report.csv';f.write_bytes(s.getvalue().encode('utf16'))
            r=h.read_changes(f);self.assertEqual(1,len(r['actions']));self.assertEqual('line1\nline2',r['actions'][0]['after'])
            r=h.audit(write(td,fixture(td)),{'BOARD':f});self.assertFalse(r['passed']);self.assertEqual(1,r['nativeImportReports']['BOARD']['actionCount'])

    def test_unknown_action_rejected_and_structural_changes_visible(self):
        with tempfile.TemporaryDirectory()as td:
            f=Path(td)/'r.csv'
            for action in ['删除元件','未知动作']:
                f.write_text('变更,动作,对象,导入前,导入后\nU1,,,,\n✓,'+action+',U1,U1,-\n',encoding='utf8')
                if action=='未知动作':
                    with self.assertRaises(h.InputError):h.read_changes(f)
                else:
                    r=h.audit(write(td,fixture(td)),{'BOARD':f});self.assertEqual(1,r['nativeImportReports']['BOARD']['classificationCounts']['structural-or-rules'])

    def test_cli_source_alias_is_refused(self):
        with tempfile.TemporaryDirectory()as td:
            f=write(td,fixture(td));before=f.read_bytes()
            r=subprocess.run([sys.executable,str(SCRIPT),str(f),'--out',str(f)],capture_output=True,text=True)
            self.assertEqual(2,r.returncode);self.assertEqual(before,f.read_bytes())

if __name__=='__main__':unittest.main()
