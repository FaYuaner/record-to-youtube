'use strict';
(() => {
  const button = document.getElementById('ownerLogin'), notice = document.getElementById('loginNotice');
  if (new URLSearchParams(location.search).has('error')) notice.textContent = '登录未完成。请使用已获准的 Google 账号重试。';
  button.addEventListener('click', async () => {
    button.disabled = true; button.textContent = '正在登录…'; notice.textContent = '正在连接 Google，请稍候…';
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch('./api/session', {cache:'no-store', signal:controller.signal});
      if (!response.ok) throw new Error('暂时无法连接，请重试。');
      const session = await response.json();
      if (session.authenticated) { location.replace('./'); return; }
      const auth = await fetch('./api/oauth/start', {method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token},body:'{}',signal:controller.signal});
      if (!auth.ok) throw new Error('暂时无法开始 Google 登录，请重试。');
      const url = new URL((await auth.json()).authorization_url);
      if (url.protocol !== 'https:' || url.hostname !== 'accounts.google.com') throw new Error('无法确认登录地址，请重试。');
      location.assign(url.href);
    } catch(error) { notice.textContent = error.name === 'AbortError' ? '连接超时，请重试。' : error.message; button.disabled = false; button.textContent = '重新登录'; }
    finally { clearTimeout(timer); }
  });
})();
