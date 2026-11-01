#!/usr/bin/env python3
"""Organize explicit board targets and estimate steady-state trace width.

Python 3.10+, no EDA access. Historical IPC-2221 width estimate only; not an
impedance, pulse-thermal, safety-clearance or creepage solver. Missing design
bases remain pending. Built-in targets are an example, not universal rules.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile

SCHEMA='param-spec/v2'
NET_CLASSES=('signal','power','hv','pulse','differential')
LIMITATIONS=[
 'IPC-2221 formula is an old steady-state estimate; actual stackup, plane spreading, necks, vias and heat require review.',
 'No peak-current substitution into a steady-state formula qualifies pulse copper.',
 'Clearance and creepage are independent caller-supplied requirements with named bases; no universal voltage table is applied.',
 'Differential geometry is supplied, not impedance-solved; board thickness alone is insufficient.',
 'Mask exposure and optocoupler slots are optional explicit design choices, not geometry created here.',
 'The example is not a manufacturing, thermal, EMC or isolation qualification.'
]

class InputError(ValueError):
    pass

def number(v,label,positive=False):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 or(positive and v==0):
        raise InputError(f'{label}: expected finite '+('positive'if positive else'nonnegative')+' number')
    return float(v)

def optional(obj,key,positive=False):
    return None if obj.get(key) is None else number(obj[key],key,positive)

def ipc2221_trace_width_mm(current_a,rise_c,copper_oz,external=True):
    current_a=number(current_a,'currentA');rise_c=number(rise_c,'riseLimitC',True);copper_oz=number(copper_oz,'weightOz',True)
    if current_a==0:return 0.0
    k=.048 if external else .024
    return(current_a/(k*rise_c**.44))**(1/.725)/(copper_oz*1.378)*.0254

def ipc_clearance_mm(voltage_v):
    number(voltage_v,'voltageV')
    raise InputError('No validated clearance category/standard supplied; use clearanceMm and clearanceBasis. Creepage is separate.')

def calc_network(net,rise_c,copper_oz):
    cls=net['class'];pending=[];rules=[]
    current=optional(net,'currentA');voltage=optional(net,'voltageV')
    minimum=optional(net,'minTraceWidthMm',True);supplied=optional(net,'traceWidthMm',True)
    raw=None
    if cls in ['pulse','differential']:
        width=max(v for v in [minimum,supplied]if v is not None)if any(v is not None for v in [minimum,supplied])else None
        if width is None:pending.append('Trace width not supplied; no universal pulse/differential geometry.')
        if not net.get('widthBasis'):pending.append('Missing widthBasis.')
        pending.append('Pulse waveform/thermal verification required.'if cls=='pulse'else'Controlled impedance/reference verification required.')
    else:
        if current is None:pending.append('Missing currentA; no ampacity estimate.')
        else:raw=ipc2221_trace_width_mm(current,rise_c,copper_oz)
        candidates=[v for v in [raw,minimum,supplied]if v is not None and v>0]
        width=max(candidates)if candidates else None
        if width is None:pending.append('Trace width unresolved.')
        if minimum is not None:rules.append('explicit-project-minimum')
        if raw is not None:rules.append('steady-state-ipc2221-estimate')
    clearance=optional(net,'clearanceMm',True);creepage=optional(net,'creepageMm',True)
    if clearance is None or not net.get('clearanceBasis'):pending.append('Clearance value/basis unresolved.')
    if net.get('requiresCreepage')and(creepage is None or not net.get('creepageBasis')):pending.append('Creepage value/basis unresolved.')
    if creepage is not None and not net.get('creepageBasis'):pending.append('Creepage supplied without an assessment basis.')
    gap=optional(net,'gapMm',True)
    if cls=='differential'and gap is None:pending.append('Differential gap unresolved.')
    if not isinstance(net.get('openSolder',False),bool):raise InputError('openSolder: expected boolean')
    return dict(name=net['name'],**{'class':cls},currentA=current,voltageV=voltage,
                rawIpcWidthMm=raw,traceWidthMm=width,gapMm=gap,clearanceMm=clearance,creepageMm=creepage,
                openSolder=net.get('openSolder',False),rulesApplied=rules,reviewRequired=pending)

def build_spec(targets):
    if not isinstance(targets,dict)or targets.get('schema')!='param-targets/v1':raise InputError('input schema must be param-targets/v1')
    copper=targets.get('copper',{});oz=number(copper.get('weightOz'),'weightOz',True);rise=number(targets.get('riseLimitC'),'riseLimitC',True)
    layers=copper.get('layers');thickness=optional(copper,'boardThicknessMm',True)
    if layers is not None and(isinstance(layers,bool)or not isinstance(layers,int)or layers<1):raise InputError('layers: expected positive integer')
    nets=targets.get('networks');seen=set();result=[];pending=[]
    if not isinstance(nets,list)or not nets:raise InputError('networks: expected nonempty array')
    for net in nets:
        if not isinstance(net,dict)or not isinstance(net.get('name'),str)or not net['name'].strip():raise InputError('network name required')
        if net['name']in seen:raise InputError('duplicate network name')
        seen.add(net['name'])
        if net.get('class')not in NET_CLASSES:raise InputError('unknown network class')
        result.append(calc_network(net,rise,oz))
    cutouts=[]
    for opto in targets.get('optocouplers',[]):
        if not isinstance(opto,dict)or not isinstance(opto.get('name'),str):raise InputError('optocoupler name required')
        width=optional(opto,'slotWidthMm',True)
        if width is not None:
            cutouts.append(dict(name=opto['name'],type='routed-slot',widthMm=width,basis=opto.get('slotBasis')))
            if not opto.get('slotBasis'):pending.append(opto['name']+': slot basis unresolved')
        else:pending.append(opto['name']+': determine whether a slot is required; no automatic slot')
    pending.extend(n['name']+': '+p for n in result for p in n['reviewRequired'])
    return dict(schema=SCHEMA,status='NEEDS_REVIEW'if pending else'ESTIMATE_ONLY',
                exampleOnly=targets.get('exampleOnly',False),copper=dict(weightOz=oz,layers=layers,boardThicknessMm=thickness),
                riseLimitC=rise,networks=result,cutouts=cutouts,
                differential={'appliesTo':[n['name']for n in result if n['class']=='differential'],'impedanceSolved':False},
                reviewRequired=pending,limitations=LIMITATIONS,nativeMutation=False)

PRESET={'schema':'param-targets/v1','exampleOnly':True,
        'copper':{'weightOz':1.0,'layers':2,'boardThicknessMm':1.6},'riseLimitC':10.0,
        'networks':[{'name':'EXAMPLE_SIGNAL','class':'signal','currentA':.05,'voltageV':3.3,'minTraceWidthMm':.25},
                    {'name':'EXAMPLE_BUS','class':'power','currentA':5,'voltageV':12},
                    {'name':'EXAMPLE_PULSE','class':'pulse','currentA':70,'voltageV':200},
                    {'name':'EXAMPLE_DIFF','class':'differential','voltageV':3.3}],
        'optocouplers':[{'name':'EXAMPLE_OPTO'}]}

def protect_output(path,inputs):
    out=Path(path).resolve()
    if any(out==Path(p).resolve()or(out.exists()and Path(p).exists()and out.samefile(p))for p in inputs):raise InputError('Report path aliases an input file')
    return out

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('targets',nargs='?');ap.add_argument('--out',required=True);args=ap.parse_args(argv)
    try:
        inputs=[args.targets]if args.targets else[];out=protect_output(args.out,inputs)
        targets=json.loads(Path(args.targets).read_text(encoding='utf-8-sig'))if args.targets else PRESET
        report=build_spec(targets)
        with tempfile.NamedTemporaryFile('w',encoding='utf8',dir=out.parent,delete=False,suffix='.tmp')as f:
            temp=Path(f.name);json.dump(report,f,ensure_ascii=False,indent=2,allow_nan=False)
        try:os.replace(temp,out)
        finally:temp.unlink(missing_ok=True)
        print(json.dumps({'status':report['status'],'exampleOnly':report['exampleOnly'],'out':str(out)}))
        return 1 if report['reviewRequired']else 0
    except(InputError,OSError,ValueError,TypeError,OverflowError)as exc:
        print(json.dumps({'status':'ERROR','error':str(exc)}),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
