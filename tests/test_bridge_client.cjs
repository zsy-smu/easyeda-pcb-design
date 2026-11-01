'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const B=require('../scripts/ai_bridge_client.cjs');
const doc={uuid:'doc',parentProjectUuid:'project',documentType:3,tabId:'tab'};
const expected={documentUuid:'doc',projectUuid:'project'};
function fake(handler){
 return class extends EventTarget {
  constructor(url){super();this.url=url;queueMicrotask(()=>this.dispatchEvent(new Event('open')));}
  send(raw){handler(JSON.parse(raw),this);}
  emit(data){const ev=new Event('message');ev.data=typeof data==='string'?data:JSON.stringify(data);this.dispatchEvent(ev);}
  close(){this.closed=true;}
 };
}
function reply(ws,msg,data){ws.emit({id:msg.id,type:'response',success:true,data});}
function cleanTemp(dir){
 const resolved=fs.realpathSync(dir);
 assert.equal(path.dirname(resolved),fs.realpathSync(os.tmpdir()));
 assert.match(path.basename(resolved),/^bridge-(description|output)-/);
 fs.rmSync(resolved,{recursive:true,force:true});
}
test('loopback command endpoint only; another HTTP gateway is not silently selected',()=>{
 assert.equal(B.endpoint(),'ws://127.0.0.1:8766/');
 for(const u of ['http://127.0.0.1:49620/execute','ws://example.com:8766','ws://127.0.0.1:8766/execute','ws://user:secret@127.0.0.1:8766','ws://127.0.0.1:8765','ws://localhost'])assert.throws(()=>B.endpoint(u));
});
test('correlate response IDs and unwrap command envelope',async()=>{
 const W=fake((m,w)=>{w.emit({id:'other',type:'response',success:true,data:'wrong'});reply(w,m,{value:42});});
 const r=await B.request('dmt.getDocumentInfo',{}, {WebSocketClass:W});assert.equal(r.data.value,42);
});
test('transport success cannot conceal application failure',async()=>{
 const W=fake((m,w)=>reply(w,m,{success:false,message:'native save failed'}));
 await assert.rejects(B.request('pcb.save',{}, {WebSocketClass:W}),e=>e.kind==='APPLICATION'&&e.response.success===false);
});
test('batch partial failure is not success',async()=>{
 const W=fake((m,w)=>reply(w,m,{failed:1,results:[{success:true},{success:false}]}));
 await assert.rejects(B.request('pcb.batchMove',{}, {WebSocketClass:W}),e=>e.kind==='APPLICATION');
});
test('drawing fewer segments than requested is a partial failure even with success true',async()=>{
 const W=fake((m,w)=>reply(w,m,{success:true,segments:[{primitiveId:'only-one'}]}));
 await assert.rejects(B.request('pcb.drawLine',{points:[{x:0,y:0},{x:1,y:0},{x:2,y:0}]},{WebSocketClass:W}),e=>e.kind==='APPLICATION'&&e.response.segments.length===1);
});
test('complete trace and batch inventories are accepted',async()=>{
 const W=fake((m,w)=>reply(w,m,m.command==='pcb.drawLine'?{success:true,segments:[{id:'1'},{id:'2'}]}:{total:1,failed:0,results:[{id:'1',success:true}]}));
 const r=await B.request('pcb.drawLine',{points:[{x:0,y:0},{x:1,y:0},{x:2,y:0}]},{WebSocketClass:W});assert.equal(r.data.segments.length,2);
 await B.request('pcb.batchMove',{moves:[{id:'1',x:0,y:0}]},{WebSocketClass:W});
});
test('remote unknown command fails once without a retry',async()=>{
 let calls=0;const W=fake((m,w)=>{calls++;w.emit({id:m.id,type:'response',success:false,error:'Unknown command'});});
 await assert.rejects(B.request('not.supported',{}, {WebSocketClass:W}),e=>e.kind==='REMOTE');assert.equal(calls,1);
});
test('bad JSON response is not accepted as an empty result',async()=>{
 const W=fake((m,w)=>w.emit('invalid JSON'));
 await assert.rejects(B.request('dmt.getDocumentInfo',{}, {WebSocketClass:W}),e=>e.kind==='PROTOCOL');
});
test('guard mismatch prevents the write from being sent',async()=>{
 const commands=[];const W=fake((m,w)=>{commands.push(m.command);reply(w,m,{...doc,uuid:'other'});});
 await assert.rejects(B.guardedCall('pcb.batchMove',{},expected,{write:true,WebSocketClass:W}),e=>e.kind==='IDENTITY');
 assert.deepEqual(commands,['dmt.getDocumentInfo']);
});
test('PCB commands and requested DRC type cannot execute in a schematic tab',async()=>{
 for(const [command,params]of [['pcb.drawLine',{points:[{x:0,y:0},{x:1,y:0}]}],['sys.runDrc',{type:'pcb'}]]){
  const commands=[];const W=fake((m,w)=>{commands.push(m.command);reply(w,m,{...doc,documentType:1});});
  await assert.rejects(B.guardedCall(command,params,expected,{write:true,WebSocketClass:W}),e=>e.kind==='IDENTITY');assert.deepEqual(commands,['dmt.getDocumentInfo']);
 }
});
test('incomplete tab/type identity cannot be called ready',async()=>{
 for(const key of ['documentType','tabId']){
  const incomplete={...doc};delete incomplete[key];assert.throws(()=>B.identity(incomplete));
  const W=fake((m,w)=>reply(w,m,m.command==='dmt.getDocumentInfo'?incomplete:{editorVersion:'3.x'}));
  const r=await B.probe({WebSocketClass:W});assert.equal(r.responsive,true);assert.equal(r.readyForGuardedCalls,false);
 }
});
test('unknown or mutating commands require explicit write mode before network',async()=>{
 let n=0;const W=fake(()=>n++);
 await assert.rejects(B.guardedCall('dmt.openDocument',{},expected,{WebSocketClass:W}));assert.equal(n,0);
});
test('changed tab after write yields a completed-but-unverified state',async()=>{
 let reads=0;const W=fake((m,w)=>reply(w,m,m.command==='dmt.getDocumentInfo'?{...doc,tabId:++reads>1?'new':'tab'}:{success:true}));
 await assert.rejects(B.guardedCall('pcb.save',{},expected,{write:true,WebSocketClass:W}),e=>e.operationCompleted===true&&e.mutationMayHaveHappened===true&&e.response.success===true);
});
test('write timeout does not cancel or replay the remote write',async()=>{
 const commands=[];const W=fake((m,w)=>{commands.push(m.command);if(m.command==='dmt.getDocumentInfo')reply(w,m,doc);});
 await assert.rejects(B.guardedCall('pcb.save',{},expected,{write:true,WebSocketClass:W,timeoutMs:20}),e=>e.kind==='TIMEOUT'&&e.mutationMayHaveHappened===true);
 assert.equal(commands.filter(v=>v==='pcb.save').length,1);
});
test('a connection opening after timeout cannot send a late command',async()=>{
 let sent=0;
 class Late extends EventTarget {
  constructor(){super();setTimeout(()=>this.dispatchEvent(new Event('open')),30);}
  send(){sent++;}close(){}
 }
 await assert.rejects(B.request('pcb.save',{}, {WebSocketClass:Late,timeoutMs:5}),e=>e.kind==='TIMEOUT'&&e.requestSent===false);
 await new Promise(resolve=>setTimeout(resolve,50));assert.equal(sent,0);
});
test('ordinary guarded read returns identity and does not assert persistence',async()=>{
 const W=fake((m,w)=>reply(w,m,m.command==='dmt.getDocumentInfo'?doc:{components:7}));
 const r=await B.guardedCall('pcb.getState',{},expected,{WebSocketClass:W});assert.equal(r.data.components,7);assert.equal(r.guardAtomic,false);assert.equal(r.nativePersistenceVerified,false);
});
test('probe is light and drops environment user data',async()=>{
 const commands=[];const W=fake((m,w)=>{commands.push(m.command);reply(w,m,m.command==='dmt.getDocumentInfo'?doc:{editorVersion:'3.x',userInfo:{secret:'private'}});});
 const r=await B.probe({WebSocketClass:W});assert.equal(r.readyForGuardedCalls,true);assert.equal(r.loadedExtensionVersion,null);
 assert.equal(JSON.stringify(r).includes('private'),false);assert.deepEqual(commands,['dmt.getDocumentInfo','sys.getDocumentInfo','dmt.getDocumentInfo']);
});
test('probe distinguishes responsive service from no active document',async()=>{
 const W=fake((m,w)=>reply(w,m,m.command==='dmt.getDocumentInfo'?{}:{editorVersion:'3.x'}));
 const r=await B.probe({WebSocketClass:W});assert.equal(r.responsive,true);assert.equal(r.readyForGuardedCalls,false);
});
test('actual file bytes require matching successful receipt, canonical base64 and signature',()=>{
 const bytes=Buffer.from([0x50,0x4b,0x03,0x04,0x11]);const d={success:true,hasFile:true,byteLength:bytes.length,base64:bytes.toString('base64')};
 assert.deepEqual(B.decodeFile(d),bytes);
 for(const x of [{...d,byteLength:100},{...d,success:false},{...d,base64:d.base64+'\n'},{...d,base64:'hello'}])assert.throws(()=>B.decodeFile(x));
});
test('static source inventory verifies protocol sets but not loaded extension',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'bridge-description-'));
 try{
  for(const p of ['mcp-server/src','eda-extension/src'])fs.mkdirSync(path.join(dir,p),{recursive:true});
  const source="export enum BridgeCommand {\n READ = 'dmt.getDocumentInfo',\n}\n";
  for(const p of ['mcp-server','eda-extension']){fs.writeFileSync(path.join(dir,p,'src/protocol.ts'),source);fs.writeFileSync(path.join(dir,p,'package.json'),JSON.stringify({version:'test'}));}
  const r=B.describe(dir);assert.deepEqual(r.commands,['dmt.getDocumentInfo']);assert.equal(r.loadedVersionsVerified,false);
  fs.appendFileSync(path.join(dir,'eda-extension/src/protocol.ts'),"\n OTHER = 'other.command'\n");assert.throws(()=>B.describe(dir));
 }finally{cleanTemp(dir);}
});
test('outputs cannot overwrite existing task files',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'bridge-output-'));const file=path.join(dir,'existing.json');
 try{
  fs.writeFileSync(file,'original');const code=await B.main(['probe','--out',file]);assert.equal(code,2);assert.equal(fs.readFileSync(file,'utf8'),'original');
 }finally{cleanTemp(dir);}
});
