/* Agent commands and app-owned deletion previews. No model output is evaluated. */
(() => {
  const el = id => document.getElementById(id);
  let settings, activeRun, timer, originalTimeline, lastAnswer, lastEvidence;
  let hoverTimer, previewLink;
  const ui = window.openrelifeAgentUI = { preview: null, exitPreview, confirmSelection };
  const framePreview = document.createElement('div');
  framePreview.id = 'agentFramePreview'; framePreview.className = 'agent-frame-preview';
  framePreview.setAttribute('role', 'tooltip'); framePreview.hidden = true;
  document.body.append(framePreview);

  async function api(path, data, method) {
    const response = await fetch(path, { method: method || (data ? 'POST' : 'GET'),
      headers: { 'Content-Type': 'application/json' }, body: data ? JSON.stringify(data) : undefined });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
    return result;
  }
  const size = bytes => `${(bytes / 1e9).toFixed(2)} GB`;
  const date = timestamp => new Date(Number(timestamp) / 1000).toLocaleString();
  function message(error) { el('agentState').textContent = error.message || error; }

  async function openPanel() {
    el('agentPanel').hidden = false;
    if (!settings) {
      settings = await api('/api/agent/settings');
      el('agentPreset').value = settings.preset;
      el('agentCommand').value = settings.command;
      el('agentTimeout').value = settings.timeout_seconds;
      el('agentMcpCommand').textContent = settings.mcp_command.map(argument => JSON.stringify(argument)).join(' ');
    }
    await loadSelections();
    if (!el('agentQuestion').value) el('agentQuestion').value = searchInput.value;
    el('agentQuestion').focus();
  }
  function askFromSearch() {
    el('agentQuestion').value = searchInput.value;
    clearTimeout(searchTimeout); resetSearchState(); hidePaletteActions();
    openPanel().then(() => { if (el('agentQuestion').value.trim()) return start(); }).catch(message);
  }
  el('agentOpen').onclick = askFromSearch;
  el('agentClose').onclick = () => { hideFramePreview(); el('agentPanel').hidden = true; };
  searchInput.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault(); event.stopImmediatePropagation();
      askFromSearch();
    }
  }, true);
  el('agentPreset').onchange = () => {
    el('agentCommand').value = settings.presets[el('agentPreset').value];
  };
  el('agentSave').onclick = async () => {
    try {
      settings = await api('/api/agent/settings', { preset: el('agentPreset').value,
        command: el('agentCommand').value, timeout_seconds: Number(el('agentTimeout').value) });
      message('Command saved');
    } catch (error) { message(error); }
  };
  el('agentStart').onclick = () => start().catch(message);
  el('agentCancel').onclick = () => {
    if (activeRun) api(`/api/agent/runs/${activeRun}`, null, 'DELETE').then(renderRun).catch(message);
  };
  async function start() {
    if (!el('agentQuestion').value.trim()) { message('Enter a question'); return; }
    const run = await api('/api/agent/runs', { query: el('agentQuestion').value });
    activeRun = run.run_id;
    if (timer) clearInterval(timer);
    renderRun(run);
    timer = setInterval(async () => {
      try { renderRun(await api(`/api/agent/runs/${activeRun}`)); }
      catch (error) { clearInterval(timer); timer = null; el('agentStart').disabled = false; el('agentCancel').hidden = true; message(error); }
    }, 1000);
  }
  function hideFramePreview() {
    clearTimeout(hoverTimer);
    if (previewLink) previewLink.removeAttribute('aria-describedby');
    previewLink = null; framePreview.hidden = true; framePreview.replaceChildren();
  }
  function showFramePreview(link, identifier) {
    if (!link.isConnected || el('agentPanel').hidden) return;
    previewLink = link; link.setAttribute('aria-describedby', framePreview.id);
    const image = document.createElement('img'), caption = document.createElement('div');
    image.alt = `Screenshot from ${date(identifier)}`; image.hidden = true;
    caption.textContent = 'Loading preview…';
    framePreview.replaceChildren(image, caption); framePreview.hidden = false;
    // Keep the preview within the viewport, beside the panel when space permits.
    const rect = link.getBoundingClientRect();
    const width = Math.min(340, window.innerWidth - 16), height = 260;
    framePreview.style.width = `${width}px`;
    const left = rect.left >= width + 20 ? rect.left - width - 12 : rect.left;
    framePreview.style.left = `${Math.max(8, Math.min(left, window.innerWidth - width - 8))}px`;
    framePreview.style.top = `${Math.max(8, Math.min(rect.bottom + 8, window.innerHeight - height - 8))}px`;
    image.onload = () => { if (previewLink === link && image.isConnected) { image.hidden = false; caption.textContent = date(identifier); } };
    image.onerror = () => { if (previewLink === link && image.isConnected) caption.textContent = 'Screenshot unavailable'; };
    image.src = `/api/frames/${identifier}/thumbnail`;
  }
  function frameLink(link, identifier) {
    link.href = '#'; link.dataset.frameId = identifier;
    link.onclick = event => { event.preventDefault(); hideFramePreview(); el('agentPanel').hidden = true; goToTimestamp(Number(identifier)); };
    link.onmouseenter = () => { hideFramePreview(); hoverTimer = setTimeout(() => showFramePreview(link, identifier), 220); };
    link.onmouseleave = hideFramePreview;
    link.onfocus = () => { hideFramePreview(); showFramePreview(link, identifier); };
    link.onblur = hideFramePreview;
  }
  window.addEventListener('resize', hideFramePreview);
  document.addEventListener('scroll', hideFramePreview, true);
  function renderAnswer(text) {
    if (text === lastAnswer) return;
    lastAnswer = text; hideFramePreview();
    const target = el('agentAnswer');
    if (!window.marked || !window.DOMPurify) { target.textContent = text; return; }
    // Model output is untrusted. Allow formatting, never images, scripts or arbitrary URLs.
    const fragment = DOMPurify.sanitize(marked.parse(text, { gfm: true, breaks: true }), {
      ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'del', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'ul', 'ol', 'li', 'blockquote', 'code', 'pre', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'a'],
      ALLOWED_ATTR: ['href', 'title', 'start', 'align'], ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false,
      ALLOWED_URI_REGEXP: /^openrelife:\/\/frame\/\d{10,17}$/, RETURN_DOM_FRAGMENT: true,
    });
    for (const link of fragment.querySelectorAll('a')) {
      const match = /^openrelife:\/\/frame\/(\d{10,17})$/.exec(link.getAttribute('href') || '');
      if (match && Number.isSafeInteger(Number(match[1]))) frameLink(link, match[1]);
      else link.replaceWith(...link.childNodes);
    }
    target.replaceChildren(fragment);
  }
  function renderRun(run) {
    if (activeRun && run.run_id !== activeRun) return;
    const running = ['starting', 'running'].includes(run.state);
    el('agentStart').disabled = running; el('agentCancel').hidden = !running;
    message(`${run.state} · ${run.tool_calls} tool calls · ${(run.returned_bytes / 1024).toFixed(0)} KB read`);
    const ungrounded = run.state === 'completed' && !run.tool_calls ? 'No archive tools were used. Check the command’s MCP connection before relying on this answer.\n\n' : '';
    renderAnswer(ungrounded + (run.error ? `${run.answer || ''}\n${run.error}` : run.answer || (running ? 'Looking for evidence…' : '')));
    el('agentTrace').textContent = (run.events || []).map(event => {
      if (event.type === 'tool_call') return `${event.tool} ${JSON.stringify(event.arguments)}`;
      if (event.type === 'tool_result') return `→ ${event.tool}: ${event.frame_ids.length} frames, ${event.bytes} bytes, ${event.elapsed_ms} ms`;
      if (event.type === 'tool_error' || event.type === 'run_error') return `ERROR: ${event.error}`;
      if (event.type === 'stderr') return event.text;
      return event.type;
    }).join('\n');
    el('agentTraceDownload').hidden = false; el('agentTraceDownload').href = `/api/agent/runs/${run.run_id}/trace`;
    const identifiers = (run.evidence_frame_ids || []).slice(0, 30).filter(value => /^\d{10,17}$/.test(value) && Number.isSafeInteger(Number(value)));
    const evidenceKey = identifiers.join(',');
    if (lastEvidence !== evidenceKey) {
      lastEvidence = evidenceKey;
      const evidence = el('agentEvidence'); evidence.replaceChildren();
      for (const identifier of identifiers) {
        const link = document.createElement('a'); link.className = 'agent-evidence-link'; link.textContent = date(identifier);
        frameLink(link, identifier); evidence.append(link);
      }
    }
    if (!running) { if (timer) clearInterval(timer); timer = null; loadSelections().catch(message); }
  }
  async function loadSelections() {
    const data = await api('/api/agent/selections');
    const target = el('agentSelections'); target.replaceChildren();
    for (const selection of data.selections.filter(item => item.requires_app_confirmation && item.count)) {
      const card = document.createElement('div'); card.className = 'agent-selection-card';
      const description = document.createElement('p'); description.textContent = `${selection.count} frames · ${size(selection.bytes)}\n${selection.reason}`;
      const button = document.createElement('button'); button.textContent = 'Review deletion timeline';
      button.onclick = () => preview(selection.selection_id).catch(message);
      card.append(description, button); target.append(card);
    }
  }
  async function preview(identifier) {
    hideFramePreview();
    if (ui.preview) exitPreview();
    const selection = await api(`/api/agent/selections/${identifier}`);
    if (!selection.count) throw new Error('This selection is empty');
    originalTimeline = { timestamps: timestamps.slice(), slider: slider.value };
    ui.preview = selection; timestamps = selection.frame_ids.map(Number);
    slider.max = timestamps.length - 1; slider.value = slider.max;
    el('timelinePill').classList.add('delete-mode'); el('deleteControls').style.display = 'flex';
    el('agentPanel').hidden = true; el('agentDeletePreview').hidden = false;
    await updateDisplay(timestamps[0]); renderPreview();
  }
  function renderPreview() {
    if (!ui.preview) return;
    const selectedBytes = timestamps.reduce((sum, stamp) => sum + (ui.preview.frame_bytes[String(stamp)] || 0), 0);
    el('agentDeleteSummary').textContent = `${timestamps.length} selected frames · ${size(selectedBytes)} · ${ui.preview.reason}`;
    el('deleteInfo').textContent = `${timestamps.length} selected screenshots to delete`;
    const current = timestamps.length - 1 - Number(slider.value);
    const target = el('agentDeleteThumbs'); target.replaceChildren();
    for (let index = Math.max(0, current - 4); index < Math.min(timestamps.length, current + 5); index++) {
      const timestamp = timestamps[index], button = document.createElement('button');
      if (index === current) button.className = 'current';
      const image = document.createElement('img'); image.src = `/static/${timestamp}.webp`; image.alt = date(timestamp);
      const label = document.createElement('span'); label.textContent = new Date(timestamp / 1000).toLocaleTimeString();
      button.append(image, label); button.onclick = () => {
        slider.value = timestamps.length - 1 - index; updateDisplay(timestamp).then(renderPreview);
      }; target.append(button);
    }
  }
  slider.addEventListener('input', renderPreview);
  el('agentExclude').onclick = () => {
    const index = timestamps.length - 1 - Number(slider.value);
    timestamps.splice(index, 1);
    if (!timestamps.length) { exitPreview(); return; }
    slider.max = timestamps.length - 1; slider.value = Math.max(0, timestamps.length - 1 - Math.min(index, timestamps.length - 1));
    updateDisplay(timestamps[timestamps.length - 1 - Number(slider.value)]).then(renderPreview);
  };
  el('agentResetSelection').onclick = () => {
    timestamps = ui.preview.frame_ids.map(Number); slider.max = timestamps.length - 1; slider.value = slider.max;
    updateDisplay(timestamps[0]).then(renderPreview);
  };
  el('agentExitPreview').onclick = exitPreview;
  function exitPreview() {
    if (!ui.preview) return;
    timestamps = originalTimeline.timestamps; slider.max = timestamps.length - 1; slider.value = originalTimeline.slider;
    ui.preview = null; originalTimeline = null;
    el('timelinePill').classList.remove('delete-mode'); el('deleteControls').style.display = 'none'; el('agentDeletePreview').hidden = true;
    if (timestamps.length) updateDisplay(timestamps[timestamps.length - 1 - Number(slider.value)]);
    syncData();
  }
  async function confirmSelection() {
    if (!confirm(`Permanently delete exactly these ${timestamps.length} selected screenshots and their search data?`)) return;
    const button = el('btnConfirmDelete'); button.disabled = true;
    try {
      const result = await api(`/api/agent/selections/${ui.preview.selection_id}/delete`, {
        digest: ui.preview.digest, frame_ids: timestamps.map(String) });
      if (!result.complete) {
        alert(`Deleted ${result.deleted.length}. ${result.errors.length} errors; ${result.changed.length} changed files were preserved. Reopen the preview to review remaining items.`);
      }
      window.location.reload();
    } catch (error) { alert(error.message); button.disabled = false; }
  }
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') hideFramePreview();
    if (event.key === 'Escape' && ui.preview) {
      event.preventDefault(); event.stopImmediatePropagation(); exitPreview();
    }
  }, true);
})();
