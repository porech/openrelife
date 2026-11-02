/* Run with the repository's Electron executable. Uses a separate synthetic store. */
process.on('uncaughtException', error => { console.error(error); process.exit(1); });
const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn, execFileSync } = require('node:child_process');
const project = path.resolve(__dirname, '../..');
const output = path.resolve(process.argv[2] || path.join(project,'logs/settings-sidebar-native'));
const python = path.join(project,'.venv/bin/python');
const control = crypto.randomBytes(32).toString('base64url');
fs.mkdirSync(output,{recursive:true});
app.setPath('userData',path.join(output,'electron-profile'));
app.disableHardwareAcceleration();
ipcMain.handle('is-accessibility-trusted',()=>true);
const report={native_electron:true,offscreen_renderer:false,personal_capture_paused:false,checks:[],errors:[]};
let backend,win,log,finishing=false;
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const watchdog=setTimeout(()=>finish(new Error('Settings acceptance exceeded 90 seconds')),90000);
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
  await until(()=>evaluate('!!window.openrelifeSettings && !!window.openrelifeAgentUI'));
  win.webContents.debugger.attach('1.3');
  await win.webContents.debugger.sendCommand('Emulation.setFocusEmulationEnabled',{enabled:true});
  win.webContents.focus();
  async function key(keyCode,modifiers=[]){
    const codes={Tab:['Tab',9],Escape:['Escape',27],Left:['ArrowLeft',37],Backspace:['Backspace',8],Enter:['Enter',13],a:['a',65],k:['k',75]};
    const [name,virtual]=codes[keyCode];
    const mask=modifiers.reduce((sum,name)=>sum+({meta:4,shift:8,control:2,alt:1}[name]||0),0);
    const code=keyCode==='a'?'KeyA':name;
    const commands=keyCode==='a'&&modifiers.includes('meta')?['selectAll']:keyCode==='Left'?['moveLeft']:keyCode==='Backspace'?['deleteBackward']:[];
    for(const type of ['rawKeyDown','keyUp'])await win.webContents.debugger.sendCommand('Input.dispatchKeyEvent',{type,key:name,code,windowsVirtualKeyCode:virtual,modifiers:mask,...(type==='rawKeyDown'?{commands}:{})});
    await pause(75);
  }
  async function click(selector){
    const point=await evaluate(`(()=>{const node=document.querySelector(${JSON.stringify(selector)});if(!node)throw new Error('Missing target');node.scrollIntoView({block:'nearest'});const r=node.getBoundingClientRect();return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};})()`);
    for(const type of ['mousePressed','mouseReleased'])await win.webContents.debugger.sendCommand('Input.dispatchMouseEvent',{type,button:'left',clickCount:1,...point});await pause(100);
  }
  await key('Tab');if(await evaluate('document.activeElement.id')!=='searchInput')throw new Error('First Tab focused '+await evaluate('document.activeElement.outerHTML'));
  await click('#agentOpen');await until(()=>evaluate('!document.getElementById("agentPanel").hidden'));
  await click('#agentQuestion');await win.webContents.insertText('Keep this question');
  await click('#agentConfigure');await until(()=>evaluate('!document.getElementById("settingsFields").disabled'));
  if(await evaluate('document.getElementById("settingsError").hidden')!==true)throw new Error('Preferences failed to load');
  const front=await evaluate(`(()=>{const n=document.querySelector('.preferences');const r=n.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+30).closest('.preferences')!==null;})()`);
  if(!front)throw new Error('Preferences did not layer above Ask AI');
  await pause(150);fs.writeFileSync(path.join(output,'ai-settings-desktop.png'),(await win.webContents.capturePage()).toPNG());
  report.checks.push({first_tab:'searchInput',settings_above_agent:true,desktop_width:1280});
  for(const section of ['general','capture','storage','ai','privacy','advanced']){
    await click(`[data-settings-section="${section}"]`);
    const geometry=await evaluate(`(()=>{const page=document.querySelector('[data-settings-pane="${section}"]');const shell=document.querySelector('.preferences').getBoundingClientRect();const group=page.querySelector('.preferences-grid')||page.querySelector('.preferences-group')?.parentElement;const nodes=[...page.querySelectorAll('input,textarea,select,button,code')].filter(n=>n.getClientRects().length);return {title:document.getElementById('settingsTitle').textContent,columns:group?getComputedStyle(group).gridTemplateColumns:null,overflow:nodes.filter(n=>{const r=n.getBoundingClientRect();return r.right>shell.right+1||r.left<shell.left-1}).map(n=>n.id)};})()`);
    if(geometry.overflow.length)throw new Error(`Horizontal overflow in ${section}`);report.checks.push({section,...geometry});
  }
  await click('[data-settings-section="ai"]');
  await click('[data-settings-ai="ocr"]');
  if(await evaluate('document.getElementById("aiApiKey").type')!=='password')throw new Error('Key input is not private');
  await click('[data-settings-ai="mcp"]');
  if(!(await evaluate('document.getElementById("agentMcpCommand").textContent')).includes('openrelife.mcp_server'))throw new Error('MCP command missing');
  await click('[data-settings-ai="agent"]');
  await click('#agentCommand');await key('a',['meta']);await win.webContents.insertText('/bin/cat');
  await key('Left');await win.webContents.insertText('X');
  const caret=await evaluate('({value:document.getElementById("agentCommand").value,start:document.getElementById("agentCommand").selectionStart})');
  if(caret.value!=='/bin/caXt')throw new Error('Caret arrow check failed: '+JSON.stringify(caret));
  await key('Backspace');
  await key('k',['meta']);
  if(await evaluate('document.activeElement.id')!=='agentCommand')throw new Error('Command+K stole focus from settings');
  const timeline=await evaluate('document.getElementById("timelineSlider").value');
  await click('[data-settings-section="storage"]');await click('[data-settings-section="ai"]');
  if(await evaluate('document.getElementById("agentCommand").value')!=='/bin/cat')throw new Error('Tab navigation discarded the command draft');
  await evaluate('document.getElementById("settingsClose").focus()');
  await key('Tab',['shift']);
  if(await evaluate('document.activeElement.id')!=='settingsSave')throw new Error('Reverse Tab escaped the settings dialog');
  await key('Tab');
  if(await evaluate('document.activeElement.id')!=='settingsClose')throw new Error('Forward Tab escaped the settings dialog');
  await click('#settingsSave');await until(()=>evaluate('document.getElementById("settingsStatus").textContent === "All changes saved"'));
  if((await api('/api/agent/settings')).command!=='/bin/cat')throw new Error('Saved command not visible through the public API');
  if(await evaluate('document.getElementById("timelineSlider").value')!==timeline)throw new Error('Settings keyboard input changed the timeline');
  report.checks.push({native_caret:true,focus_trap_both_directions:true,command_k_does_not_steal_focus:true,draft_survives_navigation:true,saved_command:'/bin/cat',timeline_unchanged:true});
  await click('#settingsClose');await until(()=>evaluate('document.getElementById("settingsModalOverlay").hidden'));
  await click('#agentConfigure');await until(()=>evaluate('!document.getElementById("settingsFields").disabled'));
  await click('#settingsClose');
  await click('#agentConfigure');await until(()=>evaluate('!document.getElementById("settingsFields").disabled'));
  await key('Escape');
  const escaped=await evaluate('({settings:document.getElementById("settingsModalOverlay").hidden,agent:document.getElementById("agentPanel").hidden,question:document.getElementById("agentQuestion").value})');
  if(!escaped.settings||escaped.agent||escaped.question!=='Keep this question')throw new Error('Escape did not close only the foreground settings');
  report.checks.push({escape:escaped});
  await click('#agentConfigure');await until(()=>evaluate('!document.getElementById("settingsFields").disabled'));
  win.setContentSize(820,650);await pause(200);
  const narrow=await evaluate('({width:document.querySelector(".preferences").getBoundingClientRect().width,footer:document.querySelector(".preferences-footer").getBoundingClientRect().bottom,height:innerHeight})');
  if(narrow.footer>narrow.height)throw new Error('Settings footer clipped at small height');
  fs.writeFileSync(path.join(output,'ai-settings-small.png'),(await win.webContents.capturePage()).toPNG());
  report.checks.push({small_window:narrow});
  win.setContentSize(680,650);await pause(200);
  const stacked=await evaluate('({columns:getComputedStyle(document.querySelector(".preferences-agent-grid")).gridTemplateColumns,footer:document.querySelector(".preferences-footer").getBoundingClientRect().bottom,height:innerHeight})');
  if(stacked.columns.trim().split(/\s+/).length!==1||stacked.footer>stacked.height)throw new Error('Narrow settings layout did not reflow');
  report.checks.push({narrow_window:stacked});
  await finish();
}).catch(finish);
