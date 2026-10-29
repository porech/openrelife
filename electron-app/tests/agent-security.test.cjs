/* Electron-boundary unit tests: no native UI automation or real archive access. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');

function launcher() {
  const hooks = {}, requests = [], sent = {};
  const switches = { 'storage-path': '/fixture-archive', 'backend-python': '/fixture-python/bin/python', 'no-capture': true };
  const app = { isPackaged: true, commandLine: { getSwitchValue: name => switches[name] || '', hasSwitch: name => !!switches[name] },
    isReady: () => true,
    setName() {}, setPath() {}, getPath: name => name === 'home' ? '/fixture-home' : '/fixture-archive',
    whenReady: () => ({ then() {} }), on: (name, callback) => { hooks[name] = callback; },
    dock: { isVisible: () => true }, focus() {} };
  const child = new EventEmitter(); child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
  child.stdio = [null, child.stdout, child.stderr, { end: token => { sent.control = token; } }];
  class Window {
    constructor() {
      this.handlers = {};
      this.webContents = { id: 17, session: { webRequest: { onBeforeSendHeaders: (_, callback) => { sent.interceptor = callback; } } },
        getURL: () => this.url || 'file://loading.html',
        on: (name, callback) => { this.handlers[name] = callback; }, setWindowOpenHandler: callback => { sent.openHandler = callback; },
        send: (channel, value) => { (sent.ipc ||= []).push({ channel, value }); } };
      sent.window = this;
    }
    on() {} once() {} loadFile() {} loadURL(url) { this.url = url; } show() {} focus() {} setSimpleFullScreen() {}
  }
  const fakeFs = { mkdirSync() {}, existsSync: () => true,
    readFileSync: () => JSON.stringify({ server_port: 48123 }), createWriteStream: () => ({ write() {} }) };
  const electron = { app, BrowserWindow: Window, screen: { getPrimaryDisplay: () => ({ workAreaSize: { width: 1200, height: 900 } }) },
    systemPreferences: { getMediaAccessStatus: () => 'granted' }, desktopCapturer: { getSources: async () => [] },
    dialog: { showMessageBox: () => { sent.dialog = true; return Promise.resolve({ response: 0 }); } } };
  const context = vm.createContext({ require: name => {
    if (name === 'electron') return electron;
    if (name === 'fs') return fakeFs;
    if (name === 'child_process') return { exec: (_, callback) => callback(null, ''),
      spawn: (command, args, options) => { sent.spawn = { command, args, options }; return child; } };
    return require(name);
  }, console: { log() {}, error() {} }, process: { platform: 'darwin', arch: 'arm64', env: { PATH: '/usr/bin' }, resourcesPath: '/bundle' },
    __dirname: path.resolve(__dirname, '..'), Headers, URL, setTimeout, clearTimeout,
    fetch: async (url, options) => { requests.push({ url, options }); return { ok: true }; } });
  vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../main.js'), 'utf8'), context);
  return { context, hooks, requests, sent, child, app };
}

test('Private control capability travels on a pipe and stays on the owned WebView', async () => {
  const { context, sent, requests } = launcher();
  vm.runInContext('updateServerConfig();createWindow()', context);
  await vm.runInContext('startBackend()', context);
  assert.ok(sent.control.length >= 32);
  assert.equal(sent.spawn.options.env.OPENRELIFE_CONTROL_FD, '3');
  assert.equal(sent.spawn.options.env.OPENRELIFE_CONTROL_TOKEN, undefined);
  assert.ok(!sent.spawn.args.includes(sent.control));
  assert.ok(sent.spawn.args.includes('--no-capture'));
  assert.ok(sent.spawn.args.includes('/fixture-archive'));
  let owned;
  sent.interceptor({ url: 'http://127.0.0.1:48123/api/delete', webContentsId: 17, requestHeaders: {} }, value => { owned = value.requestHeaders; });
  assert.equal(owned['X-OpenReLife-Control'], sent.control);
  let foreign;
  sent.interceptor({ url: 'http://127.0.0.1:48123/api/delete', webContentsId: 99, requestHeaders: {} }, value => { foreign = value.requestHeaders; });
  assert.equal(foreign['X-OpenReLife-Control'], undefined);
  await vm.runInContext("backendFetch('https://example.test/resource')", context);
  assert.equal(requests.at(-1).options.headers.get('X-OpenReLife-Control'), null);
  assert.equal(sent.openHandler().action, 'deny');
  let prevented = false;
  sent.window.handlers['will-navigate']({ preventDefault() { prevented = true; } }, 'https://example.test/');
  assert.equal(prevented, true);
});

test('Native quit marks intentional shutdown before backend exits', async () => {
  const { context, hooks, app, child, sent } = launcher();
  await vm.runInContext('startBackend()', context);
  hooks['before-quit']({});
  assert.equal(app.isQuitting, true);
  child.emit('close', 1);
  assert.equal(sent.dialog, undefined);
});

test('Screenshot links accept only frame instants and survive startup', async () => {
  const { context, hooks, app, sent } = launcher();
  app.isReady = () => false;
  hooks['open-url']({ preventDefault() {} }, 'openrelife://frame/1793005205000000');
  assert.equal(sent.window, undefined);
  app.isReady = () => true;
  vm.runInContext('updateServerConfig();createWindow()', context);
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(sent.ipc.some(item => item.channel === 'open-frame' && item.value === '1793005205000000'));
  const before = sent.ipc.length;
  hooks['open-url']({ preventDefault() {} }, 'openrelife://frame/../../etc/passwd');
  hooks['open-url']({ preventDefault() {} }, 'javascript:alert(1)');
  hooks['open-url']({ preventDefault() {} }, 'openrelife://frame/99999999999999999');
  assert.equal(sent.ipc.length, before);
});
