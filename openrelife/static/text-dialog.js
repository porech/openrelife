/* Read-only OCR document. Model and window text are always rendered as text. */
(() => {
  const el = id => document.getElementById(id);
  const dialog = el('textDialog'), body = el('extractedText'), image = el('textDialogImage');
  let previousFocus, previewTimestamp, findCount = 0, findCurrent = 0, copyTimer;
  const isOpen = () => dialog.classList.contains('show');
  const shownText = () => !currentEntry ? '' : currentOCRMode === 'ai' ? currentEntry.ai_text || null : currentEntry.text || '';
  const query = () => el('dialogFindInput').value.trim().toLowerCase();
  const escape = text => text.replace(/[&<>]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[character]));
  function open(aiMode) {
    if (!isOpen()) previousFocus = document.activeElement;
    hidePaletteActions(); closeFind(false);
    dialog.classList.add('show'); dialog.setAttribute('aria-hidden','false');
    el('textDialogScrim').classList.add('show');
    if (aiMode) currentOCRMode = 'ai';
    refresh(); el('textDialogClose').focus({preventScroll:true});
    if (aiMode && !(currentEntry && currentEntry.ai_text)) runAIOCR();
  }
  function close() {
    if (!isOpen()) return;
    dialog.classList.remove('show'); dialog.setAttribute('aria-hidden','true');
    el('textDialogScrim').classList.remove('show'); closeFind(false);
    image.removeAttribute('src'); image.hidden = true; previewTimestamp = null;
    body.replaceChildren();
    if (previousFocus?.isConnected && !previousFocus.closest('[hidden]') && !el('settingsModalOverlay').classList.contains('show')) previousFocus.focus({preventScroll:true});
  }
  function state(title,hint,icon='bi-body-text') {
    const content = document.createElement('div'); content.className = 'td-state';
    const symbol = document.createElement('i'); symbol.className = `bi ${icon}`; symbol.setAttribute('aria-hidden','true');
    const heading = document.createElement('span'); heading.textContent = title; content.append(symbol,heading);
    if (hint) { const detail = document.createElement('span'); detail.className = 'td-state-hint'; detail.textContent = hint; content.append(detail); }
    body.replaceChildren(content);
  }
  function refresh() {
    if (!isOpen()) return;
    const entry = currentEntry, ai = currentOCRMode === 'ai', complete = !!entry?.ai_text;
    const stamp = entry?.timestamp;
    const date = stamp ? new Date(Number(stamp)/1000).toLocaleString() : '';
    el('textDialogApp').textContent = entry?.app || 'Screenshot';
    el('textDialogTs').textContent = date;
    if (stamp) el('textDialogTs').dateTime = new Date(Number(stamp)/1000).toISOString(); else el('textDialogTs').removeAttribute('datetime');
    el('textDialogSourceApp').textContent = entry?.app || 'Unavailable';
    el('textDialogSourceTitle').textContent = entry?.title || 'Unavailable';
    el('textDialogSourceTime').textContent = date || '—';
    if (stamp !== previewTimestamp) {
      previewTimestamp = stamp; image.hidden = true; image.removeAttribute('src');
      el('textDialogImageState').hidden = false; el('textDialogImageState').textContent = stamp ? 'Loading preview…' : 'No screenshot selected';
      if (stamp) { image.alt = `Screenshot from ${date}`; image.src = `/api/frames/${stamp}/thumbnail`; }
    }
    el('textDialogPreview').disabled = !stamp;
    el('modeLabelBase').setAttribute('aria-pressed',String(!ai));
    el('modeLabelAi').setAttribute('aria-pressed',String(ai));
    const run = el('btnRunAI'); run.disabled = _aiRunning || !entry; run.classList.toggle('running',_aiRunning);
    run.querySelector('.ai-spinner').style.display = _aiRunning ? 'inline-block' : 'none';
    run.querySelector('.ai-star').style.display = _aiRunning ? 'none' : 'inline';
    run.querySelector('.run-label').textContent = _aiRunning ? 'Transcribing…' : complete ? 'Re-run AI' : 'Extract with AI';
    el('textDialogBanner').classList.toggle('show',ai && complete);
    el('textDialogOrigin').textContent = ai ? 'AI OCR' : 'Local OCR';
    const text = shownText(); el('btnCopyText').disabled = !text;
    el('textDialogStats').textContent = text ? `${text.split(/\r\n|\r|\n/).length.toLocaleString()} lines · ${text.trim().split(/\s+/).filter(Boolean).length.toLocaleString()} words` : '—';
    render();
  }
  image.onload = () => {
    if (!isOpen() || !image.getAttribute('src') || !image.complete || !image.naturalWidth) return;
    image.hidden = false; el('textDialogImageState').hidden = true;
  };
  image.onerror = () => {
    if (!isOpen()) return;
    image.hidden = true; el('textDialogImageState').hidden = false; el('textDialogImageState').textContent = 'Screenshot unavailable';
  };
  function render(reset = false) {
    if (!isOpen()) return;
    const text = shownText(), needle = query();
    findCount = 0; if (reset) findCurrent = 0;
    if (needle && text) {
      const normalized = text.toLowerCase(); let offset = 0;
      while ((offset = normalized.indexOf(needle,offset)) !== -1) { findCount++; offset += needle.length; }
    }
    findCurrent = Math.max(0,Math.min(findCurrent,findCount-1));
    el('dialogFindCount').textContent = needle ? findCount ? `${findCurrent+1}/${findCount}` : '0/0' : '';
    el('dialogFindPrevious').disabled = el('dialogFindNext').disabled = !findCount;
    if (!currentEntry) { state('Loading screenshot text…',''); return; }
    if (text === null) { state('No AI transcription for this screenshot.','Use “Extract with AI” to create one.','bi-stars'); return; }
    if (!text) { state('No text available.',currentEntry.text == null ? 'Local text recognition may be pending or paused.' : 'No text was found in this screenshot.'); return; }
    if (!needle || !findCount) { body.textContent = text; return; }
    const normalized = text.toLowerCase(); let html = '', offset = 0, index = 0, match = normalized.indexOf(needle);
    while (match !== -1) {
      html += escape(text.slice(offset,match)) + `<mark class="find-hl${index === findCurrent ? ' current' : ''}">${escape(text.slice(match,match+needle.length))}</mark>`;
      offset = match+needle.length; index++; match = normalized.indexOf(needle,offset);
    }
    body.innerHTML = html + escape(text.slice(offset));
    const current = body.querySelector('mark.current');
    if (current && (current.offsetTop < body.scrollTop + 16 || current.offsetTop > body.scrollTop + body.clientHeight - 48)) body.scrollTop = Math.max(0,current.offsetTop-body.clientHeight/2);
  }
  function openFind() { if (!isOpen()) return; el('dialogFindInput').focus(); el('dialogFindInput').select(); render(); }
  function closeFind(focus = true) {
    el('dialogFindInput').value = ''; findCount = findCurrent = 0;
    if (isOpen()) { render(); if (focus) body.focus({preventScroll:true}); }
  }
  function find() { render(true); }
  function step(direction) { if (!findCount) return; findCurrent = (findCurrent+direction+findCount)%findCount; render(); }
  function findKey(event) { if (event.key === 'Enter') { event.preventDefault(); step(event.shiftKey ? -1 : 1); } }
  async function copy() {
    const text = shownText(); if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      const label = el('btnCopyText').querySelector('.copy-label'); label.textContent = 'Copied';
      clearTimeout(copyTimer); copyTimer = setTimeout(() => { label.textContent = 'Copy text'; },1400);
    } catch { showToast('Could not copy text. Select the text and copy it manually.','error'); }
  }
  document.addEventListener('keydown',event => {
    if (event.isComposing || !isOpen() || el('settingsModalOverlay').classList.contains('show')) return;
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopImmediatePropagation();
      // The integrated find field shares the dialog's single-step Escape.
      close();
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'f') { event.preventDefault(); event.stopImmediatePropagation(); openFind(); }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'a' && event.target === body) {
      event.preventDefault(); const range = document.createRange(); range.selectNodeContents(body);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    }
    if (event.key === 'Tab') {
      const items = [...dialog.querySelectorAll('button,input,[tabindex="0"]')].filter(node=>!node.disabled && node.getClientRects().length);
      if (items.length && ((event.shiftKey && document.activeElement === items[0]) || (!event.shiftKey && document.activeElement === items.at(-1)))) {
        event.preventDefault(); (event.shiftKey ? items.at(-1) : items[0]).focus();
      }
    }
  },true);
  window.openrelifeTextUI = {open,close,refresh,shownText,openFind,closeFind,find,step,findKey,copy};
})();
