#!/usr/bin/env node
'use strict';
// AI-EDA command-port client. Never connects to the extension's 8765 port,
// retries a request, starts a service, downloads a dependency, or evaluates JS.
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {createRequire}=require('node:module');

const READ_ONLY=new Set([
 'dmt.getDocumentInfo','dmt.getProjectInfo','dmt.getBoardInfo','dmt.listBoards','dmt.listTabs',
 'sys.getDocumentInfo','sys.getEnvironment','sys.getDocumentSource','sys.getDocumentFootprintSources',
 'sys.exportProjectFile','sys.runDrc','sys.netlistComparison',
 'pcb.getState','pcb.listComponents','pcb.listNets','pcb.listLayers','pcb.listPrimitives',
 'pcb.getComponent','pcb.getComponentContext','pcb.getNetlist','pcb.getPrimitiveBboxes',
 'pcb.getPolylinesByIds','sch.getState','sch.listComponents','sch.listNets','sch.getNetlist',
 'sch.listWires','sch.listPrimitives','sch.getComponent','sch.getComponentContext','sch.getBom',
 'lib.searchDevice','lib.getDevice','lib.searchFootprint','lib.getLibraries','lib.getDeviceByLcsc',
 'pcb.exportGerber','pcb.exportPickPlace'
]);
const LONG=new Set(['sch.getNetlist','sch.createNetPort','sch.createNetFlag',
 'sys.exportProjectFile','pcb.exportGerber']);

class BridgeError extends Error {
 constructor(message,details={}){super(message);this.name='BridgeError';Object.assign(this,details);}
}
function endpoint(value='ws://127.0.0.1:8766'){
 const u=new URL(value);
 if(u.protocol!=='ws:'||!['127.0.0.1','localhost','[::1]'].includes(u.hostname)||!u.port||u.port==='8765'||u.username||u.password||u.pathname!=='/'||u.search||u.hash)
  throw new BridgeError('Use an explicit loopback AI-EDA command WebSocket endpoint; HTTP gateways use another protocol.');
 return u.href;
}
function socketClass(root){
 if(typeof globalThis.WebSocket==='function')return globalThis.WebSocket;
 if(!root)throw new BridgeError('WebSocket unavailable. Use Node22+ or --bridge-root pointing to an existing AI-EDA install with ws.');
 return createRequire(path.resolve(root,'mcp-server','package.json'))('ws');
}
function request(command,params={},options={}){
 if(typeof command!=='string'||!command||!params||typeof params!=='object'||Array.isArray(params))throw new BridgeError('Command and object params required');
 const url=endpoint(options.endpoint),timeout=options.timeoutMs??(LONG.has(command)?185000:35000);
 if(!Number.isInteger(timeout)||timeout<1||timeout>240000)throw new BridgeError('timeoutMs must be 1..240000');
 const W=options.WebSocketClass||socketClass(options.bridgeRoot);
 const id='skill_'+crypto.randomUUID();
 return new Promise((resolve,reject)=>{
  let ws,sent=false,settled=false;
  const finish=(error,value)=>{
   if(settled)return;settled=true;clearTimeout(timer);
   try{ws?.close();}catch{}
   if(error)reject(Object.assign(error,{requestId:id,command,requestSent:sent}));else resolve({id,command,data:value});
  };
  const timer=setTimeout(()=>finish(new BridgeError('Timed out waiting; remote operation was not cancelled. Inspect state before retrying.',{kind:'TIMEOUT'})),timeout);
  try{ws=new W(url);}catch(e){finish(new BridgeError(e.message,{kind:'CONNECT'}));return;}
  ws.addEventListener('open',()=>{
   if(settled)return;
   try{sent=true;ws.send(JSON.stringify({id,command,params}));}catch(e){finish(new BridgeError(e.message,{kind:'SEND'}));}
  });
  ws.addEventListener('message',event=>{
   try{
    if(typeof event.data!=='string'||event.data.length>64*1024*1024)throw new BridgeError('Unsupported/oversized response',{kind:'PROTOCOL'});
    const msg=JSON.parse(event.data);
    if(msg.id!==id)return;
    if(msg.type!=='response'||typeof msg.success!=='boolean')throw new BridgeError('Response envelope does not match AI-EDA command protocol',{kind:'PROTOCOL'});
    if(!msg.success)throw new BridgeError(String(msg.error||'Remote command failed'),{kind:'REMOTE'});
    const data=msg.data;
    // The command server can report transport success around an application failure.
    if(data&&typeof data==='object'&&(data.success===false||data.verified===false||
       (typeof data.failed==='number'&&data.failed>0)||
       (Array.isArray(data.results)&&data.results.some(v=>v&&v.success===false))))
      throw new BridgeError('Handler reported failed/unverified or partial result',{kind:'APPLICATION',response:data});
    if(command==='pcb.drawLine'&&(!Array.isArray(params.points)||!Array.isArray(data?.segments)||data.segments.length!==params.points.length-1))
      throw new BridgeError('Created trace segment count differs from requested path; inspect returned objects, do not replay the path',{kind:'APPLICATION',response:data});
    if(command==='pcb.batchMove'&&(!Array.isArray(params.moves)||data?.total!==params.moves.length||!Array.isArray(data?.results)||data.results.length!==params.moves.length))
      throw new BridgeError('Batch result inventory differs from requested moves; inspect completed objects',{kind:'APPLICATION',response:data});
    finish(null,data);
   }catch(e){finish(e instanceof BridgeError?e:new BridgeError(e.message,{kind:'PROTOCOL'}));}
  });
  ws.addEventListener('error',()=>finish(new BridgeError('WebSocket connection error; check the existing bridge and extension',{kind:'CONNECT'})));
  ws.addEventListener('close',()=>finish(new BridgeError('Connection closed before matching response',{kind:'DISCONNECTED'})));
 });
}
function identity(d){
 if(!d||typeof d.uuid!=='string'||!d.uuid||typeof d.parentProjectUuid!=='string'||!d.parentProjectUuid||
    !Number.isInteger(d.documentType)||typeof d.tabId!=='string'||!d.tabId)
  throw new BridgeError('Active document/project identity unavailable; select the intended EDA document first',{kind:'IDENTITY'});
 return {documentUuid:d.uuid,projectUuid:d.parentProjectUuid,documentType:d.documentType,tabId:d.tabId};
}
async function current(options){return identity((await request('dmt.getDocumentInfo',{},options)).data);}
function matches(d,expected){
 if(d.documentUuid!==expected.documentUuid||d.projectUuid!==expected.projectUuid)
  throw new BridgeError('Active document/project differs from required identity; no automatic retry',{kind:'IDENTITY',actual:d});
}
async function guardedCall(command,params,expected,options={}){
 const write=!READ_ONLY.has(command);
 if(write&&!options.write)throw new BridgeError('Mutating/unknown command requires --write; this is an explicit execution mode, not a new approval request');
 if(!expected?.documentUuid||!expected?.projectUuid)throw new BridgeError('Expected document and project UUIDs are required');
 const before=await current(options);matches(before,expected);
 const requiredType=command.startsWith('pcb.')?3:command.startsWith('sch.')?1:
   command==='sys.runDrc'?(params.type==='pcb'?3:params.type==='sch'?1:null):null;
 if(requiredType!==null&&before.documentType!==requiredType)
  throw new BridgeError('Command document type differs from the active document; no edit sent',{kind:'IDENTITY',actual:before});
 let result;
 try{result=await request(command,params,options);}
 catch(e){e.mutationMayHaveHappened=write&&e.requestSent===true;throw e;}
 try{
  const after=await current(options);matches(after,expected);
  if(after.documentType!==before.documentType||after.tabId!==before.tabId)throw new BridgeError('Document type/tab changed during operation',{kind:'IDENTITY'});
  return {...result,identityBefore:before,identityAfter:after,guardAtomic:false,nativePersistenceVerified:false};
 }catch(e){e.mutationMayHaveHappened=write;e.operationCompleted=true;e.response=result.data;
  e.requestId=result.id;e.command=command;e.requestSent=true;throw e;}
}
async function probe(options={}){
 const first=await request('dmt.getDocumentInfo',{},options);
 let active=null,identityError=null;
 if(first.data?.uuid)try{active=identity(first.data);}catch(e){identityError=e.message;}
 let editorVersion=null,environmentError=null;
 try{
  const version=(await request('sys.getDocumentInfo',{},options)).data?.editorVersion;
  if(typeof version==='string'||typeof version==='number')editorVersion=version;
  else environmentError='Environment wrapper did not return a scalar editor version';
 }
 catch(e){environmentError=e.message;}
 if(active){const after=await current(options);matches(after,active);}
 return {protocol:'AI-EDA command-port',endpoint:endpoint(options.endpoint),responsive:true,
         activeDocument:active,readyForGuardedCalls:!!active,identityError,editorVersion,environmentError,
         loadedExtensionVersion:null,serviceSourceIdentityVerified:false,
         note:'Command response observed; source/build identity and unit calibration are separate. No design scan or mutation performed.'};
}
function describe(root){
 if(!root)throw new BridgeError('describe requires --bridge-root pointing to AI-EDA source/build');
 const read=p=>fs.readFileSync(path.resolve(root,p));
 const server=read('mcp-server/src/protocol.ts'),extension=read('eda-extension/src/protocol.ts');
 const commands=b=>[...b.toString('utf8').matchAll(/^\s*[A-Z][A-Z0-9_]*\s*=\s*'([^']+)'/gm)].map(m=>m[1]).sort();
 const a=commands(server),b=commands(extension);
 if(!a.length||new Set(a).size!==a.length||JSON.stringify(a)!==JSON.stringify(b))throw new BridgeError('Source protocol command sets missing, duplicate, or different; do not assume compatibility');
 const builds={};
 for(const name of ['mcp-server/dist/index.js','mcp-server/dist/ws-bridge.js']){
  const full=path.resolve(root,name);builds[name]=fs.existsSync(full)?{exists:true,sha256:crypto.createHash('sha256').update(read(name)).digest('hex')}:{exists:false};
 }
 return {protocol:'AI-EDA command-port',bridgeRoot:path.resolve(root),
  serverPackageVersion:JSON.parse(read('mcp-server/package.json')).version,
  extensionSourceVersion:JSON.parse(read('eda-extension/package.json')).version,
  sourceProtocolCommandsAgree:true,commands:a,builds,loadedVersionsVerified:false,
  note:'Local source/build inventory only. It does not prove the running MCP server or installed extension loaded these bytes.'};
}
function decodeFile(data){
 if(!data||data.success!==true||data.hasFile!==true||typeof data.base64!=='string'||!Number.isSafeInteger(data.byteLength)||data.byteLength<1||data.byteLength>32*1024*1024)
  throw new BridgeError('No complete successful base64 File result to store');
 const bytes=Buffer.from(data.base64,'base64');
 if(bytes.length!==data.byteLength||bytes.toString('base64')!==data.base64)throw new BridgeError('File base64/declared length mismatch');
 if(bytes.length<4||bytes.readUInt32LE(0)!==0x04034b50)throw new BridgeError('Expected native project/Gerber ZIP signature');
 return bytes;
}
function args(argv){
 const result={mode:argv[0]};let i=1;
 if(result.mode==='call')result.command=argv[i++];
 const keys={'--endpoint':'endpoint','--bridge-root':'bridgeRoot','--timeout-ms':'timeoutMs','--params-file':'paramsFile',
 '--expect-document':'documentUuid','--expect-project':'projectUuid','--out':'out','--file-out':'fileOut'};
 for(;i<argv.length;i++){
  if(argv[i]==='--write'){result.write=true;continue;}
  const key=keys[argv[i]];if(!key||!argv[i+1])throw new BridgeError('Unknown/incomplete argument: '+argv[i]);
  result[key]=argv[++i];
 }
 if(result.timeoutMs!==undefined)result.timeoutMs=Number(result.timeoutMs);
 if(!['probe','call','describe'].includes(result.mode))throw new BridgeError('Usage: ai_bridge_client.cjs describe|probe|call COMMAND --out receipt.json [options]');
 if(!result.out)throw new BridgeError('--out required; full results are written to a new file');
 return result;
}
function newOutput(p,inputs){
 const target=path.resolve(p);
 if(inputs.some(v=>path.resolve(v)===target)||fs.existsSync(target))throw new BridgeError('Output must be a new path, distinct from inputs: '+target);
 if(!fs.statSync(path.dirname(target)).isDirectory())throw new BridgeError('Output parent missing');
 return target;
}
async function main(argv=process.argv.slice(2)){
 if(argv.length===1&&['--help','-h'].includes(argv[0])){
  console.log('AI-EDA command-port client (no auto-start/retry)\n'+
   '  describe --bridge-root ROOT --out NEW.json\n'+
   '  probe [--endpoint ws://127.0.0.1:8766] --out NEW.json\n'+
   '  call COMMAND --expect-document UUID --expect-project UUID --out NEW.json\n'+
   '    [--params-file INPUT.json] [--write] [--timeout-ms N]\n'+
   '    [--file-out NEW.epro|NEW.zip] [--bridge-root ROOT]');return 0;
 }
 let a,out;
 try{
  a=args(argv);out=newOutput(a.out,a.paramsFile?[a.paramsFile]:[]);
  let fileOut=a.fileOut?newOutput(a.fileOut,[a.out,...(a.paramsFile?[a.paramsFile]:[])]):null;
  let result;
  if(a.mode==='describe'){
   if(fileOut)throw new BridgeError('describe does not export design files');
   result=describe(a.bridgeRoot);
  }else if(a.mode==='probe'){
   if(fileOut)throw new BridgeError('probe does not export design files');
   result=await probe(a);
  }else{
   let params=a.paramsFile?JSON.parse(fs.readFileSync(a.paramsFile,'utf8').replace(/^\uFEFF/,'')):{};
   if(!params||typeof params!=='object'||Array.isArray(params))throw new BridgeError('Params file must contain an object');
   if(fileOut){
    if(!['sys.exportProjectFile','pcb.exportGerber'].includes(a.command))throw new BridgeError('--file-out supports only actual native project/Gerber exports');
    params.includeBase64=true;
    if(a.command==='sys.exportProjectFile'){
     if(params.expectedDocumentUuid&&params.expectedDocumentUuid!==a.documentUuid||params.expectedProjectUuid&&params.expectedProjectUuid!==a.projectUuid)
      throw new BridgeError('Export parameter identities conflict with CLI identity');
     params.expectedDocumentUuid=a.documentUuid;params.expectedProjectUuid=a.projectUuid;
    }
   }
   result=await guardedCall(a.command,params,{documentUuid:a.documentUuid,projectUuid:a.projectUuid},a);
   if(fileOut){
    const bytes=decodeFile(result.data);fs.writeFileSync(fileOut,bytes,{flag:'wx'});
    const {base64,...metadata}=result.data;
    result.data=metadata;result.storedFile={path:fileOut,byteLength:bytes.length,sha256:crypto.createHash('sha256').update(bytes).digest('hex')};
   }
  }
  const receipt={status:a.mode==='describe'?'LOCAL_SOURCE_INSPECTED':a.mode==='probe'?'BRIDGE_RESPONSIVE':'RESPONSE_RECEIVED',atUtc:new Date().toISOString(),...result};
  fs.writeFileSync(out,JSON.stringify(receipt,null,2),{flag:'wx'});
  console.log(JSON.stringify({status:receipt.status,receipt:out,storedFile:receipt.storedFile??null}));return 0;
 }catch(e){
  const failure={status:'ERROR',atUtc:new Date().toISOString(),error:{message:e.message,kind:e.kind??null,
   requestId:e.requestId??null,command:e.command??a?.command??null,requestSent:e.requestSent??false,
   mutationMayHaveHappened:e.mutationMayHaveHappened??false,operationCompleted:e.operationCompleted??false},
   ...(e.response?{response:e.response}:{})};
  if(out&&!fs.existsSync(out))try{fs.writeFileSync(out,JSON.stringify(failure,null,2),{flag:'wx'});}catch{}
  console.error(JSON.stringify(failure.error));return 2;
 }
}
module.exports={BridgeError,READ_ONLY,endpoint,request,identity,guardedCall,probe,describe,decodeFile,args,main};
if(require.main===module)main().then(code=>{process.exitCode=code;});
