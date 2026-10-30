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
  backend = spawn(python, ['-m', 'openrelife.app', '--storage-path', path.join(root, 'archive'), '--no-capture'],
    { cwd: project, env: { ...process.env, OPENRELIFE_CONTROL_FD: '3', PYTHONDONTWRITEBYTECODE: '1' }, stdio: ['ignore', 'pipe', 'pipe', 'pipe'] });
  backend.stdio[3].end(control); backend.stdout.pipe(log, { end: false }); backend.stderr.pipe(log, { end: false });
  await until(async () => {
    if (backend.exitCode !== null) throw new Error('Fixture backend exited; ' + root);
    try { await api('/api/recording-status'); return true; } catch { return false; }
  }, 30000);
});
after(async () => {
  for (const dom of connections) dom.window.close();
  if (backend && backend.exitCode === null) {
    backend.kill('SIGTERM'); await Promise.race([new Promise(resolve => backend.once('exit', resolve)), wait(3000)]);
    if (backend.exitCode === null) backend.kill('SIGKILL');
  }
  if (log) log.end();
  const output = path.join(project, 'logs/agent-dom-test-location.json');
  fs.writeFileSync(output, JSON.stringify({ artifact_directory: root, native_visual_test: false }, null, 2));
});

async function page() {
  const response = await fetch(base + '/', { headers: { 'X-OpenReLife-Control': control } });
  assert.equal(response.status, 200);
  const html = await response.text();
  const dom = new JSDOM(html, { url: base, runScripts: 'outside-only', pretendToBeVisual: true });
  connections.add(dom);
  const win = dom.window;
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
  return dom;
}

test('Command+Enter and AI button use saved command; Markdown and previews stay safe', async () => {
  const dom = await page(); const win = dom.window; const doc = win.document;
  doc.getElementById('agentOpen').click();
  await until(() => doc.getElementById('agentCommand').value.length > 0);
  const answer = `## Fixture\n\n**Bold** and *italic*, with [source](openrelife://frame/${manifest.identifiers.meeting_first}).\n\n- First\n- Second\n\n| Time | Status |\n| --- | --- |\n| 10:00 | Joined |\n\n\`\`\`html\n<img src=x onerror="window.compromised=true">\n\`\`\`\n\n<img src=x onerror="window.compromised=true"><script>window.compromised=true</script> [unsafe](javascript:alert(1)) [external](https://example.com) ![remote](https://example.com/tracking.gif)`;
  const program = `import json; print(json.dumps({'answer': ${JSON.stringify(answer)}}))`;
  doc.getElementById('agentPreset').value = 'custom';
  doc.getElementById('agentPreset').dispatchEvent(new win.Event('change', { bubbles: true }));
  doc.getElementById('agentCommand').value = `${JSON.stringify(python)} -c ${JSON.stringify(program)}`;
  doc.getElementById('agentSave').click();
  await until(() => doc.getElementById('agentState').textContent.includes('saved'));
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
  doc.getElementById('agentSave').click();
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
    doc.getElementById('agentSave').click();
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

test('Deletion preview is a filtered timeline; adjustment and cancellation do not delete', async () => {
  const connection = JSON.parse(fs.readFileSync(path.join(root, 'archive/agent-connection.json')));
  const selected = [manifest.identifiers.telegram_native, manifest.identifiers.telegram_web];
  const proposal = await api('/api/agent/tools', { tool: 'propose_deletion', arguments: { frame_ids: selected, reason: 'DOM selection fixture' } },
    { Authorization: 'Bearer ' + connection.read_token, 'X-OpenReLife-Client': 'dom-fixture' });
  const before = await api('/api/agent/tools', { tool: 'archive_status', arguments: {} }, { Authorization: 'Bearer ' + connection.read_token });
  const dom = await page(); const win = dom.window; const doc = win.document;
  doc.getElementById('agentOpen').click();
  await until(() => doc.querySelector('#agentSelections button'));
  doc.querySelector('#agentSelections button').click();
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
  await until(() => doc.querySelector('#agentSelections button'));
  doc.querySelector('#agentSelections button').click();
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
