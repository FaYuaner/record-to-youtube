'use strict';

(() => {
  const API = './api';
  if (new URLSearchParams(location.hash.slice(1)).has('access')) history.replaceState({}, '', location.pathname + location.search);
  const locale = window.RecorderI18n || { locale: 'zh-Hant', t: (value, ...args) => value.replace(/\{(\d+)\}/g, (match, index) => args[index] ?? match) };
  const localMessages = {
    '伺服器上傳':'保存到本机队列',
    '伺服器影片已清理，YouTube 影片與文字記錄仍保留。':'本机副本已清理，YouTube 影片与文字记录仍保留。',
    '原片已保留在此裝置，等待傳送至伺服器。':'原片已保留在此设备，等待保存到本机队列。',
    '這段只保留在此设备，未上傳伺服器。':'原片已留在此设备。',
    '原片已保留在本機，請選擇是否上傳伺服器。':'原片已保留在本机，可以准备上传。',
    '這段尚未確認上傳。請在錄製列表選擇是否上傳伺服器。':'请在录制列表选择要准备的视频。',
    '成片完成後，自動以「{0}」上傳到你的 YouTube 頻道。':'原片或剪辑成片准备完成后，自动以「{0}」上传 YouTube。',
    '先製作成片，再由你確認上傳。':'视频准备完成后，由你确认上传。',
    '嘗試啟動剪輯':'继续准备视频',
    '重新產生文案':'重试准备视频',
    '正在暫停上傳…':'正在暂停传送…'
  };
  const t = (value, ...args) => locale.t(state.session?.mode === 'local' && localMessages[value] ? localMessages[value] : value, ...args);
  const $ = (id) => document.getElementById(id);
  const pageTitle = document.title;
  const state = { session: null, stream: null, recorder: null, recording: false, starting: false, stopping: false, chunks: [], startedAt: 0, folder: null, writer: null, writeChain: Promise.resolve(), writeError: null, timer: null, audio: null, analyser: null, animation: null, pending: new Map(), uploading: new Set(), uploadControls: new Map(), decidingUpload: new Set(), jobs: [], editing: new Set(), drafts: new Map(), recordingPreferences: null, lastPending: null, busyAccount: false, poll: null, devicesBusy: false, deviceListVersion: 0, transfers: new Map() };
  const privacyNames = { private: t('私人'), unlisted: t('不公開'), public: t('公開') };
  const stateNames = { local_only: t('僅存本機'), local_decision: t('等待你的選擇'), receiving: t('原片上傳中'), queued: t('等待製作'), processing: t('正在製作'), ready: t('成片已完成'), publish_queued: t('等待上傳 YouTube'), publishing: t('正在上傳 YouTube'), youtube_processing: t('YouTube 處理中'), published: t('已公開發布'), private: t('已上傳 · 私人'), unlisted: t('已上傳 · 不公開'), failed: t('需要處理'), disconnected: t('已解除連結'), records_cleared: t('上傳紀錄已清除') };
  const ACTIVE_STATES = new Set(['receiving', 'queued', 'processing', 'publish_queued', 'publishing', 'youtube_processing']);
  const QUALITY_PRESETS = { '1080': { width: 1920, height: 1080, bitrate: 8000000 }, '720': { width: 1280, height: 720, bitrate: 4000000 }, '480': { width: 854, height: 480, bitrate: 1500000 } };
  const isMobile = matchMedia('(pointer: coarse)').matches;
  let selectedQuality = isMobile ? '720' : '1080';
  let orientation = 'auto', cloudRevision = 0, wakeLock = null, importing = false;
  let captureId = null, captureIndex = 0, captureChain = Promise.resolve(), captureError = null;
  let recordOrientation = null;
  try { const value = localStorage.getItem('daigui.quality'); if (Object.hasOwn(QUALITY_PRESETS, value)) selectedQuality = value; } catch (_) {}
  let dbPromise;
  let accountDetailsVisible = false;
  let previewPromise = null, previewBusy = false, streamDevices = '';
  let confirmationQueue = Promise.resolve();
  let preferences = { privacy: 'private', auto_publish: false };
  let preferencesRestored = false, processingMode = 'direct';
  const localMode = () => state.session?.mode === 'local';
  let devicePreferences = { camera: '', microphone: '' };
  let preferredFacing = 'user', exactFacing = false, switchingCamera = false;
  try { if (localStorage.getItem('daigui.cameraFacing') === 'environment') { preferredFacing = 'environment'; exactFacing = true; } } catch (_) {}
  try { const stored = JSON.parse(localStorage.getItem('daigui.devices') || '{}'); for (const kind of ['camera', 'microphone']) if (typeof stored[kind] === 'string') devicePreferences[kind] = stored[kind]; } catch (_) {}
  try { const stored = JSON.parse(localStorage.getItem('daigui.preferences') || 'null'); if (stored && Object.hasOwn(privacyNames, stored.privacy)) { preferences = { privacy: stored.privacy, auto_publish: stored.version >= 2 && stored.auto_publish === true }; preferencesRestored = true; } } catch (_) {}

  const noticeTimers = new Map();
  function notify(id, message, type = '', duration = 0) { clearTimeout(noticeTimers.get(id)); noticeTimers.delete(id); const el = $(id); el.textContent = message; el.className = 'notice' + (type ? ' ' + type : ''); el.hidden = !message; if (message && duration > 0 && type !== 'error') noticeTimers.set(id, setTimeout(() => { el.hidden = true; el.textContent = ''; noticeTimers.delete(id); }, duration)); }
  function statusNotice(el, message, type = '') { el.textContent = message; el.classList.add('notice'); el.classList.remove('busy', 'error', 'success'); if (type) el.classList.add(type); el.hidden = !message; }
  function messageOf(error) { return t(error?.message) || t('操作未完成，請稍後重試。'); }
  function errorText(body, status) { const msg = body?.message || body?.error || body?.detail; return typeof msg === 'string' ? t(msg) : status === 401 ? t('登入已到期，請重新連結 Google 帳戶。') : t('連線未完成（{0}），請重試。', status); }
  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.method && options.method !== 'GET') headers.set('X-CSRF-Token', state.session?.csrf_token || '');
    if (options.json !== undefined) { headers.set('Content-Type', 'application/json'); options.body = JSON.stringify(options.json); }
    const method = (options.method || 'GET').toUpperCase();
    const timeoutMs = method === 'PUT' && /^\/jobs\/[^/]+\/chunks\/\d+$/.test(path) ? 180000 : method === 'POST' && (path === '/disconnect' || /^\/jobs\/[^/]+\/complete$/.test(path)) ? 120000 : 30000;
    const controller = new AbortController();
    const externalSignal = options.signal;
    const forwardAbort = () => controller.abort(externalSignal.reason);
    if (externalSignal?.aborted) forwardAbort();
    else externalSignal?.addEventListener('abort', forwardAbort, { once: true });
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    try {
      const response = await fetch(API + path, { ...options, headers, signal: controller.signal, credentials: 'same-origin', cache: 'no-store' });
      let body = null;
      try { body = await response.json(); } catch (error) { if (controller.signal.aborted) throw error; }
      if (response.status === 401 && state.session?.authenticated) { state.session.authenticated = false; state.jobs = []; state.drafts.clear(); document.querySelector('.workspace').hidden = true; if (state.recording) stopRecording(); else releasePreview(); notify('languageNotice', t('登录已到期。请重新打开页面登录，原片仍保留在此设备。'), 'error'); }
      if (!response.ok) { const e = new Error(errorText(body, response.status)); e.status = response.status; throw e; }
      return body;
    } catch (error) {
      if (timedOut) { const timeoutError = new Error(t('請求逾時，目前無法確認伺服器是否已完成。請重新整理狀態後再重試。')); timeoutError.code = 'REQUEST_TIMEOUT'; throw timeoutError; }
      throw error;
    } finally { clearTimeout(timer); externalSignal?.removeEventListener('abort', forwardAbort); }
  }
  function database() {
    if (!dbPromise) dbPromise = new Promise((resolve, reject) => { const req = indexedDB.open('daigui-recorder', 2); req.onupgradeneeded = () => { if (!req.result.objectStoreNames.contains('settings')) req.result.createObjectStore('settings'); if (!req.result.objectStoreNames.contains('pending')) req.result.createObjectStore('pending', { keyPath: 'client_id' }); if (!req.result.objectStoreNames.contains('captures')) req.result.createObjectStore('captures', { keyPath: 'client_id' }); if (!req.result.objectStoreNames.contains('captureChunks')) req.result.createObjectStore('captureChunks', { keyPath: ['client_id', 'index'] }); }; req.onsuccess = () => resolve(req.result); req.onerror = () => reject(req.error); });
    return dbPromise;
  }
  async function dbAction(store, mode, action) { const db = await database(); return new Promise((resolve, reject) => { const tx = db.transaction(store, mode); const req = action(tx.objectStore(store)); tx.oncomplete = () => resolve(req.result); tx.onerror = () => reject(tx.error); tx.onabort = () => reject(tx.error); }); }
  function savePending(item) { return dbAction('pending', 'readwrite', (store) => store.put(item)); }
  function confirmation(title, body, accept = t('確認'), cancel = t('取消')) {
    const result = confirmationQueue.then(() => new Promise((resolve) => {
      const dialog = $('confirmDialog'); $('confirmTitle').textContent = title; $('confirmBody').textContent = body; $('confirmAccept').textContent = accept; $('confirmCancel').textContent = cancel; dialog.returnValue = '';
      dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true }); dialog.showModal(); $('confirmCancel').focus();
    }));
    confirmationQueue = result.catch(() => {}); return result;
  }
  function uploadApproved(item) { return item && (item.upload_decision === 'approved' || (item.upload_decision == null && !!item.job_id)); }
  async function resumeApprovedUploads() { await Promise.all([...state.pending.values()].filter(uploadApproved).map((item) => uploadPending(item))); }
  async function chooseUpload(item) {
    if (!item) return;
    if (state.decidingUpload.has(item.client_id)) { notify('recordNotice', t('正在處理這段錄製的選擇，請稍候。'), 'busy'); return; }
    if (uploadApproved(item)) { uploadPending(item); return; }
    state.decidingUpload.add(item.client_id);
    try {
      if (localMode()) {
        if (!state.session.youtube_connected) {
          item.upload_decision = 'local_only'; await savePending(item); renderJobs();
          notify('recordNotice', t('原片已保留在此设备。连接自己的 YouTube 频道后，可在列表上传。'), 'success');
          return;
        }
        const accepted = item.auto_publish || await confirmation(t('准备这段录制？'),
          t('视频会交给本机上传队列，按本次处理方式准备。自动上传关闭时，完成后可预览并手动上传。'), t('准备视频'), t('只留原片'));
        item.upload_decision = accepted ? 'approved' : 'local_only'; item.error = null;
        await savePending(item); renderJobs();
        if (accepted) { notify('recordNotice', t('正在将原片保存到本机队列…'), 'busy'); await uploadPending(item); }
        else notify('recordNotice', t('原片已留在此设备。'), 'success');
        return;
      }
      const next = item.auto_publish ? t('伺服器會剪輯、整理文案，並按這段錄製的設定上傳 YouTube。') : t('伺服器會剪輯、整理文案，成片完成後再由你決定是否上傳 YouTube。');
      const accepted = await confirmation(t('是否上傳這段錄製到伺服器？'), t('「') + item.filename + '」\n\n' + next + (item.auto_publish ? '\n' + t('可見範圍') + '：' + privacyNames[item.privacy] : '') + t('\n\n選「否」會只保留本機原片，這段不會自動上傳。'), t('是，上傳伺服器'), t('否，留在此设备'));
      item.upload_decision = accepted ? 'approved' : 'local_only'; item.error = null;
      notify('recordNotice', accepted ? t('已確認上傳，正在準備原片…') : t('正在保存留在此设备的選擇…'), 'busy');
      let stored = true; try { await savePending(item); } catch (_) { stored = false; }
      renderJobs();
      if (!accepted) { notify('recordNotice', t('這段已選擇留在此设备，未上傳伺服器。可以直接開始下一次錄製。') + (!stored ? t('\n瀏覽器未能記住本次選擇；重新開啟時仍需確認，原片不會自動上傳。請確認下載的原片已保存。') : ''), 'success'); $('recordHelp').textContent = t('可以開始下一次錄製。'); return; }
      notify('recordNotice', t('已確認上傳，正在傳送原片。請保持此視窗開啟。') + (!stored ? t('\n瀏覽器未能保留恢復資料，請等原片傳送完成後再關閉。') : ''), 'busy');
      await uploadPending(item);
    } catch (error) { notify('recordNotice', t('尚未開始上傳：') + messageOf(error) + t('。可在錄製列表重新選擇。'), 'error'); }
    finally { state.decidingUpload.delete(item.client_id); }
  }
  function seconds(t) { const secs = Math.max(0, Math.floor(t)); const h = Math.floor(secs / 3600); return (h ? String(h).padStart(2, '0') + ':' : '') + String(Math.floor(secs / 60) % 60).padStart(2, '0') + ':' + String(secs % 60).padStart(2, '0'); }
  function bytes(n) { return n >= 1024 ** 3 ? (n / 1024 ** 3).toFixed(1) + ' GB' : (n / 1024 ** 2).toFixed(1) + ' MB'; }
  function fileName(mime = '') { const d = new Date(); const p = (n) => String(n).padStart(2, '0'); return 'Daigui_' + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '_' + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds()) + (mime.includes('mp4') ? '.mp4' : '.webm'); }
  function setRecordButton(text, busy, recording = false) { $('recordButtonText').textContent = text; $('recordButton').disabled = busy; $('recordButton').classList.toggle('recording', recording); }
  function showRecordDock(active) {
    const button = $('recordButton'), focused = document.activeElement === button;
    if (active) $('recordDock').append(button); else $('recordControls').prepend(button);
    $('recordDock').hidden = !active; document.body.classList.toggle('recording-active', active);
    if (focused) button.focus({ preventScroll: true });
  }
  function updatePrefs() { $('defaultPrivacy').value = preferences.privacy; $('autoPublish').checked = preferences.auto_publish; $('preferencesSummary').textContent = preferences.auto_publish ? t('成片完成後，自動以「{0}」上傳到你的 YouTube 頻道。', privacyNames[preferences.privacy]) : t('先製作成片，再由你確認上傳。'); try { localStorage.setItem('daigui.preferences', JSON.stringify({ ...preferences, version: 3 })); } catch (_) {} }
  function applyProcessingCopy() {
    const builtIn = processingMode === 'builtin';
    $('recordTopic').placeholder = builtIn ? t('留空時，根據口播內容提煉標題') : t('留空时使用录制文件名');
    $('recordDescription').placeholder = builtIn ? t('留空時，根據口播內容整理簡介') : t('选填，直接使用你填写的内容');
    $('metadataHelp').textContent = builtIn ? t('填寫的內容直接使用，留空的欄位才會自動生成。設定套用於下一次錄製。') : t('直接上传使用原片和你的文案；自定义剪辑可返回成片与文案。');
    $('languageNote').hidden = !builtIn;
  }
  async function prefsChanged() {
    const next = { privacy: $('defaultPrivacy').value, auto_publish: $('autoPublish').checked };
    if (next.privacy === 'public' && !state.session?.public_enabled) { updatePrefs(); notify('preferencesNotice', t('公開發布尚未啟用。你可以先製作成片，或選擇私人、不公開上傳。'), 'error'); return; }
    if (next.auto_publish && next.privacy === 'public' && !(preferences.auto_publish && preferences.privacy === 'public')) {
      const ok = await confirmation(t('啟用自動公開發布？'), t('之後的新錄製在剪輯與文案完成後，會自動公開發布到目前連結的 YouTube 頻道。所有人都可能觀看影片。\n\n你可以隨時關閉這項設定；已開始處理的錄製會沿用開始錄製時的設定。'), t('啟用自動公開發布'));
      if (!ok) { updatePrefs(); notify('preferencesNotice', t('已取消，原設定保持不變。')); return; }
    }
    preferences = next; updatePrefs(); notify('preferencesNotice', (localMode() ? t('已修改。点击「保存设置」记住本次选择。') : t('已修改。点击「保存到账号」，即可在其他设备使用这些设置。')));
  }
  function renderAccountDetails() {
    const connected = !!state.session?.youtube_connected;
    if (!connected) accountDetailsVisible = false;
    $('accountTitle').textContent = connected ? (accountDetailsVisible ? state.session.user?.channel_title || t('Google 帳戶已連結') : t('頻道已連結')) : t('連結你的頻道');
    $('accountSubtitle').textContent = connected ? (accountDetailsVisible ? state.session.user?.email || t('可上傳到你的 YouTube 頻道') : t('帳號資訊已隱藏')) : t('透過 Google 安全授權');
    $('toggleAccountDetails').hidden = !connected;
    $('toggleAccountDetails').textContent = accountDetailsVisible ? t('隱藏帳號資訊') : t('顯示帳號資訊');
    $('toggleAccountDetails').setAttribute('aria-expanded', String(accountDetailsVisible));
  }
  async function loadSession() {
    const previousAccount = JSON.stringify(state.session?.user || null);
    state.session = await api('/session'); updateRecordingBudget();
    if (!preferencesRestored) { preferences.auto_publish = state.session.default_auto_publish === true; preferencesRestored = true; }
    if (!state.sessionModeLoaded) { processingMode = state.session.default_processing_mode || 'builtin'; state.sessionModeLoaded = true; }
    $('processingMode').value = processingMode; updatePrefs();
    $('externalMode').disabled = !state.session.external_processor_available; applyProcessingCopy();
    if (localMode()) {
      $('retentionHelp').textContent = t('原片、成片与上传队列保存在本机。上传期间请保持电脑运行。');
      $('saveCloudSettings').textContent = t('保存设置');

      $('recordHelp').textContent = t('先保存原片，连接频道后按设置自动上传 YouTube。');
    }
    if (previousAccount !== JSON.stringify(state.session.user || null)) accountDetailsVisible = false;
    const connected = state.session.youtube_connected;
    if (!state.session.authenticated) { releasePreview(); state.jobs = []; state.pending.clear(); state.drafts.clear(); document.querySelector('.workspace').hidden = true; location.replace('./'); return; }
    $('connectionText').textContent = connected ? t('頻道已連結') : t('尚未連結頻道'); $('connectionText').parentElement.classList.toggle('connected', connected);
    renderAccountDetails();
    $('connectButton').hidden = connected; $('accountActions').hidden = !connected;
    $('logoutButton').hidden = localMode();
    $('authPurpose').textContent = connected ? t('影片只會上傳到此處顯示的頻道。你可以隨時登出或解除授權。') : t('用於辨識你的頻道、上傳錄製影片，以及查詢發布結果。授權前可查看下方的隱私政策。');
    $('publicAvailability').hidden = !!state.session.public_enabled;
    if (state.session.auth_error) notify('authNotice', t(state.session.auth_error), 'error'); else if (connected && $('authNotice').classList.contains('error')) notify('authNotice', '');
    if (!state.session.public_enabled && preferences.privacy === 'public') { notify('preferencesNotice', t('目前無法啟用公開上傳，請選擇不公開或稍後重試。'), 'error'); }
  }
  async function connect() { if (state.busyAccount) return; if (state.recording || state.starting || state.stopping || state.uploading.size) { notify('authNotice', t('請先結束錄製並等原片保存完成，再連接頻道。'), 'error'); return; } state.busyAccount = true; $('connectButton').disabled = $('reauthorizeButton').disabled = true; notify('authNotice', t('正在開啟 Google 授權頁面…'), 'busy'); try { if (!state.session) await loadSession(); const result = await api('/oauth/start', { method: 'POST', json: {} }); const url = new URL(result.authorization_url); if (url.protocol !== 'https:' || url.hostname !== 'accounts.google.com') throw new Error(t('授權網址無法確認，請稍後重試。')); location.assign(url.href); } catch (error) { notify('authNotice', messageOf(error), 'error'); $('connectButton').disabled = $('reauthorizeButton').disabled = false; state.busyAccount = false; } }
  async function accountAction(disconnect) {
    if (state.busyAccount) return;
    if (disconnect && !await confirmation(t('解除 Google 連結？'), t('解除後，工具將撤銷 Google 授權並清除相關頻道與上傳資料，無法繼續上傳或查詢 YouTube 影片。\n\n已發布的影片、此设备上的原片與伺服器中的錄製不會被刪除。為避免重複發布，曾開始上傳的錄製須到 YouTube 工作室核查。'), t('解除連結'))) { notify('authNotice', t('已取消解除連結。')); return; }
    if (state.recording || state.uploading.size) { notify('authNotice', t('目前仍在錄製或傳送原片，請等這些工作結束後再操作。'), 'error'); return; }
    state.busyAccount = true; $('logoutButton').disabled = $('disconnectButton').disabled = true; notify('authNotice', disconnect ? t('正在解除連結…') : t('正在登出…'), 'busy');
    try { await api(disconnect ? '/disconnect' : '/logout', { method: 'POST', json: {} }); await loadSession(); state.jobs = []; renderJobs(); notify('authNotice', disconnect ? t('已解除 Google 連結。') : t('已登出。')); } catch (e) { notify('authNotice', messageOf(e), 'error'); } finally { state.busyAccount = false; $('logoutButton').disabled = $('disconnectButton').disabled = false; }
  }
  async function chooseFolder() {
    if (!window.showDirectoryPicker) { $('storageFeedback').textContent = t('此瀏覽器未提供資料夾選擇。原片會透過瀏覽器下載，請確認下載位置。'); return; }
    if (state.recording || state.starting) { $('storageFeedback').textContent = t('請在這次錄製結束後變更儲存資料夾。'); return; }
    $('chooseFolder').disabled = true; $('storageFeedback').textContent = t('請在開啟的視窗中選擇原片儲存資料夾。');
    try { state.folder = await window.showDirectoryPicker({ id: 'daigui-originals', mode: 'readwrite' }); await dbAction('settings', 'readwrite', (store) => store.put(state.folder, 'folder')); $('storageLabel').textContent = t('本機儲存：') + state.folder.name; $('storageFeedback').textContent = t('已選擇。之後錄製的原片將保存到此資料夾。'); } catch (error) { $('storageFeedback').textContent = error.name === 'AbortError' ? t('已取消選擇，原儲存方式保持不變。') : t('無法保存資料夾設定：') + messageOf(error); } finally { $('chooseFolder').disabled = false; }
  }
  function lockRecordingFields(busy) {
    for (const id of ['recordTopic', 'recordDescription']) $(id).disabled = busy;
    for (const id of ['cameraSelect', 'microphoneSelect', 'qualitySelect', 'orientationSelect', 'chooseVideo']) $(id).disabled = busy || previewBusy || state.devicesBusy;
    $('refreshDevices').disabled = busy || state.devicesBusy || previewBusy;
  }
  function validateRecordingFields() {
    if (state.devicesBusy) { notify('recordNotice', t('正在檢查攝影機與麥克風，完成後即可開始錄製。'), 'busy'); return false; }
    const title = $('recordTopic'), description = $('recordDescription'); let first = null;
    for (const [input, error, message] of [
      [title, $('recordTopicError'), title.value.length > 100 || /[<>\r\n]/.test(title.value) ? t('標題最多 100 個字元，不能換行或包含 <、>。') : ''],
      [description, $('recordDescriptionError'), new TextEncoder().encode(description.value).length > 5000 || /[<>]/.test(description.value) ? t('簡介最多 5,000 位元組（中文約 1,600 字），不能包含 <、>。') : '']
    ]) { error.textContent = message; error.hidden = !message; input.setAttribute('aria-invalid', String(!!message)); if (message) first ||= input; }
    if (first) { first.scrollIntoView({ behavior: 'smooth', block: 'center' }); first.focus({ preventScroll: true }); notify('recordNotice', t('請先修正標示的設定，再開始錄製。'), 'error'); return false; }
    return true;
  }
  async function refreshDeviceList() {
    if (!navigator.mediaDevices?.enumerateDevices) throw new Error(t('此瀏覽器無法列出裝置，請使用最新版 Chrome 或 Edge。'));
    const version = ++state.deviceListVersion, devices = await navigator.mediaDevices.enumerateDevices();
    if (version !== state.deviceListVersion) return devices;
    const missing = [];
    for (const [kind, type, label] of [['camera', 'videoinput', t('攝影機')], ['microphone', 'audioinput', t('麥克風')]]) {
      const select = $(kind + 'Select'), available = devices.filter((d) => d.kind === type && d.deviceId);
      select.replaceChildren(new Option(t('系統預設') + label, ''));
      available.forEach((device, i) => select.add(new Option(device.label || label + ' ' + (i + 1), device.deviceId)));
      if (devicePreferences[kind] && !available.some((d) => d.deviceId === devicePreferences[kind])) {
        const unavailable = new Option(t('先前選擇的') + label + t('暫時不可用'), devicePreferences[kind]); unavailable.dataset.unavailable = 'true'; select.add(unavailable); missing.push(label);
      }
      select.value = devicePreferences[kind];
    }
    if (missing.length && !state.recording && !state.starting) notify('deviceNotice', t('先前選擇的') + missing.join(t('、')) + t('暫時不可用。請允許裝置權限、重新連接或選擇其他裝置。'), 'error');
    return devices;
  }
  async function deviceChanged(kind) {
    if (state.recording || state.starting || state.stopping) { $(kind + 'Select').value = devicePreferences[kind]; return; }
    devicePreferences[kind] = $(kind + 'Select').value;
    let saved = true; try { localStorage.setItem('daigui.devices', JSON.stringify(devicePreferences)); } catch (_) { saved = false; }
    if (kind === 'camera') exactFacing = false;
    try { await preparePreview({ feedback: true }); notify('deviceNotice', t('已選擇「') + $(kind + 'Select').selectedOptions[0].textContent + t('」，將用於下一次錄製。') + (saved ? '' : t('瀏覽器未能記住設定，下次開啟請重新選擇。')), saved ? 'success' : 'error', saved ? 4000 : 0); } catch (_) {}
  }
  const cameraCopy = {
    front: ['切到前置', '切換前鏡頭', 'Switch to front'], rear: ['切到后置', '切換後鏡頭', 'Switch to rear'], busy: ['切换中…', '切換中…', 'Switching…'],
    wait: ['正在切换摄像头…', '正在切換鏡頭…', 'Switching cameras…'], recording: ['请先结束录制，再切换摄像头。', '請先結束錄製，再切換鏡頭。', 'Finish the recording before switching cameras.'],
    loading: ['摄像头正在准备，请稍后重试。', '鏡頭正在準備，請稍後重試。', 'The camera is getting ready. Try again shortly.'],
    readyFront: ['已切换到前置摄像头，请检查画面。', '已切換到前鏡頭，請檢查畫面。', 'Front camera ready. Check the preview.'],
    readyRear: ['已切换到后置摄像头，请检查画面。', '已切換到後鏡頭，請檢查畫面。', 'Rear camera ready. Check the preview.'],
    failed: ['切换失败，已尝试恢复原来的摄像头：', '切換失敗，已嘗試恢復原來的鏡頭：', 'Switch failed. Tried to restore the previous camera: '],
    unavailable: ['当前无法确认所选摄像头，请检查预览画面。', '目前無法確認所選鏡頭，請檢查預覽畫面。', 'Could not confirm the selected camera. Check the preview.']
  };
  function cameraMessage(key) { return cameraCopy[key][Math.max(0, ['zh-Hans', 'zh-Hant', 'en'].indexOf(locale.locale))]; }
  function updateCameraSwitch() {
    if (!isMobile) return;
    const actual = state.stream?.getVideoTracks()[0]?.getSettings()?.facingMode;
    if (actual === 'user' || actual === 'environment') preferredFacing = actual;
    const rear = actual === 'environment' || (!actual && preferredFacing === 'environment' && !devicePreferences.camera);
    $('cameraStage').classList.toggle('rear-camera', rear);
    const label = cameraMessage(rear ? 'front' : 'rear');
    $('switchCamera').textContent = '↻ ' + label;
    $('switchCamera').setAttribute('aria-label', label);
  }
  async function switchCamera() {
    if (state.recording || state.starting || state.stopping) { notify('cameraSwitchNotice', cameraMessage('recording'), 'error'); return; }
    if (!state.session?.authenticated || previewBusy || state.devicesBusy || switchingCamera || importing) { notify('cameraSwitchNotice', cameraMessage('loading'), 'busy'); return; }
    switchingCamera = true;
    const previous = { facing: preferredFacing, exact: exactFacing, device: devicePreferences.camera };
    const next = $('cameraStage').classList.contains('rear-camera') ? 'user' : 'environment';
    $('switchCamera').disabled = true; $('switchCamera').textContent = cameraMessage('busy'); notify('cameraSwitchNotice', cameraMessage('wait'), 'busy');
    preferredFacing = next; exactFacing = true; devicePreferences.camera = '';
    try {
      await preparePreview();
      const actual = state.stream?.getVideoTracks()[0]?.getSettings()?.facingMode;
      if (actual && actual !== next) throw new Error(cameraMessage('unavailable'));
      try { localStorage.setItem('daigui.cameraFacing', next); localStorage.setItem('daigui.devices', JSON.stringify(devicePreferences)); } catch (_) {}
      updateCameraSwitch(); notify('cameraSwitchNotice', cameraMessage(next === 'environment' ? 'readyRear' : 'readyFront'), 'success');
    } catch (error) {
      preferredFacing = previous.facing; exactFacing = previous.exact; devicePreferences.camera = previous.device;
      try { await preparePreview(); } catch (_) {}
      updateCameraSwitch(); notify('cameraSwitchNotice', cameraMessage('failed') + deviceError(error), 'error');
    } finally { switchingCamera = false; $('switchCamera').disabled = false; updateCameraSwitch(); }
  }
  function deviceError(error) {
    return ({ NotAllowedError: t('攝影機或麥克風未獲得允許。請在網址列旁開啟權限後重試。'), NotFoundError: t('找不到攝影機或麥克風，請連接裝置後重試。'), OverconstrainedError: t('所選的攝影機或麥克風已無法使用，請重新連接或選擇其他裝置後再錄製。'), NotReadableError: t('攝影機或麥克風無法使用，請關閉正在佔用裝置的程式後重試。') })[error.name] || messageOf(error);
  }
  function streamReady() { return state.stream?.getVideoTracks().length && state.stream?.getAudioTracks().length && state.stream.getTracks().every((track) => track.readyState === 'live'); }
  function showCaptureState() {
    const ready = !!streamReady();
    $('cameraEmpty').hidden = ready; $('liveOverlay').hidden = !ready;
    $('previewStatus').hidden = state.recording; $('recordIndicator').hidden = $('recordTime').hidden = !state.recording;
    $('recordBadge').textContent = state.recording ? t('正在錄製') : ready ? t('預覽中') : t('裝置未就緒'); $('recordBadge').classList.toggle('recording', state.recording);
  }
  function releasePreview() {
    cancelAnimationFrame(state.animation); if (state.audio) { state.audio.onstatechange = null; state.audio.close().catch(() => {}); } state.audio = null;
    state.stream?.getTracks().forEach((track) => track.stop()); state.stream = null; streamDevices = '';
    $('captureQuality').hidden = true; $('captureQuality').textContent = '';
    $('cameraPreview').srcObject = null; $('audioLevel').style.width = '0'; $('enableMeter').hidden = true;
  }
  function updateRecordingBudget() {
    const limit = state.session?.limits?.max_upload_bytes;
    if (!limit) { $('recordingBudget').textContent = t('正在讀取單段上傳上限…'); return; }
    const preset = QUALITY_PRESETS[selectedQuality];
    const minutes = Math.max(0, Math.floor((limit - 8 * 1024 ** 2) * 8 / (preset.bitrate + 128000) / 60));
    $('recordingBudget').textContent = t('單段上傳上限：{0}。所選清晰度預估可錄約 {1} 分鐘。', bytes(limit), minutes) + t('時長僅供參考，實際大小會隨畫面變化；接近上限時會自動結束並保存。');
    const available = state.session?.limits?.available_upload_bytes;
    if (Number.isFinite(available) && available < limit) {
      const safeMinutes = Math.max(0, Math.floor(available * 8 / (preset.bitrate + 128000) / 60));
      $('recordingBudget').textContent = t('当前可上传约 {0}，按所选画质估算约 {1} 分钟。', bytes(available), safeMinutes) + t('实际大小会随画面变化；开始上传时会再次检查可用空间。');
    }
    $('recordedSize').hidden = !(state.recording || state.stopping);
    $('recordedSize').textContent = t('已錄製大小：{0} / {1}', bytes(state.recordedBytes || 0), bytes(limit));
  }
  async function changeQuality() {
    if (state.recording || state.starting || state.stopping || previewBusy) { $('qualitySelect').value = selectedQuality; return; }
    selectedQuality = $('qualitySelect').value;
    updateRecordingBudget(); notify('qualityNotice', t('正在切換錄製清晰度…'), 'busy');
    try {
      await preparePreview();
      try { localStorage.setItem('daigui.quality', selectedQuality); } catch (_) {}
      notify('qualityNotice', t('清晰度已切換，下次錄製將使用此設定。'), 'success');
    } catch (error) { notify('qualityNotice', deviceError(error), 'error'); }
  }
  function updateCaptureQuality() {
    const settings = state.stream?.getVideoTracks()[0]?.getSettings() || {};
    const width = settings.width || $('cameraPreview').videoWidth;
    const height = settings.height || $('cameraPreview').videoHeight;
    const label = $('captureQuality'); label.hidden = false;
    if (!width || !height) { label.textContent = t('目前無法讀取實際錄製解析度。'); return; }
    label.textContent = t('目前錄製：{0} × {1}', width, height) + (settings.frameRate ? t(' · {0} 幀／秒', Math.round(settings.frameRate)) : '');
    $('cameraStage').style.aspectRatio = width + ' / ' + height;
    $('cameraStage').classList.toggle('portrait-video', height > width);
    if ((height > width) !== (captureDimensions().height > captureDimensions().width)) label.textContent += t('。摄像头返回的方向与所选方向不同，请旋转手机并检查预览后录制。');
    if (Math.min(width, height) < QUALITY_PRESETS[selectedQuality].height) label.textContent += t('。目前畫面未達所選清晰度，將按實際解析度錄製。');
  }
  function preparePreview({ feedback = false } = {}) {
    if (previewPromise) return previewPromise;
    const dimensions = captureDimensions();
    const selected = JSON.stringify({ ...devicePreferences, quality: selectedQuality, ...dimensions, preferredFacing, exactFacing });
    if (streamReady() && streamDevices === selected) { startMeter(); return Promise.resolve(state.stream); }
    previewBusy = true; lockRecordingFields(state.starting || state.recording || state.stopping);
    const showHint = () => notify('deviceNotice', t('正在開啟攝影機與麥克風預覽。若出現權限視窗，請選擇允許。'), 'busy');
    const hintTimer = feedback ? (showHint(), null) : setTimeout(showHint, 1000);
    previewPromise = (async () => {
      releasePreview();
      if (!navigator.mediaDevices?.getUserMedia) throw new Error(t('无法打开摄像头。iPhone 请用 Safari 打开，并允许摄像头与麦克风权限。'));
      state.stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: dimensions.width }, height: { ideal: dimensions.height }, aspectRatio: { ideal: dimensions.width / dimensions.height }, frameRate: { ideal: 30, max: 30 }, ...(devicePreferences.camera ? { deviceId: { exact: devicePreferences.camera } } : { facingMode: exactFacing ? { exact: preferredFacing } : { ideal: preferredFacing } }) }, audio: { echoCancellation: true, noiseSuppression: true, ...(devicePreferences.microphone ? { deviceId: { exact: devicePreferences.microphone } } : {}) } });
      if (!streamReady()) throw new Error(t('尚未取得完整的攝影機與麥克風訊號，請檢查裝置後重試。'));
      streamDevices = selected; $('cameraPreview').srcObject = state.stream; await $('cameraPreview').play(); updateCaptureQuality(); updateCameraSwitch();
      for (const track of state.stream.getTracks()) track.addEventListener('ended', () => {
        if (state.recording && !state.stopping) { notify('recordNotice', t('錄製裝置已中斷，正在保存已有內容。'), 'error'); stopRecording(); }
        else if (!state.recording && !state.starting && !state.stopping) { releasePreview(); showCaptureState(); notify('deviceNotice', t('預覽裝置已中斷，請重新連接後點擊檢查裝置。'), 'error'); }
      });
      await refreshDeviceList().catch(() => {}); showCaptureState(); startMeter();
      notify('deviceNotice', feedback ? t('預覽已開啟，可檢查畫面與音量，準備好後點擊開始錄製。') : '', 'success', 4000);
      return state.stream;
    })().catch((error) => { releasePreview(); showCaptureState(); notify('deviceNotice', deviceError(error), 'error'); throw error; })
      .finally(() => { clearTimeout(hintTimer); previewBusy = false; previewPromise = null; lockRecordingFields(state.starting || state.recording || state.stopping); });
    return previewPromise;
  }
  async function checkDevices() {
    if (state.devicesBusy || previewBusy || state.recording || state.starting || state.stopping) return;
    state.devicesBusy = true; lockRecordingFields(true); $('refreshDevices').textContent = t('正在檢查…'); notify('deviceNotice', t('正在檢查攝影機與麥克風。若出現權限視窗，請選擇允許。'), 'busy');
    try {
      await refreshDeviceList(); await preparePreview();
      notify('deviceNotice', t('裝置清單已更新，預覽已開啟。'), 'success', 4000);
    } catch (error) { notify('deviceNotice', deviceError(error), 'error'); }
    finally { state.devicesBusy = false; lockRecordingFields(false); $('refreshDevices').textContent = t('檢查裝置'); }
  }
  const uploadCopy = {
    pause: ['暂停上传', '暫停上傳', 'Pause upload'], stop: ['中断上传', '中斷上傳', 'Stop upload'], resume: ['继续上传', '繼續上傳', 'Resume upload'],
    pausedState: ['已暂停', '已暫停', 'Paused'], stoppedState: ['已中断', '已中斷', 'Stopped'],
    youtubePausedState: ['YouTube 已暂停', 'YouTube 已暫停', 'YouTube paused'], youtubeStoppedState: ['YouTube 已中断', 'YouTube 已中斷', 'YouTube stopped'],
    pausing: ['正在暂停上传…', '正在暫停上傳…', 'Pausing upload…'], stopping: ['正在中断上传…', '正在中斷上傳…', 'Stopping upload…'],
    paused: ['上传已暂停，进度和原片已保留。', '上傳已暫停，進度和原片已保留。', 'Upload paused. Progress and the original are saved.'],
    stopped: ['这次上传已中断，原片仍保留在此设备。', '這次上傳已中斷，原片仍保留在此裝置。', 'This upload stopped. The original remains on this device.'],
    confirmStop: ['中断这次上传？原片会留在此设备，之后可以手动重新上传。', '中斷這次上傳？原片會留在此裝置，之後可以手動重新上傳。', 'Stop this upload? The original stays on this device, and you can upload it manually later.'],
    finishing: ['原片正在完成服务器确认，请稍后查看结果。', '原片正在完成伺服器確認，請稍後查看結果。', 'The server is finalizing this upload. Check the result shortly.'],
    alreadyDone: ['这段原片当前没有正在传输的上传。', '這段原片目前沒有正在傳輸的上傳。', 'This original is not currently uploading.'],
    saving: ['正在保存继续上传的选择…', '正在儲存繼續上傳的選擇…', 'Saving your choice to resume…'],
    saveFailed: ['无法保存继续上传的选择，请重试：', '無法儲存繼續上傳的選擇，請重試：', 'Could not save your choice to resume. Try again: ']
  };
  Object.assign(uploadCopy, {
    youtubePausing: ['正在暂停 YouTube 上传…', '正在暫停 YouTube 上傳…', 'Pausing YouTube upload…'],
    youtubeStopping: ['正在中断 YouTube 上传…', '正在中斷 YouTube 上傳…', 'Stopping YouTube upload…'],
    youtubePaused: ['YouTube 上传已暂停，进度已保留。', 'YouTube 上傳已暫停，進度已保留。', 'YouTube upload paused. Progress is saved.'],
    youtubeStopped: ['YouTube 上传已中断，服务器仍保留成片。', 'YouTube 上傳已中斷，伺服器仍保留成片。', 'YouTube upload stopped. The finished video remains on the server.'],
    youtubeStopConfirm: ['中断上传到 YouTube？会在当前传输片段结束后停下。若最后一段已完成，影片可能已进入 YouTube 处理；请查看最终状态。', '中斷上傳到 YouTube？會在目前傳輸片段結束後停下。若最後一段已完成，影片可能已進入 YouTube 處理；請查看最終狀態。', 'Stop uploading to YouTube? It will stop after the current chunk. If the final chunk already completed, YouTube may already be processing the video. Check the final status.'],
    youtubePauseTitle: ['暂停 YouTube 上传', '暫停 YouTube 上傳', 'Pause YouTube upload'],
    youtubeStopTitle: ['中断 YouTube 上传', '中斷 YouTube 上傳', 'Stop YouTube upload'],
    youtubeResume: ['继续上传 YouTube', '繼續上傳 YouTube', 'Resume YouTube upload']
  });
  function uploadMessage(key) { return uploadCopy[key][Math.max(0, ['zh-Hans', 'zh-Hant', 'en'].indexOf(locale.locale))]; }
  function uploadCard(item) { return [...$('jobsList').children].find((element) => element.dataset.jobId === item.job_id) || [...$('jobsList').children].find((element) => element.dataset.jobId === 'local-' + item.client_id); }
  async function pauseOrStopUpload(item, intent) {
    const control = state.uploadControls.get(item.client_id), card = uploadCard(item);
    if (!control || !state.uploading.has(item.client_id)) { if (card) jobNotice(card, uploadMessage('alreadyDone')); return; }
    if (control.finalizing) { if (card) jobNotice(card, uploadMessage('finishing'), 'busy'); return; }
    if (intent === 'stopped') {
      const accepted = await confirmation(uploadMessage('stop'), uploadMessage('confirmStop'), uploadMessage('stop'));
      if (!accepted) { const current = uploadCard(item); if (current) jobNotice(current, t('已取消，上传继续进行。')); return; }
    }
    if (!state.uploading.has(item.client_id) || control.finalizing) { const current = uploadCard(item); if (current) jobNotice(current, uploadMessage('finishing'), 'busy'); return; }
    control.intent = intent;
    const current = uploadCard(item);
    if (current) jobNotice(current, uploadMessage(intent === 'paused' ? 'pausing' : 'stopping'), 'busy');
    control.abort.abort(); control.xhr?.abort();
  }
  async function resumePausedUpload(item) {
    if (!item || state.uploading.has(item.client_id)) return;
    const card = uploadCard(item), previous = item.upload_decision;
    if (card) jobNotice(card, uploadMessage('saving'), 'busy');
    item.upload_decision = 'approved'; item.error = null;
    try { await savePending(item); }
    catch (error) { item.upload_decision = previous; if (card) jobNotice(card, uploadMessage('saveFailed') + messageOf(error), 'error'); return; }
    renderJobs(); await uploadPending(item);
  }
  function sendChunk(job, index, chunk, control) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest(); xhr.open('PUT', API + '/jobs/' + encodeURIComponent(job.id) + '/chunks/' + index);
      control.xhr = xhr;
      xhr.withCredentials = true; xhr.timeout = 180000; xhr.setRequestHeader('X-CSRF-Token', state.session.csrf_token);
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.upload.onprogress = (event) => {
        state.transfers.set(job.id, Math.min(.9999, ((job.received_bytes || 0) + event.loaded) / job.total_bytes));
        const card = [...$('jobsList').children].find((element) => element.dataset.jobId === job.id); if (card) updateJobHeader(card, job);
      };
      xhr.onload = () => {
        control.xhr = null;
        let body; try { body = JSON.parse(xhr.responseText); } catch (_) { reject(new Error(t('伺服器回應無法讀取，重試會先確認已接收的進度。'))); return; }
        if (xhr.status < 200 || xhr.status >= 300) { const error = new Error(errorText(body, xhr.status)); error.status = xhr.status; reject(error); }
        else resolve(body);
      };
      xhr.onerror = () => { control.xhr = null; reject(new Error(t('網路連線中斷，原片已保留，可以繼續上傳。'))); };
      xhr.ontimeout = () => { control.xhr = null; reject(new Error(t('原片傳送逾時，重試會先確認伺服器已接收的進度。'))); };
      xhr.onabort = () => { control.xhr = null; reject(new DOMException('Upload interrupted', 'AbortError')); };
      xhr.send(chunk);
    });
  }
  async function startRecording() {
    if (state.starting || state.recording || state.stopping) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { notify('recordNotice', t('此浏览器无法直接录制。请用最新版 Safari，或选择相册中已录好的视频。'), 'error'); return; }
    if (!state.session?.authenticated) { notify('recordNotice', t('请先登录后再录制。'), 'error'); return; }
    if (importing || !validateRecordingFields()) return;
    state.starting = true; lockRecordingFields(true); setRecordButton(t('正在開始錄製…'), true); notify('recordNotice', t('正在準備錄製…'), 'busy');
    $('dockStatus').textContent = t('正在準備錄製…'); $('dockTime').textContent = '00:00';
    state.writer = null; state.writeError = null; state.writeChain = Promise.resolve(); state.chunks = []; state.recordedBytes = 0; state.currentFile = fileName(); state.recordingPreferences = { ...preferences, processing_mode:processingMode, title: $('recordTopic').value, description: $('recordDescription').value };
    try {
      const mimeType = ['video/mp4;codecs=avc1.42E01E,mp4a.40.2', 'video/mp4', 'video/webm;codecs=vp8,opus', 'video/webm;codecs=vp9,opus', 'video/webm'].find((type) => MediaRecorder.isTypeSupported(type));
      if (!mimeType) throw new Error(t('无法直接录制此视频格式。请用 Safari，或选择相册中已录好的视频。'));
      state.currentFile = fileName(mimeType);
      captureId = crypto.randomUUID(); captureIndex = 0; captureError = null; captureChain = Promise.resolve();
      await dbAction('captures', 'readwrite', (store) => store.put({client_id:captureId, filename:state.currentFile, mimeType, created_at:new Date().toISOString(), ...state.recordingPreferences}));
      if (state.folder) { let permission = await state.folder.queryPermission({ mode: 'readwrite' }); if (permission !== 'granted') permission = await state.folder.requestPermission({ mode: 'readwrite' }); if (permission === 'granted') { const handle = await state.folder.getFileHandle(state.currentFile, { create: true }); state.writer = await handle.createWritable(); } else { $('storageFeedback').textContent = t('資料夾尚未獲准寫入，這次原片將使用瀏覽器下載。'); } }
      await preparePreview();
      state.recorder = new MediaRecorder(state.stream, { mimeType, videoBitsPerSecond: QUALITY_PRESETS[selectedQuality].bitrate, audioBitsPerSecond: 128000 });
      state.recorder.ondataavailable = (event) => { if (!event.data.size) return; const part = {client_id:captureId, index:captureIndex++, blob:event.data}; captureChain = captureChain.then(async () => { if (captureError) { state.chunks.push(event.data); return; } try { await dbAction('captureChunks', 'readwrite', (store) => store.put(part)); } catch(error) { captureError = error; state.chunks.push(event.data); notify('mobileNotice', t('设备储存空间不足，正在停止录制并保留已有内容。请下载原片或立即上传。'), 'error'); stopRecording(); } }); state.recordedBytes += event.data.size; updateRecordingBudget(); if (state.writer && !state.writeError) state.writeChain = state.writeChain.then(() => state.writer.write(event.data)).catch((error) => { state.writeError = error; notify('recordNotice', t('資料夾寫入中斷，錄製仍在繼續。結束後會改為下載完整原片。'), 'error'); }); const limit = state.session?.limits?.max_upload_bytes || 1024 ** 3; if (state.recordedBytes > limit - 8 * 1024 ** 2 && state.recording && !state.stopping) { notify('recordNotice', t('已接近單支影片大小限制，正在結束並保存這次錄製。'), 'busy'); stopRecording(); } };
      state.recorder.onerror = (event) => { notify('recordNotice', t('錄製中斷：') + (event.error?.message || t('攝影機訊號異常')) + t('。正在保存已錄製的內容。'), 'error'); if (state.recorder.state !== 'inactive') stopRecording(); };
      state.recorder.onstop = finishRecording;
      state.recorder.onstart = () => { recordOrientation = deviceOrientation(); state.recording = true; requestWakeLock(); state.starting = false; updateRecordingBudget(); state.startedAt = Date.now(); $('dockStatus').textContent = t('正在錄製'); document.title = 'Daigui Recorder · ' + t('錄製中') + ' · 00:00'; showCaptureState(); showRecordDock(true); $('recordBadge').textContent = t('正在錄製'); $('recordBadge').classList.add('recording'); $('recordTime').textContent = $('dockTime').textContent = '00:00'; setRecordButton(t('結束錄製'), false, true); $('recordHelp').textContent = t('正在錄下你的聲音與畫面，說完後點擊結束錄製。'); notify('deviceNotice', ''); notify('recordNotice', ''); if (isMobile) notify('mobileNotice', t('录制时请保持 Safari 在前台，勿锁屏或旋转手机。切到后台时会尝试停止并保存已有内容。')); state.timer = setInterval(() => { const elapsed = seconds((Date.now() - state.startedAt) / 1000); $('recordTime').textContent = $('dockTime').textContent = elapsed; document.title = 'Daigui Recorder · ' + t('錄製中') + ' · ' + elapsed; }, 250); startMeter(); };
      state.recorder.start(1000);
    } catch (error) {
      cleanupStream(); if (state.writer) { try { await state.writer.abort(); } catch (_) {} } state.writer = null; state.starting = false; lockRecordingFields(false); setRecordButton(t('開始錄製'), false);
      notify('recordNotice', deviceError(error), 'error');
    }
  }
  function startMeter() {
    if (state.audio) { state.audio.resume().catch(() => {}); return; }
    try {
      state.audio = new (window.AudioContext || window.webkitAudioContext)();
      state.audio.onstatechange = () => { $('enableMeter').hidden = state.audio?.state !== 'suspended'; };
      $('enableMeter').hidden = state.audio.state !== 'suspended';
      const source = state.audio.createMediaStreamSource(state.stream); state.analyser = state.audio.createAnalyser(); state.analyser.fftSize = 256; source.connect(state.analyser);
      const data = new Uint8Array(state.analyser.fftSize);
      const tick = () => { if (!state.stream || !state.audio) return; state.analyser.getByteTimeDomainData(data); let sum = 0; for (const x of data) sum += ((x - 128) / 128) ** 2; $('audioLevel').style.width = Math.min(100, Math.sqrt(sum / data.length) * 420) + '%'; state.animation = requestAnimationFrame(tick); };
      state.audio.resume().catch(() => {}); tick();
    } catch (_) { $('audioLevel').style.width = '0'; }
  }
  async function enableMeter() {
    $('enableMeter').disabled = true; $('enableMeter').textContent = t('正在啟用…');
    try { await state.audio?.resume(); if (state.audio?.state !== 'running') throw new Error(t('音量顯示未能啟用，請檢查麥克風後重試。')); notify('deviceNotice', t('音量顯示已啟用。'), 'success', 4000); }
    catch (error) { notify('deviceNotice', messageOf(error), 'error'); }
    finally { $('enableMeter').disabled = false; $('enableMeter').textContent = t('啟用音量顯示'); $('enableMeter').hidden = state.audio?.state !== 'suspended'; }
  }
  function cleanupStream() { releaseWakeLock(); document.title = pageTitle; clearInterval(state.timer); if (!streamReady()) releasePreview(); showRecordDock(false); showCaptureState(); if (streamReady()) notify('deviceNotice', ''); }
  function stopRecording() { if (!state.recorder || state.stopping || state.recorder.state === 'inactive') return; state.stopping = true; setRecordButton(t('正在保存原片…'), true, true); notify('recordNotice', t('正在結束錄製並保存最後一段影音…'), 'busy'); state.recorder.stop(); }
  function downloadBlob(blob, filename) { const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000); }
  async function finishRecording() {
    state.recording = false; state.stopping = true; cleanupStream(); await captureChain; let parts = []; try { parts = await dbAction('captureChunks', 'readonly', (store) => store.getAll(IDBKeyRange.bound([captureId, 0], [captureId, Number.MAX_SAFE_INTEGER]))); } catch(error) { state.stopping=false; lockRecordingFields(false); setRecordButton(t('開始錄製'),false); notify('recordNotice',t('无法读取这次录制的恢复副本。请保留此页面，重新打开后恢复并下载检查原片。'),'error'); return; } const blob = new Blob([...parts.map(part => part.blob), ...state.chunks], { type: state.recorder.mimeType }); parts = []; state.chunks = []; const recordingPrefs = state.recordingPreferences; let saved = false;
    try { await state.writeChain; if (state.writer && !state.writeError) { await state.writer.close(); saved = true; } else if (state.writer) { try { await state.writer.abort(); } catch (_) {} } } catch (error) { state.writeError = error; }
    state.writer = null;
    if (!blob.size) { state.stopping = false; lockRecordingFields(false); setRecordButton(t('開始錄製'), false); notify('recordNotice', t('這次沒有取得可保存的影音內容，請檢查裝置後重新錄製。'), 'error'); return; }
    const item = { client_id: captureId || crypto.randomUUID(), filename: state.currentFile, total_bytes: blob.size, blob, created_at: new Date().toISOString(), title: recordingPrefs.title, description: recordingPrefs.description, privacy: recordingPrefs.privacy, auto_publish: recordingPrefs.auto_publish, processing_mode:recordingPrefs.processing_mode, retain_original:isMobile || localMode(), job_id: null, upload_decision: 'pending' };
    state.pending.set(item.client_id, item); state.lastPending = item.client_id;
    let recovery = true; try { await savePending(item); await clearCapture(item.client_id); } catch (_) { recovery = false; }
    if (!saved && !isMobile) downloadBlob(blob, item.filename);
    state.stopping = false; lockRecordingFields(false); setRecordButton(t('開始錄製'), false); $('recordHelp').textContent = localMode() ? t('正在准备这段录制。') : t('請選擇是否上傳這段錄製。');
    notify('recordNotice', (isMobile ? t('原片已保留在此页面，可在录制列表下载。上传完成前请保持页面打开。') : saved ? t('原片已保存到「') + state.folder.name + t('」。') : t('原片下載已開始，請確認瀏覽器的下載結果。')) + (localMode() ? t(' 视频会按本次设置准备上传。') : t('\n請選擇是否上傳到伺服器。')) + (!recovery ? t('\n瀏覽器未能保留恢復副本，請確認下載的原片已保存。') : ''));
    $('localRetryActions').hidden = true; renderJobs(); chooseUpload(item);
  }
  async function uploadPending(item) {
    if (!uploadApproved(item)) { notify('recordNotice', t('這段尚未確認上傳。請在錄製列表選擇是否上傳伺服器。')); return; }
    if (state.uploading.has(item.client_id)) { notify('recordNotice', t('這段原片正在上傳，請稍候。'), 'busy'); return; }
    const control = { intent: null, abort: new AbortController(), xhr: null, finalizing: false };
    state.uploadControls.set(item.client_id, control);
    state.lastPending = item.client_id; state.uploading.add(item.client_id); requestWakeLock(); $('retryUpload').disabled = true;
    item.error = null; renderJobs();
    try {
      if (!state.session?.authenticated) throw new Error(t('請重新連結 Google 帳戶後重試上傳。原片仍保留在此设备与浏览器中。'));
      if (item.total_bytes > state.session.limits.max_upload_bytes) throw new Error(t('原片超過目前單支影片上限（') + bytes(state.session.limits.max_upload_bytes) + t('）。原片仍可下載，請縮短錄製後重新製作。'));
      let job = item.job_id ? await api('/jobs/' + encodeURIComponent(item.job_id), { signal: control.abort.signal }) : await api('/jobs', { method: 'POST', signal: control.abort.signal, json: { client_id: item.client_id, filename: item.filename, total_bytes: item.total_bytes, title: item.title, description: item.description, privacy: item.privacy, auto_publish: item.auto_publish, processing_mode:item.processing_mode || processingMode, retain_original:!!item.retain_original } });
      if (control.intent) throw new DOMException('Upload interrupted', 'AbortError');
      job = job.job || job; item.job_id = job.id; try { await savePending(item); } catch (_) {} updateJob(job);
      if (job.state === 'receiving') {
        const chunkSize = state.session.limits.chunk_bytes; let index = job.next_chunk || 0; const count = Math.ceil(item.total_bytes / chunkSize);
        while (index < count) { if (control.intent) throw new DOMException('Upload interrupted', 'AbortError'); const chunk = item.blob.slice(index * chunkSize, Math.min((index + 1) * chunkSize, item.total_bytes)); const result = await sendChunk(job, index, chunk, control); if (control.intent) throw new DOMException('Upload interrupted', 'AbortError'); index = result.next_chunk ?? index + 1; job = { ...job, received_bytes: result.received_bytes, next_chunk: index, progress: result.received_bytes / item.total_bytes, server_upload_progress: result.received_bytes / item.total_bytes, message: t('原片已傳送 ') + bytes(result.received_bytes) + ' / ' + bytes(item.total_bytes) }; updateJob(job); }
        state.transfers.delete(job.id);
        control.finalizing = true; renderJobs();
        job = await api('/jobs/' + encodeURIComponent(job.id) + '/complete', { method: 'POST', json: {} }); job = job.job || job; updateJob(job);
      }
      state.pending.delete(item.client_id); try { await dbAction('pending', 'readwrite', (store) => store.delete(item.client_id)); } catch (_) {}
      if (state.lastPending === item.client_id) { const activeRecording = state.recording || state.starting || state.stopping; notify('recordNotice', (localMode() ? t('原片已保存到本机队列，可关闭此页面；上传期间请保持电脑运行。') : t('原片已傳送完成，伺服器將繼續製作影片。')) + (activeRecording ? t('目前的新錄製仍在進行，請保持視窗開啟。') : state.uploading.size > 1 ? t('其他原片仍在傳送，請保持視窗開啟。') : t('現在可以關閉此視窗。'))); $('localRetryActions').hidden = true; }
      await refreshJobs(false);
    } catch (error) {
      if (control.intent) { item.upload_decision = control.intent; item.error = null; }
      else item.error = messageOf(error);
      state.pending.set(item.client_id, item);
      let saved = true; try { await savePending(item); } catch (_) { saved = false; }
      state.lastPending = item.client_id;
      if (control.intent) notify('recordNotice', uploadMessage(control.intent) + (saved ? '' : t(' 无法保存此状态，重新打开页面前请确认原片已下载。')), saved ? 'success' : 'error');
      else notify('recordNotice', t('原片尚未傳送完成：') + messageOf(error), 'error');
      $('localRetryActions').hidden = !!control.intent; renderJobs();
    }
    finally { state.uploading.delete(item.client_id); state.uploadControls.delete(item.client_id); if (!state.uploading.size && !state.recording) releaseWakeLock(); if (item.job_id) state.transfers.delete(item.job_id); $('retryUpload').disabled = false; renderJobs(); if (control.intent) { const card = uploadCard(item); if (card) jobNotice(card, uploadMessage(control.intent), 'success'); } }
  }
  function updateJob(job) { if (!job?.id) return; const index = state.jobs.findIndex((x) => x.id === job.id); if (index < 0) state.jobs.unshift(job); else state.jobs[index] = job; renderJobs(); }
  function node(tag, cls, text) { const el = document.createElement(tag); if (cls) el.className = cls; if (text !== undefined) el.textContent = text; return el; }
  function action(label, onClick, secondary = true) { const button = node('button', 'button small' + (secondary ? ' secondary' : ''), label); button.type = 'button'; button.addEventListener('click', onClick); return button; }
  function jobNotice(card, message, type = '') { statusNotice(card.querySelector('.job-notice'), message, type); }
  function safeYoutubeURL(value) { try { const url = new URL(value); return url.protocol === 'https:' && ['www.youtube.com', 'youtube.com', 'youtu.be'].includes(url.hostname) ? url.href : null; } catch (_) { return null; } }
  function mediaUrl(job, kind) { return API + '/jobs/' + encodeURIComponent(job.id) + '/media?kind=' + kind; }
  async function controlYoutubeUpload(card, id, intent) {
    if (intent === 'stop') {
      const accepted = await confirmation(uploadMessage('youtubeStopTitle'), uploadMessage('youtubeStopConfirm'), uploadMessage('youtubeStopTitle'));
      if (!accepted) { jobNotice(card, t('已取消，上传继续进行。')); return; }
    }
    card.dataset.busy = 'true'; card.querySelectorAll('button').forEach((button) => button.disabled = true);
    jobNotice(card, uploadMessage(intent === 'pause' ? 'youtubePausing' : 'youtubeStopping'), 'busy');
    try {
      const updated = await api('/jobs/' + encodeURIComponent(id) + '/upload-control', { method: 'POST', json: { action: intent } });
      card.dataset.busy = 'false'; updateJob(updated.job || updated);
      const current = [...$('jobsList').children].find((element) => element.dataset.jobId === id);
      if (current) jobNotice(current, uploadMessage((updated.job || updated).state === 'youtube_paused' ? 'youtubePaused' : (updated.job || updated).state === 'youtube_stopped' ? 'youtubeStopped' : intent === 'pause' ? 'youtubePausing' : 'youtubeStopping'), ['youtube_paused', 'youtube_stopped'].includes((updated.job || updated).state) ? 'success' : 'busy');
    } catch (error) { jobNotice(card, messageOf(error), 'error'); }
    finally { card.dataset.busy = 'false'; card.querySelectorAll('button').forEach((button) => button.disabled = false); }
  }
  function renderJobs() {
    const container = $('jobsList');
    const items = state.jobs.map((job) => { const pending = [...state.pending.values()].find((item) => item.job_id === job.id); return pending?.error ? { ...job, error: pending.error } : job; });
    for (const pending of state.pending.values()) if (!items.some((job) => job.id === pending.job_id)) items.unshift({ ...pending, id: 'local-' + pending.client_id, state: ['paused', 'stopped'].includes(pending.upload_decision) ? pending.upload_decision : uploadApproved(pending) ? 'local_pending' : pending.upload_decision === 'local_only' ? 'local_only' : 'local_decision', message: pending.error || (['paused', 'stopped'].includes(pending.upload_decision) ? uploadMessage(pending.upload_decision) : uploadApproved(pending) ? t('原片已保留在此裝置，等待傳送至伺服器。') : pending.upload_decision === 'local_only' ? t('這段只保留在此设备，未上傳伺服器。') : t('原片已保留在本機，請選擇是否上傳伺服器。')), received_bytes: 0 });
    const timestamp = (job) => { const value = typeof job.created_at === 'number' ? job.created_at * 1000 : Date.parse(job.created_at); return Number.isFinite(value) ? value : 0; };
    items.sort((a, b) => timestamp(b) - timestamp(a) || String(a.id).localeCompare(String(b.id)));
    if (!items.length) { container.replaceChildren(); const empty = node('div', 'empty-state'); empty.append(node('span', '', '↗')); const body = node('div'); body.append(node('h3', '', t('第一段記錄，從現在開始。')), node('p', '', state.session?.authenticated ? t('錄製結束後，可以在這裡查看製作進度、編輯文案與開啟影片。') : t('連結 Google 帳戶後，即可查看你的錄製與發布進度。'))); empty.append(body); container.append(empty); return; }
    container.querySelector('.empty-state')?.remove(); const ids = new Set(items.map((job) => job.id)); container.querySelectorAll('.job-card').forEach((card) => { if (!ids.has(card.dataset.jobId)) card.remove(); });
    items.forEach((job, index) => {
      let card = Array.from(container.children).find((el) => el.dataset.jobId === job.id);
      const pending = [...state.pending.values()].find((item) => item.job_id === job.id || ('local-' + item.client_id) === job.id);
      const control = pending && state.uploadControls.get(pending.client_id);
      const signature = JSON.stringify(job) + '|' + (pending?.upload_decision || '') + '|' + (pending && state.uploading.has(pending.client_id)) + '|' + !!control?.finalizing;
      if (card) { if (container.children[index] !== card) container.insertBefore(card, container.children[index] || null); card.querySelector('.job-count').textContent = String(items.length - index).padStart(2, '0'); }
      if (card && (state.editing.has(job.id) || card.dataset.busy === 'true' || card.dataset.signature === signature)) { updateJobHeader(card, job); return; }
      const newCard = node('article', 'job-card'); newCard.dataset.jobId = job.id; newCard.dataset.signature = signature;
      const top = node('div', 'job-top'); top.append(node('span', 'job-count', String(items.length - index).padStart(2, '0'))); const info = node('div', 'job-info'); info.append(node('h3', 'job-title', job.title || job.filename || t('新的錄製'))); const date = new Date(typeof job.created_at === 'number' ? job.created_at * 1000 : job.created_at); info.append(node('p', 'job-date', (Number.isNaN(date.getTime()) ? '' : date.toLocaleString(locale.locale, { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' })) + ' · ' + (privacyNames[job.actual_privacy || job.privacy] || t('私人')))); top.append(info); const stat = node('span', 'job-state'); stat.append(node('i'), node('span', 'job-state-text')); top.append(stat); newCard.append(top); const progress = node('div', 'job-progress'); progress.setAttribute('role', 'progressbar'); progress.setAttribute('aria-label', t('影片處理進度')); progress.setAttribute('aria-valuemin', '0'); progress.setAttribute('aria-valuemax', '100'); progress.append(node('span')); newCard.append(progress, transferRows(), node('p', 'job-message'), node('p', 'job-cleanup')); const actions = node('div', 'job-actions');
      if (job.final_ready || job.transcript || job.state === 'ready') { const button = action(job.cleanup_state === 'complete' ? t('查看文案') : job.video_id || job.publication_locked ? t('預覽成片與文案') : t('預覽與編輯文案'), () => state.editing.has(job.id) ? closeEditor(newCard, job.id) : openEditor(newCard, job.id)); button.classList.add('editor-toggle'); button.dataset.closedLabel = button.textContent; button.setAttribute('aria-expanded', 'false'); button.setAttribute('aria-controls', 'editor-' + job.id); actions.append(button); }
      if (job.original_ready) { const link = node('a', 'button small secondary', t('下載原片')); link.href = mediaUrl(job, 'original') + '&download=1'; link.download = job.filename || ''; link.addEventListener('click', () => jobNotice(newCard, t('原片下載已開始，請查看瀏覽器的下載結果。'))); actions.append(link); }
      if (job.final_ready && ['ready', 'failed'].includes(job.state) && !job.video_id && !job.publication_locked) actions.append(action(t('上傳 YouTube'), () => publishJob(newCard, job.id), false));
      if (job.video_url && safeYoutubeURL(job.video_url)) {
        const url = safeYoutubeURL(job.video_url);
        const link = node('a', 'button small', job.state === 'published' ? t('觀看影片 ↗') : t('在 YouTube 開啟 ↗')); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; actions.append(link);
        const copy = action(t('複製影片連結'), async () => {
          copy.disabled = true; newCard.dataset.busy = 'true'; jobNotice(newCard, t('正在複製連結…'), 'busy');
          try { await navigator.clipboard.writeText(url); jobNotice(newCard, t('影片連結已複製。'), 'success'); }
          catch (_) { jobNotice(newCard, url + '\n' + t('無法存取剪貼簿，請選取上方連結手動複製，或點擊重試。'), 'error'); }
          finally { copy.disabled = false; newCard.dataset.busy = 'false'; }
        }); copy.classList.add('copy-video-link'); copy.setAttribute('aria-label', t('複製影片連結')); copy.title = t('複製影片連結'); copy.style.cssText = 'overflow-wrap:anywhere;white-space:normal;text-align:left;max-width:100%'; actions.append(copy);
      }
      if (job.cleanup_state === 'failed') actions.append(action(t('重試清理伺服器副本'), async () => { if (await confirmation(t('清理這支影片的伺服器副本？'), t('會先重新確認 YouTube 已處理成功，再清理此錄製的伺服器影音檔案。電腦原片與 YouTube 影片仍保留。'), t('確認清理'))) retryJob(newCard, job.id); else jobNotice(newCard, t('已取消清理。')); }));
      if (['local_pending', 'local_only', 'local_decision', 'paused', 'stopped'].includes(job.state)) { if (!state.uploading.has(job.client_id)) actions.append(action(['paused', 'stopped'].includes(job.state) ? uploadMessage('resume') : job.state === 'local_pending' ? t('繼續上傳原片') : job.state === 'local_only' ? t('上傳這段') : t('選擇是否上傳'), () => ['paused', 'stopped'].includes(job.state) ? resumePausedUpload(state.pending.get(job.client_id)) : chooseUpload(state.pending.get(job.client_id)), false)); actions.append(action(t('下載本機原片'), () => { downloadBlob(job.blob, job.filename); jobNotice(newCard, t('原片下載已開始，請查看瀏覽器的下載結果。')); })); }
      if (['queued', 'publish_queued', 'processing', 'publishing', 'youtube_processing'].includes(job.state) && !job.publication_locked) actions.append(action(job.state === 'queued' ? t('嘗試啟動剪輯') : job.state === 'publish_queued' ? t('嘗試啟動上傳') : t('檢查並恢復處理'), () => retryJob(newCard, job.id), false));
      if ((job.state === 'failed' || job.metadata_required) && !job.publication_locked) actions.append(action(job.metadata_required ? t('重新產生文案') : t('重試處理'), () => retryJob(newCard, job.id), true));
      if (job.state === 'receiving' && pending && !state.uploading.has(pending.client_id)) actions.append(action(['paused', 'stopped'].includes(pending.upload_decision) ? uploadMessage('resume') : t('繼續上傳原片'), () => ['paused', 'stopped'].includes(pending.upload_decision) ? resumePausedUpload(pending) : uploadPending(pending), false));
      if (pending && state.uploading.has(pending.client_id) && !control?.finalizing) { actions.append(action(uploadMessage('pause'), () => pauseOrStopUpload(pending, 'paused'))); actions.append(action(uploadMessage('stop'), () => pauseOrStopUpload(pending, 'stopped'))); }
      if (['publish_queued', 'publishing'].includes(job.state) && !job.publication_locked) { actions.append(action(uploadMessage('youtubePauseTitle'), () => controlYoutubeUpload(newCard, job.id, 'pause'))); actions.append(action(uploadMessage('youtubeStopTitle'), () => controlYoutubeUpload(newCard, job.id, 'stop'))); }
      if (['youtube_paused', 'youtube_stopped'].includes(job.state) && !job.publication_locked) actions.append(action(uploadMessage('youtubeResume'), () => retryJob(newCard, job.id), false));
      newCard.append(actions); const notice = node('div', 'notice job-notice'); notice.hidden = true; notice.setAttribute('role', 'status'); notice.setAttribute('aria-live', 'polite'); newCard.append(notice); updateJobHeader(newCard, job);
      if (card) { const previous = card.querySelector('.job-notice'); if (previous && !previous.hidden) statusNotice(notice, previous.textContent, previous.classList.contains('error') ? 'error' : previous.classList.contains('busy') ? 'busy' : ''); card.replaceWith(newCard); } else container.insertBefore(newCard, container.children[index] || null);
    });
  }
  function transferRows() {
    const rows = node('div', 'job-transfers');
    for (const [kind, text] of [['server', t('上傳伺服器')], ['youtube', t('上傳 YouTube')]]) {
      const transfer = node('div', 'transfer ' + kind + '-transfer'); const label = node('div', 'transfer-label');
      label.append(node('span', '', text), node('span', 'transfer-value', '0%'));
      const bar = node('progress'); bar.max = 1; bar.value = 0; bar.setAttribute('aria-label', text);
      transfer.append(label, bar); rows.append(transfer);
    }
    return rows;
  }
  function updateJobHeader(card, job) {
    const pending = [...state.pending.values()].find((item) => item.job_id === job.id || ('local-' + item.client_id) === job.id);
    const interrupted = job.state === 'receiving' && ['paused', 'stopped'].includes(pending?.upload_decision) && !state.uploading.has(pending.client_id);
    card.querySelector('.job-title').textContent = job.title || job.filename || t('新的錄製');
    card.querySelector('.job-state-text').textContent = interrupted || ['paused', 'stopped'].includes(job.state) ? uploadMessage((interrupted ? pending.upload_decision : job.state) + 'State') : job.state === 'youtube_paused' ? uploadMessage('youtubePausedState') : job.state === 'youtube_stopped' ? uploadMessage('youtubeStoppedState') : job.state === 'upload_pausing' ? uploadMessage('youtubePausing') : job.state === 'upload_stopping' ? uploadMessage('youtubeStopping') : stateNames[job.state] || (job.state === 'local_pending' ? t('等待傳送原片') : t('正在更新狀態'));
    const progress = Math.max(0, Math.min(1, Number(job.progress) || 0));
    card.querySelector('.job-transfers').hidden = ['local_only', 'local_decision'].includes(job.state);
    card.querySelector('.job-progress').hidden = job.state !== 'processing';
    card.querySelector('.job-progress').setAttribute('aria-valuenow', String(Math.round(progress * 100)));
    card.querySelector('.job-progress>span').style.width = progress * 100 + '%';
    const server = state.transfers.get(job.id) ?? job.server_upload_progress ?? ((job.received_bytes || 0) / Math.max(1, job.total_bytes || 1));
    const youtube = job.youtube_upload_progress ?? (job.video_id ? 1 : job.state === 'publishing' ? progress : 0);
    for (const [kind, value] of [['server', server], ['youtube', youtube]]) {
      const fraction = Math.max(0, Math.min(1, value));
      card.querySelector('.' + kind + '-transfer progress').value = fraction;
      card.querySelector('.' + kind + '-transfer .transfer-value').textContent = Math.floor(fraction * 100) + '%';
    }
    card.querySelector('.job-message').textContent = interrupted ? uploadMessage(pending.upload_decision) : job.state === 'youtube_paused' ? uploadMessage('youtubePaused') : job.state === 'youtube_stopped' ? uploadMessage('youtubeStopped') : job.state === 'upload_pausing' ? uploadMessage('youtubePausing') : job.state === 'upload_stopping' ? uploadMessage('youtubeStopping') : job.worker_recovering && ACTIVE_STATES.has(job.state) ? t('背景處理異常，正在嘗試恢復；你也可以點擊下方按鈕重試。') : t(job.error || job.message || '');
    card.querySelector('.job-message').style.color = job.error ? '#9a392f' : '';
    const cleanup = card.querySelector('.job-cleanup');
    cleanup.textContent = job.cleanup_state === 'complete' ? t('伺服器影片已清理，YouTube 影片與文字記錄仍保留。') : t(job.cleanup_error) || (job.cleanup_state === 'running' || job.cleanup_state === 'pending' ? t('正在確認並清理伺服器副本…') : '');
    cleanup.style.color = job.cleanup_error ? '#9a392f' : '';
  }
  async function refreshJobs(manual = false) { if (manual) { $('refreshJobs').disabled = true; $('refreshJobs').textContent = t('正在更新…'); notify('jobsNotice', t('正在取得最新進度…'), 'busy'); } try { if (!state.session?.authenticated) { if (manual) notify('jobsNotice', t('請先連結 Google 帳戶，即可查看錄製。')); return; } const result = await api('/jobs'); state.jobs = result.jobs || []; renderJobs(); if (manual) notify('jobsNotice', t('錄製進度已更新。')); } catch (error) { if (error.status === 401) { try { await loadSession(); state.jobs = []; renderJobs(); if (!state.session.auth_error) notify('authNotice', t('登入已到期，請重新連結 Google 帳戶。原片仍會保留。'), 'error'); } catch (_) { notify('authNotice', t('登入狀態無法確認，請重新整理頁面後連結 Google 帳戶。'), 'error'); } } else notify('jobsNotice', messageOf(error), 'error'); } finally { if (manual) { $('refreshJobs').disabled = false; $('refreshJobs').textContent = t('重新整理'); } } }
  function makeField(label, type, value, id) { const wrap = node('div', 'field'); const labelEl = node('label', '', label); labelEl.htmlFor = id; const input = node(type === 'textarea' ? 'textarea' : type === 'select' ? 'select' : 'input'); input.id = id; if (type === 'text') input.type = 'text'; if (type === 'select') for (const [key, text] of Object.entries(privacyNames)) { const opt = node('option', '', text); opt.value = key; input.append(opt); } input.value = value || ''; const err = node('p', 'field-error'); err.id = id + '-error'; err.hidden = true; input.setAttribute('aria-describedby', err.id); wrap.append(labelEl, input, err); return { wrap, input, error: err }; }
  function closeEditor(card, id) {
    if (card.dataset.busy === 'true') { jobNotice(card, t('正在保存文案，完成後即可收起。'), 'busy'); return; }
    const editor = card.querySelector('.job-editor'); if (!editor) return;
    const job = state.jobs.find((x) => x.id === id);
    const draft = { title: $('title-' + id).value, description: $('description-' + id).value, privacy: $('privacy-' + id).value };
    const unsaved = !job.video_id && !job.publication_locked && Object.keys(draft).some((key) => draft[key] !== (job[key] || ''));
    if (unsaved) state.drafts.set(id, draft); else state.drafts.delete(id);
    editor.querySelector('video')?.pause(); editor.remove(); state.editing.delete(id);
    const button = card.querySelector('.editor-toggle'); button.textContent = button.dataset.closedLabel; button.setAttribute('aria-expanded', 'false');
    jobNotice(card, unsaved ? t('已收起文案。未保存的修改暫留在本頁，再次展開可繼續編輯；重新載入頁面前請先保存。') : t('已收起文案。')); renderJobs();
  }
  function openEditor(card, id) {
    if (state.editing.has(id)) { jobNotice(card, t('文案編輯區已開啟。')); return; }
    const job = state.jobs.find((x) => x.id === id); const readOnly = !!job.video_id || !!job.publication_locked; const values = !readOnly && state.drafts.get(id) || job;
    state.editing.add(id); const toggle = card.querySelector('.editor-toggle'); toggle.textContent = t('收起文案'); toggle.setAttribute('aria-expanded', 'true');
    const editor = node('form', 'job-editor'); editor.id = 'editor-' + id; editor.noValidate = true; editor.append(node('h4', '', t('成片與發布文案'))); if (job.final_ready) { const video = node('video'); video.controls = true; video.preload = 'metadata'; video.src = mediaUrl(job, 'final'); video.addEventListener('error', () => jobNotice(card, t('影片暫時無法播放，請重新整理後再試。'), 'error')); editor.append(video); }
    const title = makeField(t('影片標題'), 'text', values.title, 'title-' + id); title.input.maxLength = 100; const description = makeField(t('影片描述'), 'textarea', values.description, 'description-' + id); description.input.maxLength = 5000; const privacy = makeField(t('可見範圍'), 'select', values.privacy, 'privacy-' + id); editor.append(title.wrap, description.wrap, privacy.wrap, node('p', 'field-help', t('填寫的內容直接使用，留空的欄位會依口播內容補齊。'))); const save = action(t('保存文案'), () => {}, false); save.type = 'submit'; const actions = node('div', 'job-actions'); actions.append(save); editor.append(actions);
    if (readOnly) { title.input.readOnly = description.input.readOnly = true; privacy.input.disabled = true; save.hidden = true; editor.append(node('p', 'field-help', t('已開始上傳的影片，請到 YouTube 工作室查看或修改。'))); }
    if (job.transcript) { const details = node('details', 'transcript'); details.append(node('summary', '', t('查看逐字稿')), node('p', '', job.transcript)); editor.append(details); }
    editor.addEventListener('submit', async (event) => { event.preventDefault(); title.error.hidden = description.error.hidden = privacy.error.hidden = true; [title, description, privacy].forEach((field) => field.input.removeAttribute('aria-invalid')); let first = null; const invalid = (field, text) => { field.error.textContent = text; field.error.hidden = false; field.input.setAttribute('aria-invalid', 'true'); first ||= field.input; }; if (title.input.value.length > 100) invalid(title, t('標題最多 100 個字元。')); if (new TextEncoder().encode(description.input.value).length > 5000) invalid(description, t('簡介最多 5,000 位元組（中文約 1,600 字）。')); if (privacy.input.value === 'public' && !state.session.public_enabled) invalid(privacy, t('公開發布尚未啟用，請先選擇私人或不公開。')); if (first) { first.scrollIntoView({ behavior: 'smooth', block: 'center' }); first.focus({ preventScroll: true }); jobNotice(card, t('請完成標示的欄位後再保存。'), 'error'); return; }
      save.disabled = toggle.disabled = true; save.textContent = t('正在保存…'); card.dataset.busy = 'true'; jobNotice(card, t('正在保存文案…'), 'busy');
      try { let updated = await api('/jobs/' + encodeURIComponent(id), { method: 'PATCH', json: { title: title.input.value.trim(), description: description.input.value.trim(), privacy: privacy.input.value } }); updated = updated.job || updated; state.drafts.delete(id); updateJob(updated); jobNotice(card, t('文案與可見範圍已保存。')); } catch (error) { jobNotice(card, messageOf(error), 'error'); } finally { save.disabled = toggle.disabled = false; save.textContent = t('保存文案'); card.dataset.busy = 'false'; }
    }); card.append(editor); jobNotice(card, readOnly ? t('已開啟成片與文案預覽。發布後的影片請在 YouTube 工作室管理。') : t('可以預覽成片並編輯文案，完成後點擊保存。'));
  }
  async function publishJob(card, id) {
    const job = state.jobs.find((x) => x.id === id);
    if (!job.title?.trim()) { openEditor(card, id); const input = $('title-' + id); input.focus(); input.scrollIntoView({ behavior: 'smooth', block: 'center' }); jobNotice(card, t('請先輸入並保存影片標題，再上傳 YouTube。'), 'error'); return; }
    if (job.privacy === 'public' && !state.session.public_enabled) { jobNotice(card, t('公開發布尚未啟用。請在文案編輯區選擇私人或不公開，或稍後再試。'), 'error'); return; }
    if (!await confirmation(t('上傳這支影片？'), t('「') + job.title + t('」將上傳到 ') + (state.session.user?.channel_title || t('目前連結的頻道')) + t('，可見範圍為「') + privacyNames[job.privacy] + t('」。') + (job.privacy === 'public' ? t('\n所有人都可能觀看這支影片。') : ''), t('開始上傳'))) { jobNotice(card, t('已取消上傳。')); return; }
    card.dataset.busy = 'true'; card.querySelectorAll('button').forEach((b) => b.disabled = true); jobNotice(card, t('正在安排 YouTube 上傳…'), 'busy');
    try { let updated = await api('/jobs/' + encodeURIComponent(id) + '/publish', { method: 'POST', json: {} }); updated = updated.job || updated; state.editing.delete(id); card.dataset.busy = 'false'; updateJob(updated); notify('jobsNotice', t('影片已加入 YouTube 上傳佇列，進度會自動更新。')); } catch (error) { jobNotice(card, messageOf(error), 'error'); } finally { card.dataset.busy = 'false'; card.querySelectorAll('button').forEach((b) => b.disabled = false); }
  }
  async function retryJob(card, id) { card.dataset.busy = 'true'; card.querySelectorAll('button').forEach((b) => b.disabled = true); jobNotice(card, t('正在檢查並恢復處理…'), 'busy'); try { let job = await api('/jobs/' + encodeURIComponent(id) + '/retry', { method: 'POST', json: {} }); job = job.job || job; card.dataset.busy = 'false'; state.editing.delete(id); updateJob(job); const currentCard = [...$('jobsList').children].find((el) => el.dataset.jobId === id); if (currentCard) jobNotice(currentCard, t(job.retry_message || '已重新安排處理，進度會自動更新。'), 'success'); } catch (error) { jobNotice(card, messageOf(error), 'error'); } finally { card.dataset.busy = 'false'; card.querySelectorAll('button').forEach((b) => b.disabled = false); } }
  async function restoreLocal() { try { state.folder = await dbAction('settings', 'readonly', (store) => store.get('folder')); if (state.folder) { $('storageLabel').textContent = t('本機儲存：') + state.folder.name; const granted = await state.folder.queryPermission({ mode: 'readwrite' }) === 'granted'; $('storageFeedback').textContent = granted ? t('已沿用你選擇的儲存資料夾。') : t('下次開始錄製時，會再次確認資料夾的寫入權限。'); } const pending = await dbAction('pending', 'readonly', (store) => store.getAll()); for (const item of pending) state.pending.set(item.client_id, item); if (pending.length) { state.lastPending = pending[pending.length - 1].client_id; const approved = pending.filter(uploadApproved).length; if (approved) notify('recordNotice', t('找到 ') + approved + t(' 段已確認上傳的原片，連接可用時會繼續傳送。'), 'busy'); $('localRetryActions').hidden = approved === 0; } } catch (_) { $('storageFeedback').textContent = t('瀏覽器未提供本機恢復儲存，請保持視窗開啟直到原片上傳完成。'); } }

  function deviceOrientation() {
    if (screen.orientation?.type) return screen.orientation.type.startsWith('portrait') ? 'portrait' : 'landscape';
    if (typeof window.orientation === 'number') return Math.abs(window.orientation) === 90 ? 'landscape' : 'portrait';
    return innerHeight >= innerWidth ? 'portrait' : 'landscape';
  }
  function captureDimensions() {
    const preset = QUALITY_PRESETS[selectedQuality];
    const portrait = orientation === 'portrait' || (orientation === 'auto' && isMobile && deviceOrientation() === 'portrait');
    return portrait ? {width:preset.height, height:preset.width} : {width:preset.width, height:preset.height};
  }
  async function requestWakeLock() {
    if (wakeLock || document.hidden || !navigator.wakeLock) return;
    try { wakeLock = await navigator.wakeLock.request('screen'); wakeLock.addEventListener('release', () => { wakeLock = null; }); }
    catch (_) { if (isMobile) notify('mobileNotice', t('无法保持屏幕常亮。录制和上传时请勿锁屏，并保持 Safari 在前台。')); }
  }
  function releaseWakeLock() { const current = wakeLock; wakeLock = null; current?.release().catch(() => {}); }
  async function clearCapture(id) {
    const db = await database();
    await new Promise((resolve, reject) => { const tx = db.transaction(['captures','captureChunks'], 'readwrite'); tx.objectStore('captures').delete(id); tx.objectStore('captureChunks').delete(IDBKeyRange.bound([id,0],[id,Number.MAX_SAFE_INTEGER])); tx.oncomplete = resolve; tx.onerror = tx.onabort = () => reject(tx.error); });
  }
  async function restoreCaptures() {
    const captures = await dbAction('captures', 'readonly', (store) => store.getAll());
    for (const capture of captures) {
      if (state.pending.has(capture.client_id)) { await clearCapture(capture.client_id); continue; }
      const parts = await dbAction('captureChunks', 'readonly', (store) => store.getAll(IDBKeyRange.bound([capture.client_id,0],[capture.client_id,Number.MAX_SAFE_INTEGER])));
      if (!parts.length) { await clearCapture(capture.client_id); continue; }
      const blob = new Blob(parts.map(part=>part.blob), {type:capture.mimeType});
      const item = {...capture, blob, total_bytes:blob.size, upload_decision:'pending', job_id:null, error:t('上次录制意外中断。请先下载检查原片是否可以播放，再决定是否上传。')};
      state.pending.set(item.client_id,item); await savePending(item); await clearCapture(item.client_id);
    }
  }
  async function loadCloudSettings() {
    const saved = await api('/preferences'); cloudRevision = saved.revision;
    if (saved.quality) {
      preferences = {privacy:saved.privacy, auto_publish:saved.auto_publish};
      processingMode = saved.processing_mode || state.session.default_processing_mode || 'builtin'; $('processingMode').value = processingMode;
      selectedQuality = saved.quality; orientation = saved.orientation; applyProcessingCopy();
      $('qualitySelect').value = selectedQuality; $('orientationSelect').value = orientation; updatePrefs(); updateRecordingBudget();
    }
    return saved;
  }
  async function saveCloudSettings() {
    if (state.recording || state.starting || state.stopping) { notify('cloudNotice', t('请结束录制后再保存设置。')); return; }
    $('saveCloudSettings').disabled = true; notify('cloudNotice', (localMode() ? t('正在保存设置…') : t('正在保存到账号…')), 'busy');
    try {
      const result = await api('/preferences', {method:'PUT',json:{revision:cloudRevision, ...preferences, quality:selectedQuality, orientation, processing_mode:processingMode}});
      cloudRevision = result.revision; notify('cloudNotice', (localMode() ? t('设置已保存，下次打开时继续使用。') : t('设置已保存。手机和电脑下次打开时会使用这些设置。')), 'success'); $('reloadCloudSettings').hidden = true;
    } catch(error) { notify('cloudNotice', messageOf(error), 'error'); $('reloadCloudSettings').hidden = error.status !== 409; }
    finally { $('saveCloudSettings').disabled = false; }
  }
  async function importVideo(file) {
    if (!file) return;
    if (importing || state.recording || state.starting || state.stopping) { notify('importNotice', t('请先结束当前录制或导入。'), 'error'); return; }
    if (!state.session?.authenticated) { notify('importNotice', t('请先登录后再选择视频。'), 'error'); return; }
    if (!file.size || !(file.type.startsWith('video/') || /\.(mp4|mov|m4v|webm)$/i.test(file.name))) { notify('importNotice', t('请选择 MP4、MOV 或 WebM 视频文件。'), 'error'); return; }
    if (file.size > state.session.limits.max_upload_bytes) { notify('importNotice', t('视频超过单段上限：') + bytes(state.session.limits.max_upload_bytes), 'error'); return; }
    if (!validateRecordingFields()) return;
    importing = true; lockRecordingFields(true); releasePreview(); showCaptureState(); notify('importNotice', t('正在准备视频：') + file.name, 'busy');
    try {
      const item = {client_id:crypto.randomUUID(), filename:file.name, retain_original:true, total_bytes:file.size, blob:file, created_at:new Date().toISOString(), ...preferences, processing_mode:processingMode, title:$('recordTopic').value, description:$('recordDescription').value, job_id:null, upload_decision:'pending'};
      state.pending.set(item.client_id,item); state.lastPending=item.client_id;
      let recoverable = true; try { await savePending(item); } catch (_) { recoverable = false; }
      notify('importNotice', file.name + ' · ' + bytes(file.size) + (recoverable ? t('，已准备好。') : t('。浏览器空间不足，无法保存恢复副本；请保持页面打开，并保留相册中的原片。')), recoverable ? 'success' : 'error');
      renderJobs(); await chooseUpload(item);
    } catch(error) { notify('importNotice', messageOf(error), 'error'); }
    finally { importing=false; lockRecordingFields(false); $('videoFile').value=''; }
  }
  function mobileSetup() {
    $('switchCamera').hidden = !isMobile;
    $('switchCamera').addEventListener('click', switchCamera);
    updateCameraSwitch();
    $('orientationSelect').addEventListener('change', async () => {
      orientation=$('orientationSelect').value; notify('mobileNotice', t('正在调整画面…'), 'busy');
      try { await preparePreview(); notify('mobileNotice', t('画面已调整。请检查预览方向，再开始录制。'), 'success'); }
      catch(error) { notify('mobileNotice', deviceError(error), 'error'); }
    });
    $('saveCloudSettings').addEventListener('click', saveCloudSettings);
    $('reloadCloudSettings').addEventListener('click', async () => {
      if (!await confirmation(t('读取账号设置？'), t('将用账号中较新的设置替换本页尚未保存的设置。'))) return;
      $('reloadCloudSettings').disabled=true; notify('cloudNotice', t('正在读取…'), 'busy');
      try { await loadCloudSettings(); await preparePreview(); notify('cloudNotice', t('已读取账号中的最新设置。'), 'success'); $('reloadCloudSettings').hidden=true; }
      catch(error) { notify('cloudNotice', messageOf(error), 'error'); }
      finally { $('reloadCloudSettings').disabled=false; }
    });
    $('chooseVideo').addEventListener('click', () => { notify('importNotice', t('请选择相册或文件中的视频。')); $('videoFile').click(); });
    $('videoFile').addEventListener('change', () => importVideo($('videoFile').files[0]));
    $('videoFile').addEventListener('cancel', () => notify('importNotice', t('已取消选择。')));
    const zone=$('videoDropZone'); let depth=0;
    zone.addEventListener('dragenter', event=>{event.preventDefault(); depth++; zone.classList.add('dragging'); $('dropHint').textContent=t('松开即可添加');});
    zone.addEventListener('dragover', event=>event.preventDefault());
    zone.addEventListener('dragleave', ()=>{if(--depth<=0){zone.classList.remove('dragging');$('dropHint').textContent=t('也可以将视频拖到这里');}});
    zone.addEventListener('drop', event=>{event.preventDefault(); depth=0;zone.classList.remove('dragging');$('dropHint').textContent=t('也可以将视频拖到这里');if(event.dataTransfer.files.length!==1) notify('importNotice',t('请一次选择一支视频。'),'error'); else importVideo(event.dataTransfer.files[0]);});
    if (isMobile) { $('retentionHelp').textContent=t('上传完成后，手机录制的原片仍保留在服务器，可在录制列表下载。'); $('chooseFolder').hidden=true; $('storageLabel').textContent=t('原片可下载到「文件」App'); $('storageFeedback').textContent=t('录制结束后可在列表下载原片。较长的视频也可先用 iPhone 相机录好，再从相册选择上传。'); }
    document.addEventListener('visibilitychange', () => {
      if (document.hidden && isMobile && state.recording && !state.stopping) { notify('mobileNotice', t('页面进入后台，正在尝试结束录制并保存已有内容。返回后请检查原片。'), 'error'); stopRecording(); }
      else if (!document.hidden && (state.recording || state.uploading.size)) requestWakeLock();
    });
    const rotated=()=>{ if (state.recording && isMobile && recordOrientation!==deviceOrientation()) { notify('mobileNotice',t('手机方向已改变，正在结束并保存这段录制。请按新方向开始下一段。'));stopRecording(); } else if (!state.recording && !state.starting && !state.stopping && state.session?.authenticated && orientation==='auto' && isMobile) preparePreview().catch(()=>{}); };
    if (screen.orientation) screen.orientation.addEventListener('change', rotated); else window.addEventListener('orientationchange', rotated);
    window.addEventListener('pageshow', event=>{if(event.persisted) location.reload();});
  }

  async function init() {
    mobileSetup();
    let languageDraft = null;
    try { languageDraft = JSON.parse(sessionStorage.getItem('daigui.languageDraft') || 'null'); sessionStorage.removeItem('daigui.languageDraft'); } catch (_) {}
    if (languageDraft) { $('recordTopic').value = languageDraft.title || ''; $('recordDescription').value = languageDraft.description || ''; state.drafts = new Map(languageDraft.drafts || []); }
    locale.beforeChange = () => {
      if (state.recording || state.starting || state.stopping || state.uploading.size) { notify('languageNotice', t('請先結束錄製並等待原片上傳完成，再切換語言。'), 'error'); return false; }
      if ($('confirmDialog').open || state.decidingUpload.size) { notify('languageNotice', t('請先完成目前的確認，再切換語言。'), 'error'); return false; }
      const drafts = new Map(state.drafts);
      for (const id of state.editing) drafts.set(id, { title: $('title-' + id).value, description: $('description-' + id).value, privacy: $('privacy-' + id).value });
      try { sessionStorage.setItem('daigui.languageDraft', JSON.stringify({ title: $('recordTopic').value, description: $('recordDescription').value, drafts: [...drafts], editing: [...state.editing] })); }
      catch (_) { notify('languageNotice', t('無法保留目前輸入，尚未切換語言。請先保存文案後重試。'), 'error'); return false; }
      return true;
    };
    $('toggleAccountDetails').addEventListener('click', () => { accountDetailsVisible = !accountDetailsVisible; renderAccountDetails(); });
    $('qualitySelect').value = selectedQuality; $('qualitySelect').addEventListener('change', changeQuality); updateRecordingBudget();
    updatePrefs(); $('recordButton').addEventListener('click', () => state.recording ? stopRecording() : startRecording()); $('chooseFolder').addEventListener('click', chooseFolder); $('connectButton').addEventListener('click', connect); $('reauthorizeButton').addEventListener('click', connect); $('logoutButton').addEventListener('click', () => accountAction(false)); $('disconnectButton').addEventListener('click', () => accountAction(true)); $('preferencesForm').addEventListener('submit', (event) => event.preventDefault()); $('defaultPrivacy').addEventListener('change', prefsChanged); $('autoPublish').addEventListener('change', prefsChanged); $('processingMode').addEventListener('change', () => { processingMode=$('processingMode').value; applyProcessingCopy(); notify('preferencesNotice', t('处理方式已修改，将用于下一次录制。')); }); $('refreshJobs').addEventListener('click', () => refreshJobs(true)); $('retryLocalSave').addEventListener('click', () => { const item = state.pending.get(state.lastPending); if (!item) { notify('recordNotice', t('這段原片已完成傳送，請在錄製列表中下載原片。')); return; } downloadBlob(item.blob, item.filename); notify('recordNotice', t('原片下載已開始，請查看瀏覽器的下載結果。')); }); $('retryUpload').addEventListener('click', async () => { if (![...state.pending.values()].some(uploadApproved)) { notify('recordNotice', t('沒有已確認上傳的原片需要重試。可在錄製列表選擇要上傳的片段。')); return; } await resumeApprovedUploads(); }); window.addEventListener('beforeunload', (event) => { if (state.recording || state.starting || state.stopping || state.uploading.size) { event.preventDefault(); event.returnValue = ''; } });
    $('cameraSelect').addEventListener('change', () => deviceChanged('camera')); $('microphoneSelect').addEventListener('change', () => deviceChanged('microphone')); $('refreshDevices').addEventListener('click', checkDevices); $('enableMeter').addEventListener('click', enableMeter); window.addEventListener('pagehide', releasePreview);
    navigator.mediaDevices?.addEventListener('devicechange', () => refreshDeviceList().catch((error) => notify('deviceNotice', deviceError(error), 'error')));
    await refreshDeviceList().catch((error) => notify('deviceNotice', deviceError(error), 'error'));
    try { await loadSession(); if (!state.session?.authenticated) return; await loadCloudSettings(); await restoreLocal(); await restoreCaptures(); renderJobs(); if (!isMobile) preparePreview().catch(() => {}); await refreshJobs(); for (const id of languageDraft?.editing || []) { const card = [...$('jobsList').children].find((item) => item.dataset.jobId === id); if (card?.querySelector('.editor-toggle')) openEditor(card, id); } if (state.session.authenticated) resumeApprovedUploads(); const params = new URLSearchParams(location.search); if (params.has('error') || ['failed','cancelled'].includes(params.get('auth'))) { notify('authNotice', t('Google 授權未完成，請重新連結帳戶。'), 'error'); history.replaceState({}, '', location.pathname); } else if (params.has('connected') || params.has('authorized') || params.get('auth') === 'connected') { notify('authNotice', t('Google 帳戶已連結，可以開始錄製。')); history.replaceState({}, '', location.pathname); }
    } catch (error) { $('connectionText').textContent = t('連線暫時中斷'); notify('authNotice', t('無法連接服務：') + messageOf(error) + t(' 請重新整理頁面後再試。'), 'error'); }
    setInterval(() => { if (state.session?.authenticated && !document.hidden) loadSession().catch(() => {}); }, 6 * 60 * 60 * 1000);
    window.addEventListener('online', async () => { try { await loadSession(); if (state.session.authenticated) resumeApprovedUploads(); } catch (_) { notify('authNotice', t('連接暫時無法恢復，請重新整理或稍後重試。'), 'error'); } });
    state.poll = setInterval(() => { if (state.session?.authenticated && !document.hidden) refreshJobs(false); }, 5000); document.addEventListener('visibilitychange', () => { if (!document.hidden && state.session?.authenticated) refreshJobs(false); });
  }
  init();
})();
