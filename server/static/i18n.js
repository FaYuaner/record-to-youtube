'use strict';
(() => {
  const messages = window.RecorderMessages;
  const supported = ['zh-Hans', 'zh-Hant', 'en'];
  const names = ['简体中文', '繁體中文', 'English'];
  const query = new URLSearchParams(location.search).get('lang');
  let stored; try { stored = localStorage.getItem('daigui.uiLanguage'); } catch (_) {}
  const locale = supported.includes(query) ? query : supported.includes(stored) ? stored : 'zh-Hans';
  try { localStorage.setItem('daigui.uiLanguage', locale); } catch (_) {}
  const languageIndex = supported.indexOf(locale);
  const aliases = new Map();
  for (const [source, translations] of Object.entries(messages)) {
    aliases.set(source, translations);
    aliases.set(translations[0], translations);
  }
  function t(source, ...values) {
    if (typeof source !== 'string') return source;
    const exact = aliases.get(source);
    const trimmed = source.trim(), entry = exact || aliases.get(trimmed);
    let result = entry ? entry[languageIndex] : source;
    if (entry && !exact) result = source.slice(0, source.indexOf(trimmed)) + result + source.slice(source.indexOf(trimmed) + trimmed.length);
    return result.replace(/\{(\d+)\}/g, (match, index) => index < values.length ? String(values[index]) : match);
  }
  window.RecorderI18n = { locale, t, beforeChange: null };
  document.documentElement.lang = locale;
  document.title = t(document.title);
  // Localize the static page template before the application adds account data or recordings.
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    if (node.parentElement?.closest('script,style,[data-no-i18n]')) continue;
    node.nodeValue = t(node.nodeValue);
  }
  for (const element of document.querySelectorAll('[aria-label],[placeholder],meta[name=description]')) {
    if (element.closest('[data-no-i18n]')) continue;
    for (const attr of ['aria-label', 'placeholder', 'content']) if (element.hasAttribute(attr)) element.setAttribute(attr, t(element.getAttribute(attr)));
  }
  const select = document.getElementById('languageSelect');
  if (!select) return;
  select.replaceChildren(...supported.map((code, index) => new Option(names[index], code)));
  select.setAttribute('aria-label', t('介面語言')); select.value = locale;
  const notice = document.getElementById('languageNotice');
  select.addEventListener('change', () => {
    const next = select.value;
    if (next === locale) { notice.textContent = t('目前已使用此語言。'); notice.hidden = false; return; }
    if (window.RecorderI18n.beforeChange?.() === false) { select.value = locale; return; }
    select.disabled = true; notice.textContent = t('正在切換語言…'); notice.hidden = false;
    try { localStorage.setItem('daigui.uiLanguage', next); } catch (_) {}
    const destination = new URL(location.href); destination.searchParams.set('lang', next);
    location.assign(destination.href);
  });
})();
