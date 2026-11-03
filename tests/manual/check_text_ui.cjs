/* Run with the repository's Electron executable. Uses a separate synthetic store. */
process.on('uncaughtException', error => { console.error(error); process.exit(1); });
const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn, execFileSync } = require('node:child_process');
const project = path.resolve(__dirname, '../..');
const output = path.resolve(process.argv[2] || path.join(project,'logs/text-document-native'));
const python = path.join(project,'.venv/bin/python');
const control = crypto.randomBytes(32).toString('base64url');
fs.mkdirSync(output,{recursive:true});
app.setPath('userData',path.join(output,'electron-profile'));
app.disableHardwareAcceleration();
ipcMain.handle('is-accessibility-trusted',()=>true);
const report={native_electron:true,offscreen_renderer:false,personal_capture_paused:false,checks:[],errors:[]};
let backend,win,log,finishing=false;
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const watchdog=setTimeout(()=>finish(new Error('Text acceptance exceeded 90 seconds')),90000);
async function until(check){const end=Date.now()+15000;while(Date.now()<end){if(await check())return;await pause(100);}throw new Error('UI condition not satisfied');}
async function finish(error){
  if(finishing)return;finishing=true;clearTimeout(watchdog);
  if(backend&&backend.exitCode===null)backend.kill('SIGTERM');
  report.state=error?'failed':'passed';if(error)report.error=error.stack;
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
  if(error)console.error(error);
  if(win&&!win.isDestroyed())win.destroy();app.exit(error?1:0);
}
app.whenReady().then(async()=>{
  execFileSync(python,['tests/manual/check_agents_public.py','--prepare-only','--output',path.join(output,'fixture')],{cwd:project});
  const archive=path.join(output,'fixture/archive');
  const settings=JSON.parse(fs.readFileSync(path.join(archive,'settings.json')));
  const base=`http://127.0.0.1:${settings.server_port}`;
  report.backend_command=[python,'-m','openrelife.app','--storage-path',archive,'--no-capture'];
  backend=spawn(python,report.backend_command.slice(1),{cwd:project,env:{...process.env,OPENRELIFE_CONTROL_FD:'3',PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe','pipe']});
  backend.stdio[3].end(control);log=fs.createWriteStream(path.join(output,'backend.log'));
  backend.stdout.pipe(log,{end:false});backend.stderr.pipe(log,{end:false});
  async function api(route){const r=await fetch(base+route,{headers:{'X-OpenReLife-Control':control}});if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json();}
  await until(async()=>{try{await api('/api/recording-status');return true;}catch{return false;}});
  report.stage='creating_hidden_window';fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
  win=new BrowserWindow({width:1280,height:900,show:false,webPreferences:{preload:path.join(project,'electron-app/preload.js'),contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
  win.webContents.session.webRequest.onBeforeSendHeaders({urls:[base+'/*']},(details,done)=>done({requestHeaders:{...details.requestHeaders,'X-OpenReLife-Control':control}}));
  win.webContents.on('console-message',(_event,level,message)=>{if(level>=3)report.errors.push(message);});
  const evaluate=code=>win.webContents.executeJavaScript(code);
  report.stage='loading_product';fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
  await win.loadURL(base);
  await until(()=>evaluate('!!window.openrelifeSettings && !!window.openrelifeAgentUI && !!window.openrelifeTextUI'));
  win.webContents.debugger.attach('1.3');
  await win.webContents.debugger.sendCommand('Emulation.setFocusEmulationEnabled',{enabled:true});
  win.webContents.focus();
  async function key(keyCode,modifiers=[]){
    const codes={Tab:['Tab',9],Escape:['Escape',27],Left:['ArrowLeft',37],Backspace:['Backspace',8],Enter:['Enter',13],a:['a',65],k:['k',75],f:['f',70],Comma:[',',188]};
    const [name,virtual]=codes[keyCode];
    const mask=modifiers.reduce((sum,name)=>sum+({meta:4,shift:8,control:2,alt:1}[name]||0),0);
    const code=keyCode==='a'?'KeyA':keyCode==='f'?'KeyF':keyCode==='Comma'?'Comma':name;
    const commands=keyCode==='a'&&modifiers.includes('meta')?['selectAll']:keyCode==='Left'?['moveLeft']:keyCode==='Backspace'?['deleteBackward']:[];
    for(const type of ['rawKeyDown','keyUp'])await win.webContents.debugger.sendCommand('Input.dispatchKeyEvent',{type,key:name,code,windowsVirtualKeyCode:virtual,modifiers:mask,...(type==='rawKeyDown'?{commands}:{})});
    await pause(75);
  }
  async function click(selector){
    const point=await evaluate(`(()=>{const node=document.querySelector(${JSON.stringify(selector)});if(!node)throw new Error('Missing target');node.scrollIntoView({block:'nearest'});const r=node.getBoundingClientRect();if(!r.width||!r.height)throw new Error('Target is hidden: '+${JSON.stringify(selector)});return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};})()`);
    for(const type of ['mousePressed','mouseReleased'])await win.webContents.debugger.sendCommand('Input.dispatchMouseEvent',{type,button:'left',clickCount:1,...point});await pause(100);
  }
  const manifest=JSON.parse(fs.readFileSync(path.join(output,'fixture/fixture-manifest.json')));
  async function frame(identifier){
    await evaluate(`(()=>{const slider=document.getElementById('timelineSlider');const index=timestamps.indexOf(Number(${JSON.stringify(identifier)}));if(index<0)throw Error('Fixture not in timeline');slider.value=String(Number(slider.max)-index);slider.dispatchEvent(new Event('input',{bubbles:true}));})()`);
    await until(()=>evaluate(`document.getElementById('screenshot').src.endsWith(${JSON.stringify(identifier+'.webp')})`));
  }
  const local=manifest.identifiers.document_local,entry=await api('/api/entry/'+local);
  await frame(local);await click('button[onclick="openTextDialog(false)"]');
  await until(()=>evaluate(`document.getElementById('extractedText').textContent===${JSON.stringify(entry.text)}`));
  await until(()=>evaluate('document.getElementById("textDialogImage").complete && document.getElementById("textDialogImage").naturalWidth>0'));
  await pause(250);await win.webContents.capturePage();
  fs.writeFileSync(path.join(output,'document-desktop.png'),(await win.webContents.capturePage()).toPNG());
  const context=await evaluate(`({app:document.getElementById('textDialogApp').textContent,title:document.getElementById('textDialogSourceTitle').textContent,preview:document.getElementById('textDialogImage').getAttribute('src'),html_nodes:document.querySelectorAll('#extractedText img,#extractedText b').length})`);
  if(context.app!==entry.app||context.title!==entry.title||context.html_nodes)throw new Error('Document context or literal rendering failed');
  report.checks.push({frame:local,context,literal_text_and_whitespace:true});
  await key('Escape');
  if(await evaluate('document.getElementById("textDialog").classList.contains("show")'))throw new Error('First Escape failed after opening Text');
  await click('button[onclick="openTextDialog(false)"]');
  await click('#dialogFindInput');await key('Escape');
  if(await evaluate('document.getElementById("textDialog").classList.contains("show")'))throw new Error('First Escape failed with empty search focused');
  await click('button[onclick="openTextDialog(false)"]');
  await evaluate(`Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.fixtureCopiedText=text;}}})`);
  await click('#btnCopyText');
  if(await evaluate(`window.fixtureCopiedText===${JSON.stringify(entry.text)}`)!==true)throw new Error('Copy did not preserve exact OCR');
  report.checks.push({copy_exact:true,clipboard_transport:'stubbed; personal clipboard untouched'});
  await click('#textDialogClose');
  await click('button[onclick="openTextDialog(false)"]');
  await key('f',['meta']);await win.webContents.insertText('meeting');
  await until(()=>evaluate('document.querySelectorAll("#extractedText mark").length===3'));
  await click('#dialogFindNext');
  if(await evaluate('document.getElementById("dialogFindCount").textContent')!=='2/3')throw new Error('Find next did not advance');
  await key('Escape');
  if(await evaluate('document.getElementById("textDialog").classList.contains("show")'))throw new Error('First Escape failed with an active search');
  await click('button[onclick="openTextDialog(false)"]');
  await click('#extractedText');
  await key('a',['meta']);
  const selected=await evaluate('({text:window.getSelection().toString(),active:document.activeElement.id,range:window.getSelection().rangeCount?window.getSelection().getRangeAt(0).toString():null})');
  // Chromium omits a final visual newline in Selection.toString(); the DOM
  // range and Copy text must still contain the exact original OCR string.
  if(selected.range!==entry.text||selected.active!=='extractedText')throw new Error('Selection range differs from OCR: '+JSON.stringify(selected));
  await evaluate('window.getSelection().removeAllRanges()');
  const timeline=await evaluate('document.getElementById("timelineSlider").value');
  await key('Left');
  if(await evaluate('document.getElementById("timelineSlider").value')!==timeline)throw new Error('Document keyboard navigation moved the timeline');
  await evaluate('document.getElementById("textDialogClose").focus()');await key('Tab',['shift']);
  if(await evaluate('document.activeElement.id')!=='extractedText')throw new Error('Reverse Tab escaped the document');
  await key('Tab');if(await evaluate('document.activeElement.id')!=='textDialogClose')throw new Error('Forward Tab escaped the document');
  await click('#modeLabelAi');
  if(!(await evaluate('document.getElementById("extractedText").textContent')).includes('No AI transcription'))throw new Error('Missing AI state not shown');
  await key('Escape');
  if(await evaluate('document.getElementById("textDialog").classList.contains("show")'))throw new Error('First Escape failed from an AI source tab');
  await click('button[onclick="openTextDialog(false)"]');await click('#modeLabelBase');
  await key('Comma',['meta']);await until(()=>evaluate('!document.getElementById("settingsModalOverlay").hidden'));
  await key('Escape');
  if(await evaluate('!document.getElementById("textDialog").classList.contains("show")'))throw new Error('Settings Escape also closed the Text window underneath');
  await key('Escape');
  if(await evaluate('document.getElementById("textDialog").classList.contains("show")'))throw new Error('Escape failed after closing foreground Settings');
  await click('button[onclick="openTextDialog(false)"]');
  report.checks.push({find_matches:3,find_next:true,escape_closes_plain_empty_search_active_search_and_ai_tab:true,foreground_settings_escape_order:true,select_all_exact:true,timeline_unchanged:true,focus_trap:true,no_ai_state:true});
  const close=await evaluate(`(()=>{const b=document.getElementById('textDialogClose').getBoundingClientRect(),s=document.querySelector('#textDialogClose svg').getBoundingClientRect();return {dx:s.x+s.width/2-b.x-b.width/2,dy:s.y+s.height/2-b.y-b.height/2,square:b.width===b.height};})()`);
  if(!close.square||Math.abs(close.dx)>.1||Math.abs(close.dy)>.1)throw new Error('Close button is not square and centered');
  report.checks.push({close_icon:close});
  await click('#textDialogClose');
  await frame(manifest.identifiers.document_ai);await click('button[onclick="openTextDialog(false)"]');await click('#modeLabelAi');
  await until(()=>evaluate('document.getElementById("extractedText").textContent.includes("Meeting summary & <keep literally>")'));
  report.checks.push({saved_ai_text:true,cloud_requests:0});
  await click('#textDialogClose');await frame(local);await click('button[onclick="openTextDialog(false)"]');await click('#modeLabelBase');
  await until(()=>evaluate(`document.getElementById('extractedText').textContent===${JSON.stringify(entry.text)}`));
  win.setContentSize(820,650);await pause(250);await win.webContents.capturePage();
  const small=await evaluate(`(()=>{const d=document.querySelector('.td-document-footer').getBoundingClientRect(),t=document.getElementById('extractedText'),p=document.querySelector('.text-dialog').getBoundingClientRect();return {footer_visible:d.bottom<=innerHeight,scrollable:t.scrollHeight>t.clientHeight,body_height:t.clientHeight,overflow:[...document.querySelectorAll('#textDialog button,#textDialog input')].filter(n=>n.getClientRects().length&&n.getBoundingClientRect().right>p.right+1).map(n=>n.id)};})()`);
  if(!small.footer_visible||!small.scrollable||small.overflow.length)throw new Error('Small document view clips content or its controls');
  fs.writeFileSync(path.join(output,'document-small.png'),(await win.webContents.capturePage()).toPNG());report.checks.push({small_window:small});
  win.setContentSize(680,650);await pause(200);
  const narrow=await evaluate('({source_hidden:getComputedStyle(document.querySelector(".td-source")).display==="none",footer:document.querySelector(".td-document-footer").getBoundingClientRect().bottom,height:innerHeight})');
  if(!narrow.source_hidden||narrow.footer>narrow.height)throw new Error('Narrow document layout failed to reflow');
  report.checks.push({narrow_window:narrow});
  await finish();
}).catch(finish);
