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
  const agent = await fetch(base + '/assets/agents.js', { headers: { 'X-OpenReLife-Control': control } }).then(result => result.text());
  win.eval(inline + '\n' + agent);
  // outside-only intentionally disables inline attributes in jsdom. Bind the
  // product's actual handlers as a browser would, rather than replacing them.
  for (const element of win.document.querySelectorAll('[onclick]')) {
    element.onclick = new win.Function('event', element.getAttribute('onclick'));
  }
  return dom;
}

test('Command+Enter uses saved command; answers are text with safe frame links', async () => {
  const dom = await page(); const win = dom.window; const doc = win.document;
  doc.getElementById('agentOpen').click();
  await until(() => doc.getElementById('agentCommand').value.length > 0);
  const answer = `Fixture [source](openrelife://frame/${manifest.identifiers.meeting_first}) <img src=x onerror="window.compromised=true">`;
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
  assert.match(doc.getElementById('agentAnswer').textContent, /Fixture source/);
  assert.match(doc.getElementById('agentAnswer').textContent, /No archive tools were used/);
  assert.equal(doc.querySelector('#agentAnswer img'), null);
  assert.equal(win.compromised, undefined);
  assert.equal(doc.querySelectorAll('#agentAnswer a').length, 1);
  assert.equal(doc.getElementById('agentTraceDownload').hidden, false);
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
  const purged = await api('/api/agent/runs/' + proposed.run_id);
  assert.equal(purged.state, 'purged');
  assert.equal(purged.answer, '');
  const remaining = await api('/api/agent/tools', { tool: 'read_frames', arguments: { frame_ids: [manifest.identifiers.telegram_docs, manifest.identifiers.telegram_injection] } },
    { Authorization: 'Bearer ' + connection.read_token, 'X-OpenReLife-Client': 'post-confirmation' });
  assert.deepEqual(remaining.missing, [manifest.identifiers.telegram_injection]);
  assert.equal(remaining.frames[0].frame_id, manifest.identifiers.telegram_docs);
});
