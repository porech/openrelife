/* Native acceptance through the normal Electron main/preload and isolated CLI store. */
process.on('uncaughtException', error => { console.error(error); process.exit(1); });
const { app, BrowserWindow, globalShortcut } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const project = path.resolve(__dirname, '../..');
const archive = app.commandLine.getSwitchValue('storage-path');
const phase = app.commandLine.getSwitchValue('acceptance-phase');
if (!archive || !['first', 'restart'].includes(phase) || !app.commandLine.hasSwitch('no-capture')) {
  throw new Error('Requires an isolated --storage-path, --no-capture and --acceptance-phase=first|restart');
}
const report = { phase, personal_capture_paused: false, entry_point: 'Electron main with --show --storage-path --no-capture', focus_transport: 'Chromium focus emulation; transparent, noninteractive native window', fullscreen_transport: 'disabled in acceptance to preserve desktop Dock visibility', checks: [] };
const marker = path.join(archive, 'search-introduction-seen');
if (fs.existsSync(marker) !== (phase === 'restart')) throw new Error('Unexpected fixture introduction state');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check) {
  const end = Date.now() + 30000;
  while (Date.now() < end) { if (await check()) return; await pause(100); }
  throw new Error('Native acceptance condition timed out');
}
let finished = false;
function finish(error) {
  if (finished) return;
  finished = true;
  report.state = error ? 'failed' : 'passed';
  if (error) report.error = error.stack;
  fs.writeFileSync(path.join(archive, `introduction-${phase}.json`), JSON.stringify(report, null, 2));
  if (error) console.error(error);
  app.quit();
}
const watchdog = setTimeout(() => finish(new Error('Native acceptance exceeded 60 seconds')), 60000);
// Do not take the installed app's system shortcut during acceptance.
globalShortcut.register = () => true;
app.focus = () => {};
// Exercise real show/hide lifecycle without taking over the user's display.
app.on('browser-window-created', (_event, window) => {
  window.setOpacity(0);
  window.setIgnoreMouseEvents(true);
  window.setFocusable(false);
  window.setSimpleFullScreen = () => {};
  window.setFullScreen = () => {};
  window.webContents.on('console-message', (_event, level, message) => {
    if (level >= 3) (report.renderer_errors ||= []).push(message);
  });
  window.webContents.debugger.attach('1.3');
  window.webContents.debugger.sendCommand('Emulation.setFocusEmulationEnabled', { enabled: true });
});
require(path.join(project, 'electron-app/main.js'));
app.whenReady().then(async () => {
  try {
    let win;
    await until(async () => {
      win = BrowserWindow.getAllWindows()[0];
      return win && win.isVisible() && !win.webContents.isLoadingMainFrame() &&
        win.webContents.getURL().startsWith('http://127.0.0.1:') &&
        await win.webContents.executeJavaScript('!!window.openrelifeSettings');
    });
    const evaluate = code => win.webContents.executeJavaScript(code);
    const captureState = () => evaluate("fetch('/api/recording-status').then(response => response.json())");
    if (!(await captureState()).viewer_open) throw new Error('Visible viewer lacks acknowledged capture protection');
    report.checks.push({ startup_capture_protected: true });
    await until(() => evaluate(`document.getElementById('paletteActions').classList.contains('show') === ${phase === 'first'}`));
    await pause(300);
    const state = await evaluate(`({palette:document.getElementById('paletteActions').classList.contains('show'),active:document.activeElement.id||document.activeElement.tagName})`);
    if (state.palette !== (phase === 'first') || state.active !== (phase === 'first' ? 'searchInput' : 'BODY')) throw new Error('Incorrect startup state: ' + JSON.stringify(state));
    if (!fs.existsSync(marker)) throw new Error('Missing persistent introduction marker');
    report.checks.push({ startup: state, marker_persisted: true });
    await win.webContents.debugger.sendCommand('Emulation.setFocusEmulationEnabled', { enabled: true });
    win.webContents.focus();
    async function key(key, code, virtual, modifiers = 0) {
      for (const type of ['rawKeyDown', 'keyUp']) await win.webContents.debugger.sendCommand('Input.dispatchKeyEvent', { type, key, code, windowsVirtualKeyCode: virtual, modifiers });
      await pause(100);
    }
    await evaluate('window.electronAPI.hideWindow()');
    await until(() => !win.isVisible());
    await pause(350);
    await until(async () => !(await captureState()).viewer_open);
    // Exercise the app's supported frame-link presentation path after hiding.
    const id = await evaluate('String(timestamps[0])');
    app.emit('open-url', { preventDefault() {} }, 'openrelife://frame/' + id);
    await until(() => win.isVisible());
    await pause(300);
    const reopened = await evaluate(`({palette:document.getElementById('paletteActions').classList.contains('show'),active:document.activeElement.tagName})`);
    if (reopened.palette || reopened.active !== 'BODY') throw new Error('Reopening did not leave the timeline free: ' + JSON.stringify(reopened));
    if (!(await captureState()).viewer_open) throw new Error('Reopened viewer lacks capture protection');
    report.checks.push({ reopened, frame_link_transport: 'native open-url event' });
    await key('Tab', 'Tab', 9);
    report.checks.push({ after_tab: await evaluate(`({active:document.activeElement.id||document.activeElement.tagName,palette:document.getElementById('paletteActions').classList.contains('show')})`) });
    if (await evaluate('document.activeElement.id') !== 'searchInput') throw new Error('First Tab did not focus search');
    await key('k', 'KeyK', 75, 4);
    report.checks.push({ after_first_command_k: await evaluate(`({active:document.activeElement.id||document.activeElement.tagName,palette:document.getElementById('paletteActions').classList.contains('show')})`) });
    await key('k', 'KeyK', 75, 4);
    if (!await evaluate('document.getElementById("paletteActions").classList.contains("show")')) throw new Error('Command+K did not open actions');
    report.checks.push({ manual_command_k_opens: true, first_tab_search: true });
    if (!(await captureState()).viewer_open) throw new Error('Open search actions lost capture protection');
    // Reopen during the delayed hide, as with a repeated global shortcut.
    await evaluate('window.electronAPI.hideWindow()');
    await pause(25);
    app.emit('open-url', { preventDefault() {} }, 'openrelife://frame/' + id);
    await pause(350);
    if (!win.isVisible() || !(await captureState()).viewer_open) throw new Error('Rapid hide/show lost capture protection');
    report.checks.push({ actions_capture_protected: true, rapid_hide_show_protected: true });
    clearTimeout(watchdog);
    finish();
  } catch (error) { clearTimeout(watchdog); finish(error); }
});
