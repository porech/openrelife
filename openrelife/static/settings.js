/* One preferences workspace; drafts stay local until an explicit save. */
(() => {
  const el = id => document.getElementById(id);
  const overlay = el('settingsModalOverlay');
  const form = el('settingsForm');
  const fields = [...form.querySelectorAll('[data-app-setting]')];
  const descriptions = {
    general: ['General', 'Make OpenReLife feel at home on your Mac.'],
    capture: ['Capture', 'Choose how your screen history is recorded and processed.'],
    storage: ['Storage', 'Manage your archive and how long screenshots are kept.'],
    ai: ['AI & agents', 'Your agent, AI text recognition and MCP, in one place.'],
    privacy: ['Privacy', 'Control what is captured and what can leave your Mac.'],
    advanced: ['Advanced', 'Local connection settings and troubleshooting.']
  };
  const modeHelp = {
    aggressive: 'Large batches and short cooldowns. Fastest text availability, highest CPU usage.',
    smart: 'Adapts batches to battery, charging and idle state.',
    on_charge_only: 'Processes the backlog on AC power. No background OCR on battery.',
    eco: 'Tiny batches on AC power when idle or fully charged. Minimum power draw.',
    disabled: 'Text recognition is paused. Screenshots are still captured.'
  };
  let baseline, loading, saving = false, previousFocus, activeSection = 'general', activeAI = 'agent';
  let commandDrafts = {}, previousPreset;

  async function api(path, data) {
    const response = await fetch(path, { method: data ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json' }, body: data ? JSON.stringify(data) : undefined });
    const result = await response.json();
    if (!response.ok || result.success === false) throw new Error(result.error || `Request failed (${response.status})`);
    return result;
  }
  function status(text) { el('settingsStatus').textContent = text; }
  function error(text, retry = false) {
    el('settingsError').hidden = !text;
    el('settingsError').querySelector('span').textContent = text;
    el('settingsRetry').hidden = !retry;
  }
  function snapshot() {
    const app = Object.fromEntries(fields.map(input => [input.dataset.appSetting,
      input.type === 'checkbox' ? input.checked : input.value]));
    return { app, agent: { preset: el('agentPreset').value, command: el('agentCommand').value,
      timeout_seconds: Number(el('agentTimeout').value) },
      ai: { provider: el('aiProvider').value, api_key: el('aiApiKey').value.trim() } };
  }
  function changes() {
    if (!baseline) return {};
    const current = snapshot();
    const app = Object.fromEntries(Object.entries(current.app).filter(([key,value]) => value !== baseline.app[key]));
    return { app: Object.keys(app).length ? app : null,
      agent: ['preset','command','timeout_seconds'].some(key => current.agent[key] !== baseline.agent[key]) ? current.agent : null,
      ai: current.ai.provider !== baseline.ai.provider || current.ai.api_key ? current.ai : null };
  }
  function isDirty() { return Object.values(changes()).some(Boolean); }
  function refresh() {
    el('settingsSave').disabled = !baseline || !!loading || saving || !isDirty();
    el('settingsDiscard').disabled = !baseline || !!loading || saving || !isDirty();
    el('intervalWarning').hidden = Number(el('intervalInput').value) >= 3 || !el('intervalInput').value;
    el('settingsComputeHelp').textContent = modeHelp[el('ocrComputeModeSelect').value] || '';
    if (baseline) {
      const preset = el('agentPreset').value;
      el('settingsAgentAvailability').textContent = preset === 'custom'
        ? 'Custom command: executable lookup happens when the request starts.'
        : `${preset === 'codex' ? 'Codex' : 'Claude'} CLI ${baseline.agent.available[preset] ? 'found on this Mac' : 'not found on the app command path'}.`;
      el('settingsKeyStatus').textContent = el('aiProvider').value !== baseline.ai.provider
        ? 'Enter a key for the new provider.'
        : baseline.ai.api_key ? 'A provider key is saved on this Mac.' : 'No provider key configured.';
    }
  }
  function choose(section, ai) {
    if (descriptions[section]) activeSection = section;
    if (['agent','ocr','mcp'].includes(ai)) activeAI = ai;
    const [title,description] = descriptions[activeSection];
    el('settingsTitle').textContent = title; el('settingsDescription').textContent = description;
    overlay.querySelectorAll('[data-settings-pane]').forEach(node => { node.hidden = node.dataset.settingsPane !== activeSection; });
    overlay.querySelectorAll('[data-settings-section]').forEach(button => {
      if (button.dataset.settingsSection === activeSection) button.setAttribute('aria-current','page');
      else button.removeAttribute('aria-current');
    });
    overlay.querySelectorAll('[data-settings-ai-pane]').forEach(node => { node.hidden = node.dataset.settingsAiPane !== activeAI; });
    overlay.querySelectorAll('[data-settings-ai]').forEach(button => button.setAttribute('aria-pressed',String(button.dataset.settingsAi === activeAI)));
    form.scrollTop = 0;
  }
  function populate() {
    fields.forEach(input => {
      if (input.type === 'checkbox') input.checked = baseline.app[input.dataset.appSetting];
      else input.value = baseline.app[input.dataset.appSetting];
    });
    el('agentPreset').value = baseline.agent.preset; el('agentCommand').value = baseline.agent.command;
    el('agentTimeout').value = baseline.agent.timeout_seconds;
    el('aiProvider').value = baseline.ai.provider; el('aiApiKey').value = '';
    el('aiApiKey').placeholder = baseline.ai.api_key ? 'Enter a new key to replace the saved key' : 'Enter a provider key';
    previousPreset = baseline.agent.preset; commandDrafts = { [previousPreset]: baseline.agent.command };
    refresh();
  }
  function safety(data) {
    const guard = data || {}, lowDisk = guard.disk_free_bytes < guard.minimum_free_bytes;
    el('ocrSafetyWarning').hidden = !guard.paused && !lowDisk;
    el('ocrSafetyReason').textContent = lowDisk ? 'Text recognition is paused until at least 10 GiB of disk space is available.' : guard.reason || '';
    el('ocrSafetyResume').hidden = !guard.paused;
    el('ocrSafetyResume').disabled = lowDisk;
  }
  function load() {
    if (loading) return loading;
    el('settingsFields').disabled = true; error(''); status('Loading settings…');
    loading = Promise.all([api('/api/settings'),api('/api/agent/settings'),api('/api/config')]).then(([app,agent,ai]) => {
      baseline = { app: Object.fromEntries(fields.map(input => [input.dataset.appSetting,
        input.type === 'checkbox' ? !!app[input.dataset.appSetting] : String(app[input.dataset.appSetting])])), agent, ai };
      el('ocrEngineSection').hidden = !app.apple_vision_available;
      safety(app.ocr_safety);
      el('settingsStoragePath').textContent = app.storage_path;
      el('agentMcpCommand').textContent = agent.mcp_command.map(argument => JSON.stringify(argument)).join(' ');
      el('settingsVersion').textContent = `OpenReLife · ${app.version}`;
      const cleanup = app.cleanup || {};
      el('settingsCleanupStatus').textContent = cleanup.state === 'running' ? 'Retention cleanup is running.'
        : cleanup.state === 'failed' || cleanup.state === 'completed_with_errors' ? 'The latest cleanup reported errors. Some expired files may remain.' : '';
      populate(); status('All changes saved'); el('settingsFields').disabled = false;
    }).catch(cause => { error(`Could not load settings: ${cause.message}`,true); status('Settings unavailable'); })
      .finally(() => { loading = null; refresh(); });
    refresh(); return loading;
  }
  async function open(section = 'general', ai) {
    const alreadyOpen = !overlay.hidden;
    if (!alreadyOpen) previousFocus = document.activeElement;
    overlay.hidden = false; overlay.classList.add('show'); choose(section,ai);
    overlay.querySelector('[aria-current="page"]').focus({ preventScroll:true });
    if (!baseline || (!alreadyOpen && !isDirty())) await load();
    else refresh();
  }
  function close() {
    if (saving) return;
    overlay.classList.remove('show'); overlay.hidden = true;
    el('aiApiKey').value = ''; refresh();
    if (previousFocus?.isConnected && !previousFocus.closest('[hidden]')) previousFocus.focus({ preventScroll:true });
  }
  function invalid(input, message) {
    const pane = input.closest('[data-settings-pane]'), aiPane = input.closest('[data-settings-ai-pane]');
    choose(pane.dataset.settingsPane,aiPane?.dataset.settingsAiPane);
    input.focus(); throw new Error(message);
  }
  function validate(pending) {
    for (const input of fields) {
      if (input.type === 'number' && !input.checkValidity()) invalid(input,`Enter a valid ${input.closest('label').childNodes[0].textContent.trim().toLowerCase()}.`);
    }
    if (pending.agent) {
      if (!pending.agent.command.trim() || pending.agent.command.length > 10000) invalid(el('agentCommand'),'Enter a command of at most 10,000 characters.');
      if (!el('agentTimeout').checkValidity()) invalid(el('agentTimeout'),'The timeout must be between 10 and 1800 seconds.');
    }
    if (pending.ai && !pending.ai.api_key) invalid(el('aiApiKey'),'Enter a key for the new AI OCR provider.');
  }
  async function save() {
    if (!baseline || saving || loading) return;
    const pending = changes();
    try { validate(pending); } catch (cause) { error(cause.message); return; }
    if (!Object.values(pending).some(Boolean)) return;
    saving = true; error(''); status('Saving changes…'); refresh(); el('settingsFields').disabled = true;
    el('settingsClose').disabled = true;
    let savedAny = false, restart = false;
    try {
      // Save only dirty domains. A later failure keeps the remaining draft and
      // reports partial progress; it never silently retries an uncertain write.
      if (pending.agent) { baseline.agent = await api('/api/agent/settings',pending.agent); savedAny = true; }
      if (pending.ai) {
        await api('/api/config',pending.ai); baseline.ai = { provider:pending.ai.provider,api_key:'***' };
        el('aiApiKey').value = ''; savedAny = true;
      }
      if (pending.app) {
        const result = await api('/api/settings',pending.app); restart = !!result.restart_required;
        Object.assign(baseline.app,pending.app); savedAny = true;
      }
      status(restart ? 'Saved · restart OpenReLife to apply the new port' : 'All changes saved');
    } catch (cause) {
      error(`${savedAny ? 'Some changes were saved. The remaining edits are still here. ' : ''}${cause.message}`);
      status('Review the remaining changes');
    } finally { saving = false; el('settingsClose').disabled = false; el('settingsFields').disabled = false; refresh(); }
  }
  el('settingsClose').onclick = close; el('settingsSave').onclick = save;
  el('settingsRetry').onclick = load;
  el('settingsDiscard').onclick = () => { populate(); error(''); status('Changes discarded'); };
  overlay.addEventListener('click',event => {
    if (event.target === overlay) close();
    const button = event.target.closest('button'); if (!button) return;
    if (button.dataset.settingsSection) choose(button.dataset.settingsSection);
    if (button.dataset.settingsAi) choose('ai',button.dataset.settingsAi);
    if (button.dataset.settingsLink) choose(button.dataset.settingsLink,button.dataset.aiLink);
    if (button.dataset.settingsPermission) {
      api('/api/open-settings',{which:button.dataset.settingsPermission}).then(() => {
        window.electronAPI?.hideWindow();
      }).catch(cause => error(cause.message));
    }
  });
  el('agentPreset').addEventListener('change',() => {
    commandDrafts[previousPreset] = el('agentCommand').value;
    previousPreset = el('agentPreset').value;
    el('agentCommand').value = commandDrafts[previousPreset] ?? baseline.agent.presets[previousPreset];
  });
  form.addEventListener('input',() => { refresh(); status(isDirty() ? 'Unsaved changes' : 'All changes saved'); });
  form.addEventListener('change',() => { refresh(); status(isDirty() ? 'Unsaved changes' : 'All changes saved'); });
  form.addEventListener('submit',event => { event.preventDefault(); save(); });
  el('settingsCopyMcp').onclick = () => navigator.clipboard.writeText(el('agentMcpCommand').textContent)
    .then(() => status('MCP command copied')).catch(() => error('Could not copy the command. Select and copy the command above.'));
  el('ocrSafetyResume').onclick = async () => {
    if (isDirty()) { error('Save or discard your changes before resuming text recognition.'); return; }
    if (el('ocrComputeModeSelect').value === 'disabled') { error('Choose and save an enabled processing mode before resuming.'); return; }
    try {
      await api('/api/settings/ocr-compute-mode',{ocr_compute_mode:el('ocrComputeModeSelect').value});
      const data = await api('/api/settings/ocr-compute-mode'); safety(data.ocr_safety); status('Text recognition resumed');
    } catch (cause) { error(cause.message); }
  };
  document.addEventListener('keydown',event => {
    if (overlay.hidden || event.isComposing) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); close(); return; }
    if (event.key === 'Tab') {
      const focusable = [...overlay.querySelectorAll('button,input,select,textarea,summary')]
        .filter(node => !node.matches(':disabled') && !node.closest('[hidden]') && node.getClientRects().length);
      const first = focusable[0], last = focusable.at(-1);
      if (first && ((event.shiftKey && document.activeElement === first) || (!event.shiftKey && document.activeElement === last))) {
        event.preventDefault(); (event.shiftKey ? last : first).focus();
      }
    }
  },true);
  window.openrelifeSettings = { open,close,save };
  // The classic viewer links here rather than retaining another AI settings UI.
  const target = new URLSearchParams(location.search).get('settings');
  if (target === 'ai-ocr') open('ai','ocr');
})();
