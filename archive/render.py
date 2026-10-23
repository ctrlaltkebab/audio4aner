from __future__ import annotations

import html
import os
from urllib.parse import quote
from .model import rich, plain, person


CSS = '''
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#141217;color:#eee6eb;font:16px/1.75 system-ui,sans-serif}main{max-width:1000px;margin:55px auto;padding:0 28px 70px}a{color:#ecacc8}h1{font-size:clamp(26px,4vw,42px);line-height:1.25}header{border-bottom:1px solid #493240;padding-bottom:25px;margin-bottom:28px}.muted,small{color:#baa7b5}audio{width:100%;margin:20px 0}.tag{display:inline-block;border:1px solid #614354;padding:3px 10px;border-radius:20px;margin:3px;font-size:13px}.credits{display:flex;flex-wrap:wrap;gap:18px;margin:22px 0}.credit{border:1px solid #493240;border-radius:12px;padding:10px 16px}.document{max-width:780px}.document img{max-width:100%;height:auto}blockquote{border-left:3px solid #cb91ad;margin-left:0;padding-left:20px}li{margin:9px 0}pre{overflow:auto}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #493240}.item{padding:14px 0;border-bottom:1px solid #392b34}
'''


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(path)


def document(title, body):
    return f'<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>{CSS}</style><main>{body}</main></html>'


def write_entry(store, entry):
    folder = store.path(entry['folder'])
    def local(path):
        return quote(os.path.relpath(store.path(path), folder).replace('\\', '/'), safe='/')
    images = {r['url']: local(r['path']) for r in entry.get('images', []) if r.get('path')}
    e = html.escape
    body = f'<header><a href="../../index.html">← Библиотека</a><p class="muted">{e(entry["kind"])} · автономная копия</p><h1>{e(entry["title"])}</h1>'
    if entry.get('source_url'):
        body += f'<a href="{e(entry["source_url"], quote=True)}">Источник Audiochan</a>'
    body += '</header>'
    body += ''.join(f'<span class="tag">{e(t.get("name", ""))} · {e(t.get("category", ""))}</span>' for t in entry.get('tags', []))
    body += '<div class="credits">'
    for c in entry.get('credits', []):
        p = person(c)
        author = store.get(p['key'])
        name = e(p['title'])
        if author:
            name = f'<a href="{local(author["folder"] + "/page.html")}">{name}</a>'
        body += f'<div class="credit"><small>{e(c.get("role") or "участник")}</small><br>{name}</div>'
    body += '</div>'
    for m in entry.get('media', []):
        if m.get('path'):
            body += f'<p>{e(m.get("label", "Аудио"))}</p><audio controls preload="metadata" src="{local(m["path"])}"></audio><p><a href="{local(m["path"])}">Открыть файл</a> · {m.get("bytes",0):,} байт</p>'
    if entry['kind'] == 'audio' and not entry.get('media'):
        body += '<p class="muted">Текст сохранён. Аудиофайл пока не загружен.</p>'
    body += '<article class="document">' + rich(entry.get('description'), images)
    if entry.get('body'):
        body += '<hr>' + rich(entry['body'], images)
    if entry.get('notes'):
        body += '<h2>Примечания автора</h2>' + rich(entry['notes'], images)
    body += '</article>'
    if entry['kind'] == 'author':
        if not entry.get('linked_profile'):
            body += '<p class="muted">Имя из подписи к работе. Связь с профилем сайта не подтверждена.</p>'
        for kind in ('audio', 'script'):
            works = []
            while True:
                batch = store.query(kind=kind, person_key=entry['key'], offset=len(works), limit=100)
                works.extend(batch['items'])
                if len(works) >= batch['total'] or not batch['items']:
                    break
            if works:
                body += '<h2>' + ('Аудио' if kind == 'audio' else 'Сценарии') + '</h2>'
                body += ''.join(f'<div class="item"><a href="{local(w["folder"] + "/page.html")}">{e(w["title"])}</a></div>' for w in works)
        for link in (entry.get('profile') or {}).get('links', []):
            if link.get('url', '').startswith(('https://', 'http://')):
                body += f'<p><a href="{e(link["url"], quote=True)}">{e(link.get("title") or link["url"])}</a></p>'
    write_text(folder / 'page.html', document(entry['title'], body))
    readme = f'# {entry["title"]}\n\nИсточник: {entry.get("source_url", "")}\n\n' + plain(entry.get('description'))
    if entry.get('body'):
        readme += '\n\n' + plain(entry['body'])
    if entry.get('notes'):
        readme += '\n\nПримечания автора\n\n' + plain(entry['notes'])
    readme += '\n\nУчастники:\n' + '\n'.join(f'- {c.get("role", "")}: {person(c)["title"]}' for c in entry.get('credits', []))
    readme += '\n\nТеги:\n' + '\n'.join(f'- {t.get("category", "")}: {t.get("name", "")}' for t in entry.get('tags', []))
    write_text(folder / 'README.md', readme + '\n')


def write_index(store):
    with store.connect() as db:
        rows = db.execute('SELECT kind,title,folder FROM entries ORDER BY kind,title').fetchall()
    sections = []
    for kind, label in [('audio', 'Аудио'), ('script', 'Сценарии'), ('author', 'Авторы')]:
        items = ''.join(f'<div class="item"><a href="{quote(r["folder"], safe="/")}/page.html">{html.escape(r["title"])}</a></div>' for r in rows if r['kind'] == kind)
        sections.append(f'<h2>{label}</h2>' + (items or '<p class="muted">Пока пусто</p>'))
    write_text(store.library / 'index.html', document('Audiochan · локальная библиотека', '<header><h1>Audiochan Archive</h1><p class="muted">Автономный индекс. Все страницы и медиа открываются без сервера.</p></header>' + ''.join(sections)))


def render_all(store):
    with store.connect() as db:
        import json
        entries = [json.loads(r[0]) for r in db.execute('SELECT payload FROM entries')]
    for e in entries:
        write_entry(store, e)
    write_index(store)
