/* Standalone editor. Formatting is stored inline; shell CSS is never copied. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const article = $('article-content');
  const title = $('article-title');
  const status = $('status');
  const metadata = JSON.parse($('document-data').textContent);
  const HIGHLIGHT = 'color: #0550ae; font-weight: 600; background: linear-gradient(transparent 58%, rgba(9, 105, 218, 0.14) 58%); padding: 0 2px;';
  const ROOT_STYLE = "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; font-size: 16px; line-height: 1.75; color: #1f2328; text-align: left; overflow-wrap: break-word;";
  title.value = metadata.title || '';
  const initial = { html: article.innerHTML, title: title.value };
  let savedRange = null;
  let suppressSelection = false;
  let timer = null;
  let storageAvailable = true;
  let history = [{ ...initial, selection: null }];
  let historyIndex = 0;
  const hash = text => {
    let result = 2166136261;
    for (let index = 0; index < text.length; index++) result = Math.imul(result ^ text.charCodeAt(index), 16777619);
    return (result >>> 0).toString(36);
  };
  const storageKey = 'wechat-oa-v1:' + hash(initial.title + '\n' + initial.html);
  const say = (message, kind = '') => {
    status.textContent = message;
    status.dataset.kind = kind;
  };
  const selectionIsInside = range => article.contains(range.startContainer) && article.contains(range.endContainer);
  const captureSelection = () => {
    if (suppressSelection) return;
    const selection = window.getSelection();
    if (selection && selection.rangeCount) {
      const range = selection.getRangeAt(0);
      if (selectionIsInside(range)) savedRange = range.cloneRange();
    }
  };
  const restoreSelection = () => {
    if (!savedRange || !savedRange.startContainer.isConnected || !selectionIsInside(savedRange)) return null;
    article.focus({ preventScroll: true });
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(savedRange);
    return savedRange.cloneRange();
  };
  const selectionOffsets = () => {
    if (!savedRange || !savedRange.startContainer.isConnected || !selectionIsInside(savedRange)) return null;
    const before = document.createRange();
    before.selectNodeContents(article);
    before.setEnd(savedRange.startContainer, savedRange.startOffset);
    const start = before.toString().length;
    return { start, end: start + savedRange.toString().length };
  };
  const selectOffsets = offsets => {
    if (!offsets) { savedRange = null; return; }
    const walker = document.createTreeWalker(article, NodeFilter.SHOW_TEXT);
    let node, count = 0, startNode = null, endNode = null, startOffset = 0, endOffset = 0;
    while ((node = walker.nextNode())) {
      const limit = count + node.length;
      if (!startNode && offsets.start <= limit) { startNode = node; startOffset = Math.max(0, offsets.start - count); }
      if (!endNode && offsets.end <= limit) { endNode = node; endOffset = Math.max(0, offsets.end - count); break; }
      count = limit;
    }
    if (!startNode || !endNode) { savedRange = null; return; }
    const range = document.createRange();
    range.setStart(startNode, startOffset);
    range.setEnd(endNode, endOffset);
    savedRange = range.cloneRange();
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  };
  const updateUI = () => {
    const modified = article.innerHTML !== initial.html || title.value !== initial.title;
    $('save-state').textContent = modified ? (storageAvailable ? '已修改 · 草稿存于本机' : '已修改 · 请下载保存') : '初始版本';
    $('save-state').dataset.modified = String(modified);
    $('undo').disabled = historyIndex === 0;
    $('redo').disabled = historyIndex === history.length - 1;
    const letters = (article.textContent || '').replace(/\s/g, '').length;
    const highlights = [...article.querySelectorAll('span,strong,mark,b,em')].filter(isHighlight).length;
    $('article-stats').textContent = `${letters} 字符 · ${highlights} 处高亮 · 正文可直接编辑`;
  };
  const saveDraft = () => {
    try {
      const changed = article.innerHTML !== initial.html || title.value !== initial.title;
      if (changed) localStorage.setItem(storageKey, JSON.stringify({ html: article.innerHTML, title: title.value, savedAt: new Date().toISOString() }));
      else localStorage.removeItem(storageKey);
    } catch (_) { storageAvailable = false; }
  };
  const commit = () => {
    clearTimeout(timer);
    timer = null;
    const state = { html: article.innerHTML, title: title.value, selection: selectionOffsets() };
    const previous = history[historyIndex];
    if (state.html !== previous.html || state.title !== previous.title) {
      history = history.slice(0, historyIndex + 1);
      history.push(state);
      if (history.length > 100) history.shift();
      historyIndex = history.length - 1;
      saveDraft();
    }
    updateUI();
  };
  const queueCommit = () => {
    clearTimeout(timer);
    timer = setTimeout(commit, 300);
    $('save-state').textContent = '正在保存本机草稿…';
    $('save-state').dataset.modified = 'true';
  };
  function isHighlight(element) {
    if (!element || element.nodeType !== Node.ELEMENT_NODE || !/^(SPAN|STRONG|MARK|B|EM)$/.test(element.tagName)) return false;
    return element.hasAttribute('data-wg-highlight') || element.classList.contains('wg-highlight') || /gradient\(/i.test(element.style.background + element.style.backgroundImage);
  }
  const unwrapIfEmpty = element => {
    if (element.tagName === 'SPAN' && element.attributes.length === 0) element.replaceWith(...element.childNodes);
  };
  // Split an inline ancestor around one text leaf, retaining all surrounding markup.
  const isolateAncestor = (leaf, ancestor) => {
    let branch = leaf;
    while (branch !== ancestor) {
      const parent = branch.parentNode;
      if (!parent || parent === article) break;
      const before = parent.cloneNode(false);
      const after = parent.cloneNode(false);
      while (parent.firstChild !== branch) before.appendChild(parent.firstChild);
      while (branch.nextSibling) after.appendChild(branch.nextSibling);
      if (before.childNodes.length) parent.before(before);
      if (after.childNodes.length) parent.after(after);
      branch = parent;
    }
  };
  const removeLeafHighlight = leaf => {
    let highlighted = leaf.parentElement;
    while (highlighted && highlighted !== article) {
      if (isHighlight(highlighted)) {
        isolateAncestor(leaf, highlighted);
        ['background', 'background-image', 'background-color', 'color', 'font-weight', 'padding', 'padding-left', 'padding-right'].forEach(property => highlighted.style.removeProperty(property));
        highlighted.removeAttribute('data-wg-highlight');
        highlighted.classList.remove('wg-highlight');
        if (!highlighted.className) highlighted.removeAttribute('class');
        if (!highlighted.style.cssText) highlighted.removeAttribute('style');
        if (/^(STRONG|B|MARK)$/.test(highlighted.tagName)) {
          const plain = document.createElement('span');
          [...highlighted.attributes].forEach(attribute => plain.setAttribute(attribute.name, attribute.value));
          plain.append(...highlighted.childNodes);
          highlighted.replaceWith(plain);
          highlighted = plain;
        }
        const next = highlighted.parentElement;
        unwrapIfEmpty(highlighted);
        highlighted = next;
      } else highlighted = highlighted.parentElement;
    }
  };
  const formatSelection = add => {
    const range = restoreSelection();
    if (!range || range.collapsed || !range.toString().trim()) { say('请先在正文中拖动选中需要处理的文字。', 'error'); return; }
    commit();
    const offsets = selectionOffsets();
    const segments = [];
    const walker = document.createTreeWalker(article, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (!range.intersectsNode(node)) continue;
      const start = range.startContainer === node ? range.startOffset : 0;
      const end = range.endContainer === node ? range.endOffset : node.length;
      if (end > start) segments.push({ node, start, end });
    }
    for (const segment of segments.reverse()) {
      let leaf = segment.node;
      if (segment.end < leaf.length) leaf.splitText(segment.end);
      if (segment.start > 0) leaf = leaf.splitText(segment.start);
      removeLeafHighlight(leaf);
      if (add && leaf.textContent.trim()) {
        const mark = document.createElement('span');
        mark.setAttribute('style', HIGHLIGHT);
        mark.setAttribute('data-wg-highlight', 'true');
        leaf.replaceWith(mark);
        mark.appendChild(leaf);
      }
    }
    article.normalize();
    selectOffsets(offsets);
    commit();
    say(add ? '已为选中文字添加浅蓝高亮；选区外的格式已保留。' : '已取消选中文字的高亮；选区外的格式已保留。', 'success');
  };
  const stepHistory = direction => {
    commit();
    const next = historyIndex + direction;
    if (next < 0 || next >= history.length) return;
    historyIndex = next;
    const state = history[historyIndex];
    article.innerHTML = state.html;
    title.value = state.title;
    selectOffsets(state.selection);
    saveDraft();
    updateUI();
    say(direction < 0 ? '已撤销上一步。' : '已重做。');
  };
  const ALLOWED_TAGS = new Set('SECTION P DIV H1 H2 H3 H4 H5 H6 SPAN STRONG EM B I U S DEL BLOCKQUOTE BR UL OL LI A IMG TABLE THEAD TBODY TFOOT TR TH TD SUP SUB PRE CODE'.split(' '));
  const ALLOWED_CSS = new Set(('color background background-color background-image font-family font-size font-weight font-style line-height letter-spacing text-align text-decoration text-decoration-color text-decoration-thickness text-underline-offset text-indent white-space word-break overflow-wrap margin margin-top margin-right margin-bottom margin-left padding padding-top padding-right padding-bottom padding-left border border-top border-right border-bottom border-left border-color border-style border-width border-radius box-shadow width max-width min-width height max-height min-height display vertical-align list-style list-style-type list-style-position border-collapse border-spacing box-sizing').split(' '));
  const cleanRoot = (inputHTML = article.innerHTML) => {
    const holder = document.createElement('div');
    holder.innerHTML = inputHTML;
    [...holder.querySelectorAll('script,style,iframe,object,embed,link,meta,input,button,textarea,select')].forEach(element => element.remove());
    [...holder.querySelectorAll('*')].forEach(element => {
      if (!ALLOWED_TAGS.has(element.tagName)) { element.replaceWith(...element.childNodes); return; }
      const styles = [];
      for (const property of element.style) {
        const value = element.style.getPropertyValue(property);
        if (ALLOWED_CSS.has(property) && !/url\s*\(|expression\s*\(|javascript:/i.test(value)) styles.push(`${property}: ${value}`);
      }
      const href = element.getAttribute('href');
      const src = element.getAttribute('src');
      const alt = element.getAttribute('alt');
      const span = element.getAttribute('colspan');
      const row = element.getAttribute('rowspan');
      [...element.attributes].forEach(attribute => element.removeAttribute(attribute.name));
      if (styles.length) element.setAttribute('style', styles.join('; ') + ';');
      if (element.tagName === 'A' && href && /^(https?:|mailto:)/i.test(href)) element.setAttribute('href', href);
      if (element.tagName === 'IMG' && src && /^(https?:|data:image\/(png|jpeg|gif|webp);base64,)/i.test(src)) { element.setAttribute('src', src); if (alt) element.setAttribute('alt', alt); }
      if (/^(TD|TH)$/.test(element.tagName)) { if (/^\d+$/.test(span || '')) element.setAttribute('colspan', span); if (/^\d+$/.test(row || '')) element.setAttribute('rowspan', row); }
    });
    const meaningful = [...holder.childNodes].filter(child => child.nodeType !== Node.TEXT_NODE || child.textContent.trim());
    if (meaningful.length === 1 && meaningful[0].nodeType === Node.ELEMENT_NODE && meaningful[0].tagName === 'SECTION') return meaningful[0];
    const root = document.createElement('section');
    root.setAttribute('style', ROOT_STYLE);
    root.append(...holder.childNodes);
    return root;
  };
  const plainText = root => {
    const blocks = /^(P|DIV|SECTION|H[1-6]|BLOCKQUOTE|LI|TR)$/;
    const read = node => {
      if (node.nodeType === Node.TEXT_NODE) return node.textContent;
      if (node.nodeType !== Node.ELEMENT_NODE) return '';
      if (node.tagName === 'BR') return '\n';
      const value = [...node.childNodes].map(read).join('');
      return blocks.test(node.tagName) ? value + '\n' : value;
    };
    return read(root).replace(/\n[ \t]+/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  };
  const fallbackCopy = (html, text) => {
    let success = false;
    const listener = event => {
      if (!event.clipboardData) return;
      event.preventDefault();
      event.clipboardData.setData('text/plain', text);
      if (html !== null) event.clipboardData.setData('text/html', html);
      success = true;
    };
    const staging = document.createElement('div');
    staging.setAttribute('contenteditable', 'true');
    staging.setAttribute('aria-hidden', 'true');
    staging.style.cssText = 'position:fixed;left:-9999px;top:0;width:600px;opacity:0;';
    if (html !== null) staging.innerHTML = html;
    else staging.textContent = text;
    document.body.appendChild(staging);
    const previous = savedRange ? savedRange.cloneRange() : null;
    const scroll = { x: window.scrollX, y: window.scrollY };
    suppressSelection = true;
    const range = document.createRange();
    range.selectNodeContents(staging);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    document.addEventListener('copy', listener);
    try { success = document.execCommand('copy') && success; } catch (_) { success = false; }
    finally {
      document.removeEventListener('copy', listener);
      staging.remove();
      selection.removeAllRanges();
      if (previous && previous.startContainer.isConnected) selection.addRange(previous);
      savedRange = previous;
      suppressSelection = false;
      window.scrollTo(scroll.x, scroll.y);
    }
    return success;
  };
  const copy = async (html, text) => {
    if (navigator.clipboard && window.ClipboardItem && html !== null) {
      try {
        await navigator.clipboard.write([new ClipboardItem({ 'text/html': new Blob([html], { type: 'text/html' }), 'text/plain': new Blob([text], { type: 'text/plain' }) })]);
        return true;
      } catch (_) { /* Local files and embedded browsers may require the copy-event fallback. */ }
    } else if (navigator.clipboard && html === null) {
      try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* Fallback below. */ }
    }
    return fallbackCopy(html, text);
  };
  $('copy-body').addEventListener('click', async () => {
    commit();
    const root = cleanRoot();
    const ok = await copy(root.outerHTML, plainText(root));
    say(ok ? '已复制当前正文（含修改后的高亮、卡片和 END）。请在公众号正文区普通粘贴。' : '浏览器未允许自动复制。请在正文内全选并复制，或用电脑浏览器打开下载的编辑版。', ok ? 'success' : 'error');
  });
  $('copy-title').addEventListener('click', async () => {
    commit();
    const ok = await copy(null, title.value);
    say(ok ? '已复制文章标题，请粘贴到公众号标题栏。' : '浏览器未允许自动复制，请选中标题文字手动复制。', ok ? 'success' : 'error');
  });
  $('download-editor').addEventListener('click', () => {
    commit();
    const clone = document.documentElement.cloneNode(true);
    clone.querySelector('#article-content').innerHTML = cleanRoot().outerHTML;
    clone.querySelector('#article-title').setAttribute('value', title.value);
    clone.querySelector('#document-data').textContent = JSON.stringify({ ...metadata, title: title.value, exportedAt: new Date().toISOString() }).replace(/</g, '\\u003c');
    clone.querySelector('title').textContent = (title.value || '公众号文章') + ' · WeChat OA';
    clone.querySelector('#draft-banner').hidden = true;
    clone.querySelector('#save-state').textContent = '初始版本';
    clone.querySelector('#save-state').removeAttribute('data-modified');
    clone.querySelector('#status').textContent = '此文件已包含导出时的修改，可以继续编辑和复制。';
    clone.querySelector('#status').removeAttribute('data-kind');
    const blob = new Blob(['<!doctype html>\n' + clone.outerHTML], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = (title.value || '公众号文章').replace(/[\\/:*?"<>|]/g, '_').slice(0, 90) + '-WeChat-OA编辑版.html';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
    say('已下载包含当前修改的独立编辑版。分享这个 HTML 文件，对方可直接打开使用。', 'success');
  });
  $('add-highlight').addEventListener('click', () => formatSelection(true));
  $('remove-highlight').addEventListener('click', () => formatSelection(false));
  $('undo').addEventListener('click', () => stepHistory(-1));
  $('redo').addEventListener('click', () => stepHistory(1));
  $('reset-article').addEventListener('click', () => {
    commit();
    article.innerHTML = initial.html;
    title.value = initial.title;
    savedRange = null;
    commit();
    say('已恢复此文件生成时的内容。如需保留刚才的修改，点击「撤销」。');
  });
  ['add-highlight', 'remove-highlight', 'undo', 'redo'].forEach(id => $(id).addEventListener('mousedown', event => event.preventDefault()));
  document.addEventListener('selectionchange', captureSelection);
  article.addEventListener('mouseup', captureSelection);
  article.addEventListener('keyup', captureSelection);
  article.addEventListener('input', queueCommit);
  title.addEventListener('input', queueCommit);
  article.addEventListener('paste', event => {
    // Paste as text into the editor so external HTML cannot inject scripts or foreign themes.
    event.preventDefault();
    commit();
    const text = event.clipboardData ? event.clipboardData.getData('text/plain') : '';
    const selection = window.getSelection();
    if (!selection || !selection.rangeCount || !selectionIsInside(selection.getRangeAt(0))) return;
    const range = selection.getRangeAt(0);
    range.deleteContents();
    const fragment = document.createDocumentFragment();
    const lines = text.replace(/\r\n?/g, '\n').split('\n');
    let last = null;
    lines.forEach((line, index) => { if (index) fragment.appendChild(document.createElement('br')); last = document.createTextNode(line); fragment.appendChild(last); });
    range.insertNode(fragment);
    if (last) { range.setStartAfter(last); range.collapse(true); selection.removeAllRanges(); selection.addRange(range); }
    captureSelection();
    commit();
    say('已以纯文本粘贴，可再选中文字设置高亮。');
  });
  document.addEventListener('keydown', event => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    const inEditor = article.contains(document.activeElement) || document.activeElement === title;
    if (!inEditor) return;
    const key = event.key.toLowerCase();
    if (key === 'z') { event.preventDefault(); stepHistory(event.shiftKey ? 1 : -1); }
    else if (key === 'y') { event.preventDefault(); stepHistory(1); }
  });
  window.addEventListener('beforeunload', () => { if (timer) commit(); });
  try {
    const raw = localStorage.getItem(storageKey);
    if (raw) {
      const draft = JSON.parse(raw);
      if (draft && typeof draft.html === 'string' && typeof draft.title === 'string' && (draft.html !== initial.html || draft.title !== initial.title)) {
        const date = new Date(draft.savedAt);
        $('draft-message').textContent = '发现这篇文章的本机草稿' + (Number.isNaN(date.getTime()) ? '。' : `（${date.toLocaleString('zh-CN')}）。`);
        $('draft-banner').hidden = false;
        $('restore-draft').addEventListener('click', () => {
          article.innerHTML = cleanRoot(draft.html).outerHTML;
          title.value = draft.title;
          savedRange = null;
          commit();
          $('draft-banner').hidden = true;
          say('已恢复本机草稿。复制和下载将使用恢复后的内容。', 'success');
        });
      }
    }
  } catch (_) { storageAvailable = false; }
  $('discard-draft').addEventListener('click', () => { try { localStorage.removeItem(storageKey); } catch (_) { storageAvailable = false; } $('draft-banner').hidden = true; updateUI(); });
  updateUI();
})();
