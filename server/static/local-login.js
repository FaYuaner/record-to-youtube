'use strict';
(() => {
  const notice = document.getElementById('localNotice'), button = document.getElementById('retryLocalOpen');
  const access = new URLSearchParams(location.hash.slice(1)).get('access') || '';
  history.replaceState({}, '', location.pathname);
  async function open() {
    button.hidden = true; notice.textContent = '正在打开本机工作台…';
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    try {
      const session = await fetch('./api/session', {signal:controller.signal, cache:'no-store'});
      if (session.ok && (await session.json()).authenticated) { location.replace('./'); return; }
      if (!access) throw new Error('请双击桌面「今日录制」打开本机工作台。');
      const response = await fetch('./api/local/open', {method:'POST', headers:{'X-Local-Access':access}, signal:controller.signal});
      if (!response.ok) throw new Error('无法打开本机工作台，请从桌面入口重试。');
      location.replace('./');
    } catch (error) {
      notice.textContent = error.name === 'AbortError' ? '打开超时，请重试。' : error.message;
      button.hidden = !access;
    } finally { clearTimeout(timer); }
  }
  button.addEventListener('click', open); open();
})();
