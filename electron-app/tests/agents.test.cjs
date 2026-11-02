/* Product HTML/JS contracts in a simulated DOM, not a native visual acceptance test. */
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn, execFileSync } = require('node:child_process');
const { JSDOM } = require('jsdom');

const project = path.resolve(__dirname, '../..');
const python = path.join(project, '.venv/bin/python');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'openrelife-agent-dom-'));
const control = crypto.randomBytes(32).toString('base64url');
const connections = new Set();
let backend, base, manifest, log;
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await check()) return; await wait(50); }
  throw new Error('DOM/API condition did not become true');
}
async function api(route, body, headers = {}) {
  const response = await fetch(base + route, { method: body ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json', 'X-OpenReLife-Control': control, ...headers },
    body: body ? JSON.stringify(body) : undefined });
  const value = await response.json();
  assert.equal(response.status, 200, JSON.stringify(value));
  return value;
}
before(async () => {
  execFileSync(python, ['tests/manual/check_agents_public.py', '--prepare-only', '--output', root], { cwd: project });
  const settings = JSON.parse(fs.readFileSync(path.join(root, 'archive/settings.json')));
  manifest = JSON.parse(fs.readFileSync(path.join(root, 'fixture-manifest.json')));
  base = `http://127.0.0.1:${settings.server_port}`;
  log = fs.createWriteStream(path.join(root, 'backend.log'));
  await startBackend();
});
async function startBackend() {
  backend = spawn(python, ['-m', 'openrelife.app', '--storage-path', path.join(root, 'archive'), '--no-capture'],
    { cwd: project, env: { ...process.env, OPENRELIFE_CONTROL_FD: '3', PYTHONDONTWRITEBYTECODE: '1' }, stdio: ['ignore', 'pipe', 'pipe', 'pipe'] });
  backend.stdio[3].end(control); backend.stdout.pipe(log, { end: false }); backend.stderr.pipe(log, { end: false });
  await until(async () => {
    if (backend.exitCode !== null) throw new Error('Fixture backend exited; ' + root);
    try { await api('/api/recording-status'); return true; } catch { return false; }
  }, 30000);
}
async function stopBackend() {
  if (backend && backend.exitCode === null) {
    backend.kill('SIGTERM'); await Promise.race([new Promise(resolve => backend.once('exit', resolve)), wait(3000)]);
    if (backend.exitCode === null) backend.kill('SIGKILL');
  }
}
after(async () => {
  for (const dom of connections) dom.window.close();
  await stopBackend();
  if (log) log.end();
  const output = path.join(project, 'logs/agent-dom-test-location.json');
  fs.writeFileSync(output, JSON.stringify({ artifact_directory: root, native_visual_test: false }, null, 2));
});

async function page({ loadPreferences = true } = {}) {
  const response = await fetch(base + '/', { headers: { 'X-OpenReLife-Control': control } });
  assert.equal(response.status, 200);
  const html = await response.text();
  const dom = new JSDOM(html, { url: base, runScripts: 'outside-only', pretendToBeVisual: true });
  connections.add(dom);
  const win = dom.window;
  for (const link of win.document.querySelectorAll('link[href^="/assets/"]')) {
    const css = await fetch(base + link.getAttribute('href'), { headers: { 'X-OpenReLife-Control': control } }).then(result => result.text());
    const style = win.document.createElement('style'); style.textContent = css; win.document.head.append(style);
  }
  win.AbortController = globalThis.AbortController;
  win.AbortSignal = globalThis.AbortSignal;
  win.fetch = (url, options = {}) => fetch(new URL(url, base), {
    ...options, headers: { ...(options.headers || {}), 'X-OpenReLife-Control': control } });
  win.alert = message => { win.testAlert = String(message); };
  win.confirm = () => false;
  win.scrollTo = () => {};
  win.HTMLElement.prototype.scrollIntoView = () => {};
  const inline = [...win.document.querySelectorAll('script:not([src])')].map(script => script.textContent).join('\n');
  const scripts = [inline];
  for (const script of win.document.querySelectorAll('script[src^="/assets/"]')) {
    const content = await fetch(base + script.getAttribute('src'), { headers: { 'X-OpenReLife-Control': control } }).then(result => result.text());
    scripts.push(content);
  }
  // A single eval preserves the shared global lexical scope of browser scripts.
  win.eval(scripts.join('\n'));
  // outside-only intentionally disables inline attributes in jsdom. Bind the
  // product's actual handlers as a browser would, rather than replacing them.
  for (const element of win.document.querySelectorAll('[onclick]')) {
    element.onclick = new win.Function('event', element.getAttribute('onclick'));
  }
  if (loadPreferences) {
    await win.openSettings('ai', 'agent');
    assert.equal(win.document.getElementById('settingsError').hidden, true);
    win.closeSettings();
  }
  return dom;
}

async function saveAgentSettings(win, doc) {
  await win.openSettings('ai', 'agent');
  doc.getElementById('agentCommand').dispatchEvent(new win.Event('input', { bubbles: true }));
  doc.getElementById('settingsSave').click();
  await until(() => doc.getElementById('settingsStatus').textContent === 'All changes saved');
  assert.equal(doc.getElementById('settingsError').hidden, true);
  win.closeSettings();
}

test('Sidebar settings preserve drafts, layer above Ask AI, and save only changed preferences', async () => {
  const dom = await page(); const win = dom.window, doc = win.document;
  const initial = await api('/api/settings');
  const posts = [], originalFetch = win.fetch;
  win.fetch = (url, options = {}) => {
    if (options.method === 'POST' && ['/api/settings', '/api/agent/settings', '/api/config'].includes(String(url))) {
      posts.push({ url: String(url), body: JSON.parse(options.body) });
    }
    return originalFetch(url, options);
  };
  doc.getElementById('agentOpen').click();
  await until(() => !doc.getElementById('agentPanel').hidden);
  doc.getElementById('agentQuestion').value = 'Keep my question';
  doc.getElementById('agentConfigure').click();
  await until(() => !doc.getElementById('settingsFields').disabled);
  assert.equal(doc.querySelector('[data-settings-pane="ai"]').hidden, false);
  const settingsZ = Number(win.getComputedStyle(doc.getElementById('settingsModalOverlay')).zIndex);
  assert.ok(settingsZ > Number(win.getComputedStyle(doc.getElementById('agentPanel')).zIndex));
  doc.querySelector('[data-settings-section="capture"]').click();
  const interval = doc.getElementById('intervalInput'); interval.value = String(initial.interval + 1);
  interval.dispatchEvent(new win.Event('input', { bubbles: true }));
  doc.querySelector('[data-settings-section="storage"]').click();
  doc.querySelector('[data-settings-section="capture"]').click();
  assert.equal(interval.value, String(initial.interval + 1));
  let hidden = 0; win.electronAPI = { hideWindow: () => { hidden++; } };
  interval.dispatchEvent(new win.KeyboardEvent('keydown', { key:'Escape', bubbles:true, cancelable:true }));
  assert.equal(doc.getElementById('settingsModalOverlay').hidden, true);
  assert.equal(doc.getElementById('agentPanel').hidden, false);
  assert.equal(doc.getElementById('agentQuestion').value, 'Keep my question');
  assert.equal(hidden, 0);
  await win.openSettings('capture');
  assert.equal(interval.value, String(initial.interval + 1));
  doc.getElementById('settingsSave').click();
  await until(() => doc.getElementById('settingsStatus').textContent === 'All changes saved');
  assert.deepEqual(posts, [{url:'/api/settings',body:{interval:String(initial.interval + 1)}}]);
  assert.equal((await api('/api/settings')).interval, initial.interval + 1);
  assert.equal((await api('/api/settings')).retention_days, initial.retention_days);
  interval.value = '20'; interval.dispatchEvent(new win.Event('input', { bubbles:true }));
  doc.getElementById('settingsDiscard').click();
  assert.equal(interval.value, String(initial.interval + 1));
  assert.equal(doc.getElementById('settingsSave').disabled, true);
  const classic = await fetch(base + '/classic', { headers: { 'X-OpenReLife-Control': control } });
  assert.equal(classic.status, 200);
  const classicHtml = await classic.text();
  assert.match(classicHtml, /settings=ai-ocr/);
  assert.doesNotMatch(classicHtml, /id="aiApiKey"/);
  dom.window.close(); connections.delete(dom);
});

test('Settings keep a saved provider key and validate new providers before any write', async () => {
  const dom = await page(); const win = dom.window, doc = win.document;
  await win.openSettings('ai','ocr');
  doc.getElementById('aiProvider').value = 'gemini';
  doc.getElementById('aiApiKey').value = 'synthetic-key-never-sent-to-a-provider';
  doc.getElementById('aiApiKey').dispatchEvent(new win.Event('input',{bubbles:true}));
  doc.getElementById('settingsSave').click();
  await until(() => doc.getElementById('settingsStatus').textContent === 'All changes saved');
  assert.equal(doc.getElementById('aiApiKey').value, '');
  const original = await api('/api/config?full=true');
  const posts = [], originalFetch=win.fetch;
  win.fetch=(url,options={})=>{if(options.method==='POST')posts.push(String(url));return originalFetch(url,options);};
  doc.getElementById('aiProvider').value='claude';
  doc.getElementById('aiProvider').dispatchEvent(new win.Event('change',{bubbles:true}));
  doc.getElementById('qualitySelect').value='low';
  doc.getElementById('settingsSave').click();
  assert.match(doc.getElementById('settingsError').textContent,/Enter a key/);
  assert.deepEqual(posts,[]);
  assert.deepEqual(await api('/api/config?full=true'),original);
  assert.equal(doc.activeElement.id,'aiApiKey');
  doc.getElementById('settingsDiscard').click();
  assert.equal(doc.getElementById('aiProvider').value,'gemini');
  dom.window.close();connections.delete(dom);
});

test('Unavailable settings cannot save defaults, and retry loads the real values', async () => {
  const dom=await page({loadPreferences:false});const win=dom.window,doc=win.document;
  const originalFetch=win.fetch;
  win.fetch=(url,options={})=>String(url)==='/api/settings'?Promise.reject(new Error('Synthetic offline failure')):originalFetch(url,options);
  await win.openSettings('storage');
  assert.equal(doc.getElementById('settingsFields').disabled,true);
  assert.equal(doc.getElementById('settingsSave').disabled,true);
  assert.match(doc.getElementById('settingsError').textContent,/Synthetic offline failure/);
  win.fetch=originalFetch;doc.getElementById('settingsRetry').click();
  await until(()=>!doc.getElementById('settingsFields').disabled);
  assert.equal(doc.getElementById('retentionSelect').value,String((await api('/api/settings')).retention_days));
  assert.equal(doc.getElementById('settingsError').hidden,true);
  dom.window.close();connections.delete(dom);
});

test('A partial settings failure retains unsaved edits and does not repeat successful writes', async () => {
  const dom=await page();const win=dom.window,doc=win.document;
  await win.openSettings('ai','agent');
  doc.getElementById('agentPreset').value='custom';
  doc.getElementById('agentPreset').dispatchEvent(new win.Event('change',{bubbles:true}));
  doc.getElementById('agentCommand').value='/bin/cat';
  doc.getElementById('aiApiKey').value='synthetic-replacement-key';
  doc.getElementById('agentCommand').dispatchEvent(new win.Event('input',{bubbles:true}));
  const originalFetch=win.fetch,posts=[];let fail=true;
  win.fetch=(url,options={})=>{
    if(options.method==='POST')posts.push(String(url));
    if(String(url)==='/api/config'&&options.method==='POST'&&fail)return Promise.resolve({ok:false,status:503,json:async()=>({error:'Synthetic config write failure'})});
    return originalFetch(url,options);
  };
  doc.getElementById('settingsSave').click();
  await until(()=>doc.getElementById('settingsStatus').textContent==='Review the remaining changes');
  assert.match(doc.getElementById('settingsError').textContent,/Some changes were saved/);
  assert.equal(doc.getElementById('aiApiKey').value,'synthetic-replacement-key');
  assert.equal((await api('/api/agent/settings')).command,'/bin/cat');
  fail=false;doc.getElementById('settingsSave').click();
  await until(()=>doc.getElementById('settingsStatus').textContent==='All changes saved');
  assert.deepEqual(posts,['/api/agent/settings','/api/config','/api/config']);
  assert.equal(doc.getElementById('aiApiKey').value,'');
  dom.window.close();connections.delete(dom);
});

test('Command+Enter and AI button use saved command; Markdown and previews stay safe', async () => {
  const dom = await page(); const win = dom.window; const doc = win.document;
  doc.getElementById('agentOpen').click();
  await until(() => doc.getElementById('agentCommand').value.length > 0);
  const answer = `## Fixture\n\n**Bold** and *italic*, with [source](openrelife://frame/${manifest.identifiers.meeting_first}).\n\n- First\n- Second\n\n| Time | Status |\n| --- | --- |\n| 10:00 | Joined |\n\n\`\`\`html\n<img src=x onerror="window.compromised=true">\n\`\`\`\n\n<img src=x onerror="window.compromised=true"><script>window.compromised=true</script> [unsafe](javascript:alert(1)) [external](https://example.com) ![remote](https://example.com/tracking.gif)`;
  const program = `import json; print(json.dumps({'answer': ${JSON.stringify(answer)}}))`;
  doc.getElementById('agentPreset').value = 'custom';
  doc.getElementById('agentPreset').dispatchEvent(new win.Event('change', { bubbles: true }));
  doc.getElementById('agentCommand').value = `${JSON.stringify(python)} -c ${JSON.stringify(program)}`;
  await saveAgentSettings(win, doc);
  doc.getElementById('searchInput').value = 'Synthetic DOM command test';
  const event = new win.KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true, cancelable: true });
  doc.getElementById('searchInput').dispatchEvent(event);
  assert.equal(event.defaultPrevented, true);
  await until(() => doc.getElementById('agentState').textContent.startsWith('completed'), 20000);
  assert.match(doc.getElementById('agentAnswer').textContent, /Fixture/);
  assert.match(doc.getElementById('agentAnswer').textContent, /No archive tools were used/);
  assert.equal(doc.querySelector('#agentAnswer strong').textContent, 'Bold');
  assert.equal(doc.querySelector('#agentAnswer em').textContent, 'italic');
  assert.equal(doc.querySelectorAll('#agentAnswer li').length, 2);
  assert.equal(doc.querySelector('#agentAnswer td').textContent, '10:00');
  assert.match(doc.querySelector('#agentAnswer pre code').textContent, /<img src=x/);
  assert.equal(doc.querySelector('#agentAnswer img'), null);
  assert.equal(doc.querySelector('#agentAnswer script'), null);
  assert.equal(win.compromised, undefined);
  assert.equal(doc.querySelectorAll('#agentAnswer a').length, 1);
  assert.equal(doc.getElementById('agentTraceDownload').hidden, false);
  const source = doc.querySelector('#agentAnswer a');
  source.dispatchEvent(new win.MouseEvent('mouseenter'));
  await until(() => !doc.getElementById('agentFramePreview').hidden);
  const thumbnail = doc.querySelector('#agentFramePreview img');
  assert.equal(new URL(thumbnail.src).pathname, `/api/frames/${manifest.identifiers.meeting_first}/thumbnail`);
  assert.equal(source.getAttribute('aria-describedby'), 'agentFramePreview');
  const image = await fetch(thumbnail.src, { headers: { 'X-OpenReLife-Control': control } });
  assert.equal(image.status, 200);
  assert.equal(image.headers.get('content-type'), 'image/jpeg');
  assert.equal(image.headers.get('cache-control'), 'private, no-store');
  assert.equal((await fetch(thumbnail.src)).status, 403);
  const decoded = await require('canvas').loadImage(Buffer.from(await image.arrayBuffer()));
  assert.ok(decoded.width <= 480 && decoded.height <= 300);
  thumbnail.dispatchEvent(new win.Event('error'));
  assert.match(doc.getElementById('agentFramePreview').textContent, /unavailable/);
  source.dispatchEvent(new win.MouseEvent('mouseleave'));
  assert.equal(doc.getElementById('agentFramePreview').hidden, true);
  source.focus();
  assert.equal(doc.getElementById('agentFramePreview').hidden, false);
  doc.getElementById('agentClose').click();
  assert.equal(doc.getElementById('agentFramePreview').hidden, true);
  assert.equal(doc.getElementById('searchInput').placeholder, 'Search your history…');
  assert.equal(doc.getElementById('agentOpen').textContent, 'Ask AI');
  assert.equal(doc.getElementById('agentOpen').parentElement.id, 'searchWrapper');
  const priorRun = doc.getElementById('agentTraceDownload').href;
  const withEvidence = `import json,os,urllib.request; connection=json.load(open(os.environ['OPENRELIFE_CONNECTION'])); request=urllib.request.Request(connection['url']+'/api/agent/tools',data=json.dumps({'tool':'find_frames','arguments':{'frame_ids':[${JSON.stringify(manifest.identifiers.meeting_first)}]}}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['OPENRELIFE_RUN_TOKEN']}); json.load(urllib.request.urlopen(request)); print(json.dumps({'answer':${JSON.stringify(answer)}}))`;
  doc.getElementById('agentCommand').value = `${JSON.stringify(python)} -c ${JSON.stringify(withEvidence)}`;
  await saveAgentSettings(win, doc);
  await until(async () => (await api('/api/agent/settings')).command.includes('urllib.request'));
  doc.getElementById('searchInput').value = 'Synthetic AI button query';
  doc.getElementById('agentOpen').click();
  await until(() => doc.getElementById('agentTraceDownload').href !== priorRun && doc.getElementById('agentState').textContent.startsWith('completed'), 20000);
  assert.equal(doc.getElementById('agentQuestion').value, 'Synthetic AI button query');
  assert.equal(doc.getElementById('agentPanel').hidden, false);
  const evidence = doc.querySelector('#agentEvidence a');
  assert.ok(evidence);
  evidence.dispatchEvent(new win.MouseEvent('mouseenter'));
  await until(() => !doc.getElementById('agentFramePreview').hidden);
  assert.match(doc.querySelector('#agentFramePreview img').src, new RegExp(manifest.identifiers.meeting_first));
  evidence.dispatchEvent(new win.MouseEvent('mouseleave'));
  evidence.dispatchEvent(new win.MouseEvent('mouseenter'));
  evidence.dispatchEvent(new win.MouseEvent('mouseleave'));
  await wait(300);
  assert.equal(doc.getElementById('agentFramePreview').hidden, true);
  dom.window.close(); connections.delete(dom);
});

test('Focused controls keep arrow keys while timeline navigation still works', async () => {
  const dom = await page(); const win = dom.window; const doc = win.document;
  doc.getElementById('agentOpen').click();
  await until(() => doc.getElementById('agentCommand').value.length > 0);
  const slider = doc.getElementById('timelineSlider'); slider.value = '500';
  const editor = doc.createElement('div'); editor.contentEditable = 'true';
  editor.setAttribute('contenteditable', 'true'); editor.tabIndex = 0;
  const nested = doc.createElement('span'); nested.textContent = 'Editable text';
  editor.append(nested); doc.body.append(editor);
  const controls = ['agentQuestion', 'agentCommand', 'agentTimeout', 'agentPreset', 'searchInput'].map(id => doc.getElementById(id));
  controls.push(nested);
  for (const control of controls) {
    control.focus();
    for (const key of ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown']) {
      for (const modifiers of [{}, { shiftKey: true }, { metaKey: true }, { altKey: true }]) {
        const event = new win.KeyboardEvent('keydown', { key, ...modifiers, bubbles: true, cancelable: true });
        control.dispatchEvent(event);
        assert.equal(event.defaultPrevented, false, `${control.id || 'contenteditable'} ${key} was intercepted`);
        assert.equal(slider.value, '500', `${control.id || 'contenteditable'} ${key} moved the timeline`);
      }
    }
  }
  doc.activeElement.blur();
  doc.body.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
  assert.equal(slider.value, '501');
  doc.body.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, cancelable: true }));
  assert.equal(slider.value, '500');
  const handled = new win.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true });
  handled.preventDefault(); doc.body.dispatchEvent(handled);
  assert.equal(slider.value, '500');
  doc.body.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowRight', isComposing: true, bubbles: true, cancelable: true }));
  assert.equal(slider.value, '500');
  dom.window.close(); connections.delete(dom);
});

test('Ask AI locks the question while running and restores editing on Stop, completion and failure', async () => {
  const dom = await page(); const win = dom.window; const doc = win.document;
  const question = doc.getElementById('agentQuestion'), field = doc.getElementById('agentQuestionField');
  doc.getElementById('searchInput').focus();
  assert.ok(doc.getElementById('paletteActions').classList.contains('show'));
  assert.equal(doc.querySelector('#agentPaletteAsk kbd').textContent, '⌘Enter');
  doc.getElementById('agentPaletteAsk').click();
  await until(() => doc.getElementById('agentCommand').value.length > 0);
  assert.equal(doc.getElementById('agentPanel').hidden, false);
  assert.equal(doc.getElementById('paletteActions').classList.contains('show'), false);
  doc.getElementById('agentPreset').value = 'custom';
  doc.getElementById('agentPreset').dispatchEvent(new win.Event('change'));
  async function command(program) {
    const value = `${JSON.stringify(python)} -c ${JSON.stringify(program)}`;
    doc.getElementById('agentCommand').value = value;
    doc.getElementById('agentTimeout').value = '60';
    await saveAgentSettings(win, doc);
    await until(async () => (await api('/api/agent/settings')).command === value);
  }
  function assertEditing(enabled) {
    assert.equal(question.disabled, !enabled);
    assert.equal(field.classList.contains('is-running'), !enabled);
    assert.equal(field.getAttribute('aria-busy'), String(!enabled));
  }
  await command('import time; time.sleep(30)');
  question.value = 'Original question'; doc.getElementById('agentStart').click();
  assertEditing(false);
  await until(() => !doc.getElementById('agentCancel').disabled);
  doc.getElementById('searchInput').value = 'Another search';
  doc.getElementById('agentOpen').click();
  assert.equal(question.value, 'Original question');
  doc.getElementById('agentCancel').click();
  assertEditing(true);
  assert.equal(doc.getElementById('agentStart').disabled, true);
  question.value = 'New draft after Stop';
  await until(() => doc.getElementById('agentState').textContent.startsWith('cancelled'));
  assertEditing(true);
  assert.equal(doc.getElementById('agentStart').disabled, false);
  assert.equal(question.value, 'New draft after Stop');
  await command('import time; time.sleep(1); print("Finished")');
  doc.getElementById('agentStart').click(); assertEditing(false);
  await until(() => doc.getElementById('agentState').textContent.startsWith('completed'));
  assertEditing(true);
  await command('import sys; sys.exit(2)');
  doc.getElementById('agentStart').click(); assertEditing(false);
  await until(() => doc.getElementById('agentState').textContent.startsWith('failed'));
  assertEditing(true);
  question.value = 'x'.repeat(12001);
  doc.getElementById('agentStart').click(); assertEditing(false);
  await until(() => doc.getElementById('agentState').textContent.includes('12000'));
  assertEditing(true);
  dom.window.close(); connections.delete(dom);
});

test('Escape closes the Ask AI panel before hiding the app and preserves the draft', async () => {
  const dom = await page(); const win = dom.window; const doc = win.document;
  let hidden = 0;
  win.electronAPI = { hideWindow: () => { hidden++; } };
  doc.getElementById('agentOpen').click();
  await until(() => !doc.getElementById('agentPanel').hidden);
  const question = doc.getElementById('agentQuestion'); question.value = 'Keep this draft'; question.focus();
  question.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', isComposing: true, bubbles: true, cancelable: true }));
  assert.equal(doc.getElementById('agentPanel').hidden, false);
  const escape = new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
  question.dispatchEvent(escape);
  assert.equal(escape.defaultPrevented, true);
  assert.equal(doc.getElementById('agentPanel').hidden, true);
  assert.equal(question.value, 'Keep this draft');
  assert.equal(hidden, 0);
  doc.body.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  assert.equal(hidden, 1);
  dom.window.close(); connections.delete(dom);
});

test('Deletion preview is a filtered timeline; adjustment and cancellation do not delete', async () => {
  const connection = JSON.parse(fs.readFileSync(path.join(root, 'archive/agent-connection.json')));
  const selected = [manifest.identifiers.telegram_native, manifest.identifiers.telegram_web];
  const proposal = await api('/api/agent/tools', { tool: 'propose_deletion', arguments: { frame_ids: selected, reason: 'DOM selection fixture' } },
    { Authorization: 'Bearer ' + connection.read_token, 'X-OpenReLife-Client': 'dom-fixture' });
  const before = await api('/api/agent/tools', { tool: 'archive_status', arguments: {} }, { Authorization: 'Bearer ' + connection.read_token });
  const dom = await page(); const win = dom.window; const doc = win.document;
  doc.getElementById('agentOpen').click();
  await until(() => doc.querySelector('#agentSelections button[data-action="review"]'));
  doc.querySelector('#agentSelections button[data-action="review"]').click();
  await until(() => doc.getElementById('agentDeletePreview').hidden === false);
  assert.equal(doc.getElementById('timelineSlider').max, '1');
  const previews = [...doc.querySelectorAll('#agentDeleteThumbs img')].map(image => image.src.split('/').at(-1).replace('.webp', ''));
  assert.deepEqual(new Set(previews), new Set(selected));
  doc.getElementById('agentExclude').click();
  await until(() => doc.getElementById('timelineSlider').max === '0' && /1 selected/.test(doc.getElementById('deleteInfo').textContent));
  assert.match(doc.getElementById('deleteInfo').textContent, /1 selected/);
  doc.getElementById('agentResetSelection').click();
  await until(() => doc.getElementById('timelineSlider').max === '1');
  let confirmations = 0;
  win.confirm = () => { confirmations++; return false; };
  doc.getElementById('btnConfirmDelete').click();
  await until(() => confirmations === 1);
  const after = await api('/api/agent/tools', { tool: 'archive_status', arguments: {} }, { Authorization: 'Bearer ' + connection.read_token });
  assert.equal(after.frames, before.frames);
  doc.getElementById('agentExitPreview').click();
  await until(() => doc.getElementById('agentDeletePreview').hidden === true);
  assert.equal(doc.getElementById('timelineSlider').max, String(before.frames - 1));
  doc.getElementById('agentOpen').click();
  await until(() => doc.querySelector('#agentSelections button[data-action="review"]'));
  doc.querySelector('#agentSelections button[data-action="review"]').click();
  await until(() => doc.getElementById('agentDeletePreview').hidden === false);
  let hiddenWindows = 0;
  win.electronAPI = { hideWindow: () => { hiddenWindows++; } };
  doc.getElementById('agentOpen').click();
  await until(() => doc.getElementById('agentPanel').hidden === false);
  doc.body.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  assert.equal(doc.getElementById('agentPanel').hidden, true);
  assert.equal(doc.getElementById('agentDeletePreview').hidden, false);
  doc.body.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  assert.equal(doc.getElementById('agentDeletePreview').hidden, true);
  assert.equal(hiddenWindows, 0);
  assert.equal((await api('/api/agent/tools', { tool: 'archive_status', arguments: {} }, { Authorization: 'Bearer ' + connection.read_token })).frames, before.frames);
  doc.getElementById('agentOpen').click();
  await until(() => doc.querySelector('#agentSelections button[data-action="review"]'));
  doc.querySelector('#agentSelections button[data-action="review"]').click();
  await until(() => doc.getElementById('agentDeletePreview').hidden === false);
  win.goToTimestamp(Number(manifest.identifiers.unrelated));
  assert.equal(doc.getElementById('agentDeletePreview').hidden, true);
  assert.equal(doc.getElementById('timelineSlider').max, String(before.frames - 1));
  dom.window.close(); connections.delete(dom);
});

test('App confirmation API deletes only reviewed fixture frames and purges derived answers', async () => {
  const connection = JSON.parse(fs.readFileSync(path.join(root, 'archive/agent-connection.json')));
  const proposed = await api('/api/agent/tools', { tool: 'propose_deletion', arguments: {
    frame_ids: [manifest.identifiers.telegram_injection], reason: 'Public confirmation fixture' } },
    { Authorization: 'Bearer ' + connection.read_token, 'X-OpenReLife-Client': 'confirmation-fixture' });
  const preview = await api('/api/agent/selections/' + proposed.selection_id);
  const result = await api(`/api/agent/selections/${proposed.selection_id}/delete`, {
    digest: preview.digest, frame_ids: preview.frame_ids });
  assert.equal(result.complete, true);
  assert.deepEqual(result.deleted, [manifest.identifiers.telegram_injection]);
  const deletedThumbnail = await fetch(base + `/api/frames/${manifest.identifiers.telegram_injection}/thumbnail`, { headers: { 'X-OpenReLife-Control': control } });
  assert.equal(deletedThumbnail.status, 404);
  const purged = await api('/api/agent/runs/' + proposed.run_id);
  assert.equal(purged.state, 'purged');
  assert.equal(purged.answer, '');
  const remaining = await api('/api/agent/tools', { tool: 'read_frames', arguments: { frame_ids: [manifest.identifiers.telegram_docs, manifest.identifiers.telegram_injection] } },
    { Authorization: 'Bearer ' + connection.read_token, 'X-OpenReLife-Client': 'post-confirmation' });
  assert.deepEqual(remaining.missing, [manifest.identifiers.telegram_injection]);
  assert.equal(remaining.frames[0].frame_id, manifest.identifiers.telegram_docs);
});

test('A real runner proposal restores its request after backend restart and can be discarded without deletion', async () => {
  let dom = await page(); let win = dom.window; let doc = win.document;
  const answer = '**Restored response** with the meeting evidence.';
  const program = `import json,os,urllib.request; connection=json.load(open(os.environ['OPENRELIFE_CONNECTION'])); request=urllib.request.Request(connection['url']+'/api/agent/tools',data=json.dumps({'tool':'propose_deletion','arguments':{'frame_ids':[${JSON.stringify(manifest.identifiers.meeting_first)}],'reason':'Meeting history fixture'}}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['OPENRELIFE_RUN_TOKEN']}); json.load(urllib.request.urlopen(request)); print(json.dumps({'answer':${JSON.stringify(answer)}}))`;
  await api('/api/agent/settings', { preset: 'custom', command: `${JSON.stringify(python)} -c ${JSON.stringify(program)}`, timeout_seconds: 60 });
  doc.getElementById('agentOpen').click();
  await until(() => doc.getElementById('agentCommand').value.length > 0);
  const query = 'Select the meeting screenshot for review';
  doc.getElementById('agentQuestion').value = query; doc.getElementById('agentStart').click();
  await until(() => doc.getElementById('agentState').textContent.startsWith('completed'));
  const proposal = (await api('/api/agent/selections')).selections.find(item => item.origin_query === query);
  assert.ok(proposal && proposal.context_available);
  const imagePath = path.join(root, 'archive/screenshots', manifest.identifiers.meeting_first + '.webp');
  const before = fs.readFileSync(imagePath);
  dom.window.close(); connections.delete(dom);
  await stopBackend(); await startBackend();
  dom = await page(); win = dom.window; doc = win.document;
  let started = 0;
  const originalFetch = win.fetch;
  win.fetch = (url, options = {}) => {
    if (url === '/api/agent/runs' && options.method === 'POST') started++;
    return originalFetch(url, options);
  };
  doc.getElementById('agentOpen').click();
  await until(() => doc.getElementById('agentState').textContent.startsWith('Restored'));
  assert.equal(doc.getElementById('agentQuestion').value, query);
  assert.equal(doc.querySelector('#agentAnswer strong').textContent, 'Restored response');
  assert.equal(doc.getElementById('agentQuestion').disabled, false);
  await wait(1100);
  assert.equal(started, 0, 'Restoring history must not execute the prompt again');
  const card = doc.querySelector(`[data-selection-id="${proposal.selection_id}"]`);
  assert.match(card.textContent, /Request: Select the meeting screenshot/);
  [...card.querySelectorAll('button')].find(button => button.textContent === 'Discard proposal').click();
  await until(() => !doc.querySelector(`[data-selection-id="${proposal.selection_id}"]`));
  assert.deepEqual(fs.readFileSync(imagePath), before);
  const connection = JSON.parse(fs.readFileSync(path.join(root, 'archive/agent-connection.json')));
  const frames = await api('/api/agent/tools', { tool: 'find_frames', arguments: { frame_ids: [manifest.identifiers.meeting_first] } }, { Authorization: 'Bearer ' + connection.read_token });
  assert.equal(frames.total, 1);
  dom.window.close(); connections.delete(dom);
});
