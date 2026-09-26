// Randy 測試擴充 view：示範正常能力 + 自動跑權限隔離自檢。
(async function () {
  const $ = (id) => document.getElementById(id);
  const xhub = window.xhub;

  let n = (await xhub.storage.get('counter')) || 0;
  $('count').textContent = n;
  $('plus').addEventListener('click', async () => {
    n += 1;
    $('count').textContent = n;
    await xhub.storage.set('counter', n);
    xhub.events.emit('hello:counter', n);
  });
  xhub.events.on('hello:counter', (v) => { n = v; $('count').textContent = v; });
  $('greeting').textContent = `config.greeting = ${await xhub.config.get('greeting')}`;

  window.RandyHello.gatewayDot($('gw'), $('gw-text'));
  try {
    const providers = await xhub.strategist.providers();
    const slots = (providers.slots && providers.slots.slots) || [];
    $('slots').textContent = `provider 插槽：${slots.map((s) => s.name).join('、') || '—'}`;
  } catch (err) { $('slots').textContent = `插槽讀取失敗：${err.code}`; }

  const info = await xhub.runtime.info();
  $('info').textContent = `${info.id} v${info.version} · 宿主 ${info.host} ${info.hostVersion}（${info.platform}）· ` +
    `能力 ${info.capabilities.length} 項 · 已授權：${Object.entries(info.permissions).filter(([, v]) => v).map(([k]) => k).join(', ') || '無'}`;
  $('notify').addEventListener('click', () => xhub.notify.show({ title: 'Randy 測試擴充', body: '通知權限正常運作' })
    .then(() => { $('notify').textContent = '已送出 ✓'; })
    .catch((err) => { $('notify').textContent = `失敗：${err.code}`; })); // sandbox 不給 alert()

  $('external').addEventListener('click', () => xhub.openExternal('https://example.com/?from=randy-hub'));

  const expectDenied = async (label, fn, codes) => {
    try {
      await fn();
      return [label, false, '沒有被擋（應該要被擋）'];
    } catch (err) {
      return [label, codes.includes(err.code), `${err.code || err.name}：${err.message}`];
    }
  };
  const results = await Promise.all([
    expectDenied('sharedStorage（未宣告 shared-storage）', () => xhub.sharedStorage.get('x'), ['PERMISSION_DENIED']),
    expectDenied('strategist.command（高危，預設未授權）', () => xhub.strategist.command('自檢：這條不該被執行'), ['PERMISSION_DENIED']),
    expectDenied('fs.saveText（未宣告 fs）', () => xhub.fs.saveText({ name: 'x.txt', content: 'x' }), ['PERMISSION_DENIED']),
    expectDenied('x-hub data.notes.list（Randy Hub 未提供）', () => xhub.data.notes.list(), ['CAPABILITY_UNAVAILABLE']),
  ]);
  const rows = results.map(([label, pass, detail]) => {
    const tr = document.createElement('tr');
    for (const [text, cls] of [[pass ? 'PASS' : 'FAIL', pass ? 'pass' : 'fail'], [label, ''], [detail, 'muted']]) {
      const td = document.createElement('td');
      td.textContent = text;
      if (cls) td.className = cls;
      tr.append(td);
    }
    return tr;
  });
  $('checks').append(...rows);
  await xhub.storage.set('selfcheck', results.map(([label, pass]) => ({ label, pass })));
})();
