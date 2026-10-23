// ==UserScript==
// @name         Audiochan — список для архива
// @namespace    local.audiochan.archive-list
// @version      1.2.0
// @description  Плюс/минус для аудио, сценариев и интересных авторов; экспорт списка ссылок в TXT.
// @match        https://audiochan.com/*
// @match        https://www.audiochan.com/*
// @run-at       document-idle
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_listValues
// @grant        GM_registerMenuCommand
// ==/UserScript==

(() => {
    'use strict';
    // One record per URL avoids overwriting additions from another open tab.
    // Keep namespace and key prefix unchanged when upgrading this script.
    const PREFIX = 'audiochan.archive.url.v1:';
    const normalize = value => {
        try {
            const url = new URL(value);
            if (url.protocol !== 'https:' || !['audiochan.com', 'www.audiochan.com'].includes(url.hostname)) return null;
            const match = url.pathname.match(/^\/(a|s|u)\/([A-Za-z0-9_-]+)\/?$/);
            return match ? `https://audiochan.com/${match[1]}/${match[2]}` : null;
        } catch { return null; }
    };
    const kind = url => ({ a: 'audio', s: 'script', u: 'author' })[url?.split('/')[3]] || null;
    const labelFor = url => ({ audio: 'аудио', script: 'сценарий', author: 'автора' })[kind(url)] || 'страницу';
    const key = url => PREFIX + url;
    const selected = url => Boolean(url && GM_getValue(key(url), null)?.selected);
    const entries = () => GM_listValues().filter(k => k.startsWith(PREFIX))
        .map(k => GM_getValue(k, null)).filter(r => r?.selected && normalize(r.url) === r.url)
        .sort((a, b) => a.addedAt.localeCompare(b.addedAt) || a.url.localeCompare(b.url));
    function write(url, value, title = '') {
        const old = GM_getValue(key(url), null);
        GM_setValue(key(url), {
            url, type: kind(url), selected: value, title: title || old?.title || '',
            addedAt: value && !old?.selected ? new Date().toISOString() : old?.addedAt || new Date().toISOString(),
            updatedAt: new Date().toISOString()
        });
    }

    const host = document.createElement('div');
    host.id = 'audiochan-local-archive-list';
    // A zero-size anchor outside body cannot become a flex/grid item in the
    // site's layout. Only its absolutely positioned children accept clicks.
    host.style.cssText = 'all:initial!important;position:fixed!important;inset:auto 20px 110px auto!important;width:0!important;height:0!important;margin:0!important;padding:0!important;border:0!important;overflow:visible!important;z-index:2147483646!important;display:block!important;pointer-events:none!important;';
    const root = host.attachShadow({ mode: 'open' });
    root.innerHTML = `
      <style>
        :host{font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:#eee}
        *{box-sizing:border-box}button,textarea{font:inherit}button{cursor:pointer}
        .dock{position:absolute;right:0;bottom:0;display:flex;align-items:center;gap:9px;justify-content:flex-end;width:max-content;pointer-events:auto}
        .toggle{width:60px;height:60px;border-radius:50%;border:2px solid #ffb0ce;background:#f397bb;color:#29141d;font-size:38px;line-height:1;box-shadow:0 5px 25px #0005}
        .toggle.saved{background:#241b22;color:#ffbad5;border-color:#d28fab}
        .toggle:hover{transform:scale(1.06)}.toggle[hidden]{display:none}
        .list-button{background:#231d25;color:#ffe7f0;border:1px solid #926078;border-radius:24px;padding:11px 16px;box-shadow:0 5px 20px #0004}
        button:focus-visible,a:focus-visible,textarea:focus-visible{outline:3px solid #ffb0ce;outline-offset:3px}
        .panel{position:absolute;right:0;bottom:74px;pointer-events:auto;background:#19171e;color:#f5edf1;border:1px solid #78576d;border-radius:20px;padding:20px;width:min(470px,calc(100vw - 40px));max-height:calc(100dvh - 210px);overflow:auto;box-shadow:0 15px 55px #0007}
        .panel[hidden]{display:none}.heading{display:flex;align-items:center;justify-content:space-between;gap:15px}
        h2{font-size:18px;margin:0}.close{border:0;background:transparent;color:#e8c8d8;font-size:25px;padding:0 5px}
        p{font-size:13px;color:#c7b6c3;margin:10px 0}
        textarea{display:block;width:100%;height:210px;resize:vertical;background:#100f14;color:#eee;border:1px solid #574251;border-radius:10px;padding:12px;font-size:12px;line-height:1.6;user-select:text}
        .actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.actions button{border:1px solid #765269;background:#30232e;color:#ffe2ee;border-radius:9px;padding:9px 12px}
        .actions .primary{background:#e6a1bd;color:#26131d;border-color:#e6a1bd}
        .status{min-height:21px;color:#e9b9ce;font-size:12px;margin-top:10px}
        .hint{position:absolute;right:0;bottom:74px;width:max-content;max-width:240px;background:#211b24;color:#f6e1eb;border-radius:10px;padding:7px 12px;font-size:12px;box-shadow:0 4px 15px #0003}
        .panel:not([hidden])~.hint{display:none}
        .hint:empty{display:none}@media(max-width:520px){.toggle{width:52px;height:52px}.panel{padding:16px}textarea{height:170px}}
      </style>
      <section class="panel" hidden aria-label="Список для архива">
        <div class="heading"><h2>Список для архива</h2><button class="close" aria-label="Закрыть список">×</button></div>
        <p>Хранится в менеджере userscript в этом профиле браузера. Скачивайте TXT как резервную копию и для передачи архиватору.</p>
        <textarea aria-label="Ссылки — по одной на строку" spellcheck="false" placeholder="https://audiochan.com/a/…&#10;https://audiochan.com/s/…&#10;https://audiochan.com/u/…"></textarea>
        <div class="actions"><button class="primary" data-action="download">Скачать TXT</button><button data-action="copy">Копировать</button><button data-action="merge">Добавить из поля</button></div>
        <p>Аудио и сценарии — для сохранения. Авторы — отдельные отметки интереса, без загрузки всех работ. «Добавить из поля» объединяет ссылки с сохранёнными. Удаление — кнопкой «−» на соответствующей странице.</p>
        <div class="status" role="status" aria-live="polite"></div>
      </section>
      <div class="hint" role="status" aria-live="polite"></div>
      <div class="dock"><button class="list-button" aria-expanded="false">Список · 0</button><button class="toggle" hidden aria-label="Добавить в список для архива">+</button></div>`;
    const $ = selector => root.querySelector(selector);
    const toggle = $('.toggle'), listButton = $('.list-button'), panel = $('.panel'), field = $('textarea');
    let current = null, signature = '', hintTimer;
    function notify(message) {
        $('.hint').textContent = message;
        clearTimeout(hintTimer);
        hintTimer = setTimeout(() => { $('.hint').textContent = ''; }, 3500);
    }
    function text() { return entries().map(r => r.url).join('\n'); }
    function refresh() {
        if (!host.isConnected && document.documentElement) document.documentElement.append(host);
        current = normalize(location.href);
        const saved = selected(current);
        toggle.hidden = !current;
        toggle.classList.toggle('saved', saved);
        toggle.textContent = saved ? '−' : '+';
        const label = saved ? `Убрать ${labelFor(current)} из списка` : `Добавить ${labelFor(current)} в список`;
        toggle.setAttribute('aria-label', label); toggle.title = label;
        toggle.setAttribute('aria-pressed', String(saved));
        const records = entries();
        listButton.textContent = `Список · ${records.length}`;
        const next = records.map(r => r.url).join('\n');
        // Never replace text while the user is editing a list for import.
        if (next !== signature && root.activeElement !== field) field.value = next;
        signature = next;
    }
    function openPanel() {
        panel.hidden = false;
        listButton.setAttribute('aria-expanded', 'true');
        field.value = text();
        $('.status').textContent = `Сохранено ссылок: ${entries().length}`;
        refresh();
    }
    function closePanel() { panel.hidden = true; listButton.setAttribute('aria-expanded', 'false'); }
    function setCurrent(value) {
        const url = normalize(location.href);
        if (!url) return refresh();
        if (selected(url) === value) {
            refresh();
            notify(value ? 'Уже есть в списке' : 'Уже отсутствует в списке');
            return;
        }
        try {
            write(url, value, document.querySelector('h1')?.textContent?.trim() || document.title.replace(/\s*-\s*audiochan\s*$/i, ''));
            refresh(); notify(value ? (kind(url) === 'author' ? 'Автор отмечен как интересный' : 'Добавлено в список для архива') : 'Удалено из списка');
        } catch { notify('Не удалось сохранить. Проверьте хранилище менеджера userscript.'); }
    }
    toggle.addEventListener('click', () => setCurrent(!selected(normalize(location.href))));
    window.addEventListener('keydown', event => {
        if (!['NumpadAdd', 'NumpadSubtract'].includes(event.code)) return;
        if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.isComposing) return;
        if (!normalize(location.href)) return;
        if (event.composedPath().some(node => node instanceof HTMLElement &&
            (node.matches('input,textarea,select,[role="textbox"],[role="combobox"]') || node.isContentEditable))) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!event.repeat) setCurrent(event.code === 'NumpadAdd');
    }, true);
    listButton.addEventListener('click', () => panel.hidden ? openPanel() : closePanel());
    $('.close').addEventListener('click', closePanel);
    root.addEventListener('keydown', event => { if (event.key === 'Escape') closePanel(); });
    $('[data-action="download"]').addEventListener('click', () => {
        const data = text();
        if (!data) { $('.status').textContent = 'Список пока пуст.'; return; }
        const blob = new Blob([data + '\n'], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url; link.download = `audiochan-urls-${new Date().toISOString().slice(0,10)}.txt`;
        root.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 30000);
        $('.status').textContent = 'TXT подготовлен к скачиванию. Список остаётся сохранённым.';
    });
    $('[data-action="copy"]').addEventListener('click', async () => {
        const data = text();
        try {
            await navigator.clipboard.writeText(data + (data ? '\n' : ''));
            $('.status').textContent = `Скопировано ссылок: ${entries().length}`;
        } catch {
            field.value = data; field.focus(); field.select();
            $('.status').textContent = 'Нажмите Ctrl+C, чтобы скопировать выделенный список.';
        }
    });
    $('[data-action="merge"]').addEventListener('click', () => {
        const candidates = field.value.match(/https:\/\/[^\s<>"']+/g) || [];
        const urls = [...new Set(candidates.map(normalize).filter(Boolean))];
        let added = 0;
        try {
            for (const url of urls) if (!selected(url)) { write(url, true); added++; }
            field.value = text(); refresh();
            $('.status').textContent = urls.length ? `Добавлено: ${added}. Всего: ${entries().length}.` : 'Не найдены ссылки Audiochan вида /a/…, /s/… или /u/…';
        } catch { $('.status').textContent = 'Ошибка сохранения. Часть ссылок могла сохраниться — повторный импорт безопасен.'; }
    });
    GM_registerMenuCommand('Открыть список для архива', openPanel);
    refresh();
    // Audiochan navigates without page reloads; also notice changes from other tabs.
    setInterval(refresh, 1500);
    window.addEventListener('popstate', refresh);
    window.addEventListener('pageshow', refresh);
})();
