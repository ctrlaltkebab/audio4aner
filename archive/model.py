from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, quote

KINDS = {'a': 'audio', 's': 'script', 'u': 'author'}
ROUTES = {v: k for k, v in KINDS.items()}


def now():
    return datetime.now(timezone.utc).isoformat()


def normalize(value):
    try:
        u = urlsplit(value.strip())
        if u.scheme != 'https' or u.netloc not in ('audiochan.com', 'www.audiochan.com'):
            return None
        m = re.fullmatch(r'/(a|s|u)/([A-Za-z0-9_-]+)/?', u.path)
        return f'https://audiochan.com/{m[1]}/{m[2]}' if m else None
    except (ValueError, AttributeError):
        return None


def urls_in(text):
    candidates = re.findall(r'https://[^\s<>"\']+', text)
    return list(dict.fromkeys(u for c in candidates if (u := normalize(c))))


def identify(url):
    canonical = normalize(url)
    if not canonical:
        raise ValueError('Нужна ссылка Audiochan вида /a/…, /s/… или /u/…')
    parts = urlsplit(canonical).path.split('/')
    return KINDS[parts[1]], parts[2], canonical


def plain(doc):
    if isinstance(doc, str):
        return doc
    if isinstance(doc, list):
        return ''.join(plain(n) for n in doc)
    if not isinstance(doc, dict):
        return ''
    if doc.get('type') == 'hardBreak':
        return '\n'
    text = doc.get('text', '') + plain(doc.get('content', []))
    return text + ('\n\n' if doc.get('type') in ('paragraph', 'heading', 'listItem') else '')


def rich(doc, images=None):
    """Render the document schema, never arbitrary source HTML."""
    if isinstance(doc, str):
        return '<p>' + html.escape(doc).replace('\n', '<br>') + '</p>'
    if isinstance(doc, list):
        return ''.join(rich(n, images) for n in doc)
    if not isinstance(doc, dict):
        return ''
    typ = doc.get('type', '')
    if typ == 'text':
        result = html.escape(doc.get('text', ''))
        for mark in doc.get('marks', []):
            tags = {'bold': 'strong', 'italic': 'em', 'strike': 's', 'underline': 'u', 'code': 'code'}
            if mark.get('type') in tags:
                tag = tags[mark['type']]
                result = f'<{tag}>{result}</{tag}>'
            elif mark.get('type') == 'link':
                href = mark.get('attrs', {}).get('href', '')
                if urlsplit(href).scheme in ('https', 'http', 'mailto'):
                    result = f'<a href="{html.escape(href, quote=True)}" rel="noreferrer">{result}</a>'
        return result
    if typ == 'hardBreak':
        return '<br>'
    if typ == 'horizontalRule':
        return '<hr>'
    if typ == 'image':
        src = doc.get('attrs', {}).get('src', '')
        local = (images or {}).get(src)
        return f'<img loading="lazy" src="{html.escape(local, quote=True)}" alt="Иллюстрация">' if local else '<p class="muted">[Иллюстрация пока не сохранена]</p>'
    child = rich(doc.get('content', []), images)
    tag = {'paragraph': 'p', 'blockquote': 'blockquote', 'bulletList': 'ul', 'orderedList': 'ol', 'listItem': 'li', 'codeBlock': 'pre', 'table': 'table', 'tableRow': 'tr', 'tableCell': 'td', 'tableHeader': 'th'}.get(typ)
    if typ == 'heading':
        tag = 'h' + str(max(2, min(6, int(doc.get('attrs', {}).get('level', 2)))))
    return f'<{tag}>{child}</{tag}>' if tag else child


def safe_name(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:90] or 'Без названия'


def person(credit):
    user = credit.get('user') or {}
    name = user.get('display_name') or user.get('username') or credit.get('display_name') or 'Неизвестный участник'
    identity = str(user.get('id') or 'unlinked-' + hashlib.sha256((name.casefold()).encode()).hexdigest()[:20])
    return {
        'key': 'author:' + identity, 'id': identity, 'kind': 'author',
        'title': name, 'slug': user.get('username') or '',
        'source_url': f"https://audiochan.com/u/{quote(user['username'])}" if user.get('username') else '',
        'description': user.get('bio') or '', 'body': None, 'notes': None,
        'tags': [], 'credits': [], 'profile': user, 'linked_profile': bool(user.get('id')),
        'media': [], 'images': [], 'state': 'metadata', 'saved_at': now(),
        'updated_at': now(), 'error': '', 'metadata_complete': bool(user.get('id')),
    }


def make_entry(kind, slug, url, raw):
    data = raw.get('data', raw) if kind == 'author' else raw
    if not isinstance(data, dict) or not data.get('id'):
        raise ValueError('Ответ сайта не содержит карточку с ID')
    return {
        'schema_version': 1, 'key': kind + ':' + str(data['id']),
        'id': str(data['id']), 'kind': kind, 'slug': data.get('username') if kind == 'author' else data.get('slug', slug),
        'source_url': url, 'title': (data.get('display_name') or data.get('username')) if kind == 'author' else data.get('title', 'Без названия'),
        'description': data.get('bio', '') if kind == 'author' else data.get('description') or data.get('teaser') or '',
        'body': data.get('body'), 'notes': data.get('author_notes'),
        'profile': data if kind == 'author' else None,
        'linked_profile': kind == 'author', 'metadata_complete': True,
        'tags': data.get('tags') or [], 'credits': data.get('credits') or [],
        'media': [], 'images': [], 'state': 'metadata',
        'saved_at': now(), 'updated_at': now(), 'error': '',
        'duration': float((data.get('audioFile') or {}).get('duration') or 0),
        'waveform': (data.get('audioFile') or {}).get('waveform') or [],
        'series': data.get('series'), 'published_at': data.get('published_at') or data.get('created_at'),
        'expected_media': [{k: f.get(k) for k in ('id', 'label', 'variant_ids', 'duration')} for f in audio_files(data)] if kind == 'audio' else [],
    }


def audio_files(raw):
    found = {}
    def add(file, label, variant=None):
        if not isinstance(file, dict) or not file.get('id'):
            return
        fid = str(file['id'])
        if fid not in found:
            found[fid] = dict(file, label=label, variant_ids=[], variant_labels=[])
        item = found[fid]
        if variant:
            if variant.get('id') and variant['id'] not in item['variant_ids']:
                item['variant_ids'].append(variant['id'])
            if label not in item['variant_labels']:
                item['variant_labels'].append(label)
            item['label'] = ' / '.join(item['variant_labels'])
    add(raw.get('audioFile') or raw.get('audio_file'), 'Основная версия')
    for i, variant in sorted(enumerate(raw.get('variants') or []), key=lambda pair: (pair[1].get('order') or 0, pair[0])):
        file = variant.get('audioFile') or variant.get('audio_file') or variant.get('file')
        if not file and variant.get('audio_file_id'):
            file = {'id': variant['audio_file_id']}
        add(file, variant.get('label') or variant.get('title') or f'Версия {i + 1}', variant)
    add(raw.get('teaserFile'), 'Превью')
    for file in raw.get('files') or []:
        if isinstance(file, dict) and str(file.get('mime_type', '')).startswith('audio/'):
            add(file, file.get('label') or file.get('title') or 'Аудио')
    return list(found.values())


def image_urls(raw):
    result = set()
    def visit(value):
        if isinstance(value, list):
            for n in value:
                visit(n)
        elif isinstance(value, dict):
            for k, v in value.items():
                if k in ('avatar', 'background', 'thumbnail_url', 'src', 'image', 'cover_url') and isinstance(v, str) and v.startswith('https://'):
                    result.add(v)
                elif isinstance(v, (dict, list)):
                    visit(v)
    visit(raw)
    return sorted(result)
