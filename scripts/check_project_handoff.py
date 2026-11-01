#!/usr/bin/env python3
"""Read-only legacy EPRO identity/footprint audit and native import-change review.

Supports PCB/FOOTPRINT 1.8 and SCH 1.1 JSON-line archives. No EDA access,
source rewriting, geometry solver, or assertion of post-import native DRC.
Exit: 0 checked scope consistent; 1 differences require review; 2 unsupported/input error.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import zipfile

SCHEMA = 'project-handoff-report/v1'
ATTR_ACTIONS = {'新增属性', '删除属性', '修改属性'}
KNOWN_ACTIONS = ATTR_ACTIONS | {'修改封装', '新增元件', '删除元件', '修改网络',
                              '新增网络', '删除网络', '修改引脚网络', '修改设计规则'}

class InputError(ValueError):
    pass

def sha(data):
    return hashlib.sha256(data).hexdigest()

def records(data, kind):
    try:
        rr = [json.loads(s) for s in data.decode('utf-8-sig').splitlines() if s.strip()]
    except (ValueError, UnicodeError) as exc:
        raise InputError(f'Unsupported {kind} JSON-line source') from exc
    version = {'PCB':'1.8', 'SCH':'1.1', 'FOOTPRINT':'1.8', 'SYMBOL':'1.1'}[kind]
    if not rr or rr[0] != ['DOCTYPE', kind, version] or any(not isinstance(r,list) for r in rr):
        raise InputError(f'Only {kind} {version} JSON-line source is supported')
    # Real 1.8 library sources can contain [] placeholders. They have no fields;
    # retain raw bytes for equality, skip only these known empty records here.
    return [r for r in rr if r]

def attributes(rr, pcb=False):
    result = {}
    for r in rr:
        if r[0] != 'ATTR':
            continue
        if len(r) < (9 if pcb else 5):
            raise InputError('Incomplete ATTR record')
        parent, key, val = (r[3],r[7],r[8]) if pcb else (r[2],r[3],r[4])
        if key in result.setdefault(parent, {}):
            raise InputError(f'Duplicate attribute {parent}:{key}')
        result[parent][key] = val
    return result

def read_changes(path):
    raw = Path(path).read_bytes()
    encodings = ['utf-16'] if raw[:2] in [b'\xff\xfe',b'\xfe\xff'] else ['utf-8-sig','gb18030']
    for enc in encodings:
        try:
            text = raw.decode(enc)
            break
        except UnicodeError:
            continue
    else:
        raise InputError(f'Cannot decode report {path}')
    header = ['变更','动作','对象','导入前','导入后']
    first_line = next((s for s in text.splitlines() if s.strip()), '')
    candidates = [d for d in [',','\t',';'] if next(csv.reader([first_line],delimiter=d))[:5] == header]
    if len(candidates)!=1:
        raise InputError('Unsupported import-report header/delimiter')
    delimiter=candidates[0]
    try:
        rows = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter, strict=True) if r and any(v.strip() for v in r)]
    except csv.Error as exc:
        raise InputError(f'Invalid delimited report {path}') from exc
    if not rows or rows[0][:5] != ['变更','动作','对象','导入前','导入后']:
        raise InputError('Unsupported import-report header; Chinese native report expected')
    ref = None
    actions = []
    for r in rows[1:]:
        if len(r) < 5:
            raise InputError('Incomplete import-report row')
        if r[0] not in ['✓','√'] and not r[1] and r[0].strip():
            ref = r[0].strip()
            continue
        if r[0] not in ['✓','√'] or r[1] not in KNOWN_ACTIONS or not ref:
            raise InputError(f'Unsupported/unclassified import action: {r[:3]}')
        actions.append(dict(ref=ref, action=r[1], object=r[2], before=r[3], after=r[4]))
    return {'sha256':sha(raw), 'encoding':enc, 'delimiter':delimiter,
            'actions':actions}

def audit(path, changes=None):
    raw = Path(path).read_bytes()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos = [v for v in z.infolist() if not v.is_dir()]
            if len(infos)>10000 or sum(v.file_size for v in infos)>256*1024*1024:
                raise InputError('Archive exceeds supported inventory budget')
            if len({v.filename for v in infos}) != len(infos):
                raise InputError('Duplicate ZIP member names')
            data = {v.filename:z.read(v) for v in infos}
    except zipfile.BadZipFile as exc:
        raise InputError('Not a supported EPRO ZIP archive') from exc
    if 'project.json' not in data:
        raise InputError('project.json absent at ZIP root; outer delivery ZIP is not the native project')
    try:
        pr = json.loads(data['project.json'].decode('utf-8-sig'))
    except (ValueError,UnicodeError) as exc:
        raise InputError('Invalid project.json') from exc
    if not isinstance(pr,dict) or any(not isinstance(pr.get(k),dict) for k in
                                     ['boards','pcbs','schematics','devices','footprints','symbols']):
        raise InputError('Unsupported project metadata schema')
    if not pr['boards'] or not pr['pcbs']:
        raise InputError('No auditable board/PCB; zero coverage cannot pass')
    issues = []
    def issue(code, board, ref, **kw):
        issues.append(dict(code=code, board=board, ref=ref, **kw))
    def member(name):
        if name not in data:
            raise InputError(f'Missing archive member: {name}')
        return data[name]
    boards = {}
    covered = set()
    for name,b in pr['boards'].items():
        if not isinstance(b,dict) or not b.get('pcb') or not b.get('schematic'):
            raise InputError(f'Board {name} lacks a PCB/schematic mapping')
        pcb, sch = b['pcb'], b['schematic']
        if pcb in covered or pcb not in pr['pcbs'] or sch not in pr['schematics']:
            raise InputError(f'Invalid/duplicate board mapping {name}')
        covered.add(pcb)
        rr = records(member(f'PCB/{pcb}.epcb'),'PCB')
        pa = attributes(rr,True)
        comps = {}
        for c in rr:
            if c[0]!='COMPONENT':
                continue
            if len(c)<8 or not isinstance(c[7],dict):
                raise InputError('Unsupported PCB component schema')
            props=c[7];a=pa.get(c[1],{});uid=props.get('Unique ID')
            if not uid or uid in comps:
                raise InputError('Missing/duplicate PCB component Unique ID')
            comps[uid]=(c,a)
        units={}
        excluded=[]
        sheet_count=0
        for n,content in data.items():
            m=re.fullmatch(r'SHEET/'+re.escape(sch)+r'/(\d+)\.esch',n)
            if not m:
                continue
            sheet_count+=1
            sr=records(content,'SCH');sa=attributes(sr)
            for c in sr:
                if c[0]!='COMPONENT':
                    continue
                a=sa.get(c[1],{})
                # Frame/power symbols without a physical designator are not PCB instances.
                if not a.get('Designator'):
                    continue
                device=pr['devices'].get(a.get('Device'))
                if not isinstance(device,dict) or not isinstance(device.get('attributes'),dict):
                    issue('SCHEMATIC_DEVICE_MISSING',name,a['Designator'])
                    defaults={}
                else:
                    defaults=device['attributes']
                symbol=a.get('Symbol') or defaults.get('Symbol')
                if not symbol or symbol not in pr['symbols']:
                    issue('SCHEMATIC_SYMBOL_MISSING',name,a['Designator'])
                else:
                    records(member(f'SYMBOL/{symbol}.esym'),'SYMBOL')
                if defaults.get('Symbol') and symbol!=defaults['Symbol']:
                    issue('DEVICE_SYMBOL_MISMATCH',name,a['Designator'])
                convert=a.get('Convert to PCB')
                if convert is None:convert=defaults.get('Convert to PCB','yes')
                if convert in [False,0,'no','false','No','False']:
                    excluded.append(a['Designator'])
                    continue
                if convert not in [True,1,'yes','true','Yes','True']:
                    raise InputError('Unsupported Convert to PCB value')
                uid=a.get('Unique ID')
                if not uid:
                    raise InputError('Missing schematic Unique ID')
                units.setdefault(uid,[]).append(dict(ref=a['Designator'], device=a.get('Device'),
                    symbol=symbol, unit=c[2], channel=f'${m[1]}{c[1]}'))
        if not sheet_count or not comps or not units:
            raise InputError(f'Empty/unsupported board {name}')
        for uid,group in units.items():
            ref=group[0]['ref']
            if len(group)>1:
                unit_parts=[v['unit'].rsplit('.',1) for v in group]
                legal=(len({(v['ref'],v['device'],v['symbol']) for v in group})==1
                       and all(len(v)==2 and v[1].isdigit() for v in unit_parts)
                       and len({v[0] for v in unit_parts})==1
                       and len({v['unit'] for v in group})==len(group))
                if not legal:
                    issue('INVALID_MULTI_UNIT_GROUP',name,ref)
            if uid not in comps:
                issue('MISSING_PCB_INSTANCE',name,ref)
        footprints=[]
        for uid,(c,a) in comps.items():
            ref=a.get('Designator')
            group=units.get(uid,[])
            if not group:
                issue('MISSING_SCHEMATIC_INSTANCE',name,ref)
            else:
                if any(v['ref']!=ref or v['device']!=a.get('Device') for v in group):
                    issue('INSTANCE_DEVICE_OR_REF_MISMATCH',name,ref)
                if c[7].get('Channel ID') not in [v['channel'] for v in group]:
                    issue('CHANNEL_NOT_IN_SCHEMATIC_UNITS',name,ref)
            dev=pr['devices'].get(a.get('Device'))
            if not isinstance(dev,dict) or not isinstance(dev.get('attributes'),dict):
                issue('DEVICE_REFERENCE_MISSING',name,ref)
                continue
            fp=a.get('Footprint');dfp=dev['attributes'].get('Footprint')
            for kind,key in [('footprints',fp),('footprints',dfp),('symbols',dev['attributes'].get('Symbol'))]:
                if not key or key not in pr[kind]:
                    issue('LIBRARY_REFERENCE_MISSING',name,ref,kind=kind,key=key)
            if fp in pr['footprints'] and dfp in pr['footprints']:
                actual=member(f'FOOTPRINT/{fp}.efoo');desired=member(f'FOOTPRINT/{dfp}.efoo')
                records(actual,'FOOTPRINT');records(desired,'FOOTPRINT')
                same=actual==desired
                footprints.append(dict(ref=ref,pcbFootprint=fp,deviceFootprint=dfp,byteIdentical=same))
                if fp!=dfp:
                    issue('FOOTPRINT_REFERENCE_MISMATCH',name,ref,byteIdentical=same)
        boards[name]=dict(pcb=pcb,schematic=sch,pcbComponents=len(comps),logicalSchematicComponents=len(units),
                          multiUnitGroups={u:g for u,g in units.items() if len(g)>1},footprints=footprints,
                          schematicOnlyDesignators=excluded)
    if covered!=set(pr['pcbs']):
        raise InputError('Not all project PCBs have auditable board mappings')
    reports={}
    for name,file in (changes or {}).items():
        if name not in boards:
            raise InputError(f'Import report names an unknown board {name}')
        r=read_changes(file)
        fp_by_ref={v['ref']:v for v in boards[name]['footprints']}
        r['classificationCounts']={}
        for a in r['actions']:
            category='attribute' if a['action'] in ATTR_ACTIONS else 'footprint' if a['action']=='修改封装' else 'structural-or-rules'
            r['classificationCounts'][category]=r['classificationCounts'].get(category,0)+1
            if category=='footprint':
                a['archiveFootprintByteIdentical']=fp_by_ref.get(a['ref'],{}).get('byteIdentical')
        r['actionCount']=len(r['actions'])
        if r['actions']:
            issue('NATIVE_IMPORT_CHANGES_PENDING',name,None,actions=r['actionCount'])
        reports[name]=r
    return dict(schema=SCHEMA,status='REVIEW' if issues else 'PASS',passed=not issues,
                inputs={'project':str(Path(path).resolve()),'sha256':sha(raw)},boards=boards,
                nativeImportReports=reports,issues=issues,nativeMutation=False,nativeRoundTripVerified=False,
                limitations=['Legacy schema only; no inheritance/attribute reconstruction beyond explicit native CSV reports.',
                    'No pin-net, copper continuity, clearance, mechanical or firmware qualification.',
                    'A valid multi-unit group/primary Channel does not establish native grouping attributes.',
                    'Byte-identical footprints do not prove the current editor has applied the update.',
                    'PASS covers only these file checks; native reimport/save/reopen/DRC still required.'])

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('project');ap.add_argument('--changes',action='append',default=[],metavar='BOARD=FILE')
    ap.add_argument('--out',required=True);args=ap.parse_args(argv)
    try:
        changes={}
        for item in args.changes:
            if '=' not in item:
                raise InputError('--changes requires BOARD=FILE')
            board,file=item.split('=',1)
            if not board or not file or board in changes:
                raise InputError('Empty/duplicate --changes board')
            changes[board]=file
        out=Path(args.out).resolve();inputs=[Path(p).resolve() for p in [args.project,*changes.values()]]
        if any(out==p or(out.exists() and p.exists() and out.samefile(p)) for p in inputs):
            raise InputError('Report aliases an input; refusing overwrite')
        report=audit(args.project,changes)
        with tempfile.NamedTemporaryFile('w',encoding='utf8',dir=out.parent,delete=False,suffix='.tmp') as f:
            temp=Path(f.name);json.dump(report,f,ensure_ascii=False,indent=2,allow_nan=False)
        try:
            os.replace(temp,out)
        finally:
            temp.unlink(missing_ok=True)
        print(json.dumps({'status':report['status'],'issues':len(report['issues']),'out':str(out)}))
        return 0 if report['passed'] else 1
    except (InputError,OSError,ValueError,TypeError,KeyError,IndexError) as exc:
        print(json.dumps({'status':'UNSUPPORTED_OR_INPUT_ERROR','error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
