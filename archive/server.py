from __future__ import annotations

import json
import mimetypes
import re
import secrets
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, quote, unquote

from .model import rich, plain, person
from .render import render_all


def serve(store, jobs, port=8765, open_browser=True):
    token = secrets.token_urlsafe(32)
    web = store.root / 'web'
    allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    origins = {'http://' + h for h in allowed_hosts}

    def file_url(path):
        return '/files/' + quote(path, safe='/')

    def prepare(entry, detail=False):
        result = dict(entry)
        images = {im['url']: file_url(im['path']) for im in entry.get('images', [])}
        result['preview'] = plain(entry.get('description'))[:230]
        result['cover'] = next((images[url] for url in [(entry.get('profile') or {}).get('avatar'), *((c.get('user') or {}).get('avatar') for c in entry.get('credits', []))] if url in images), None)
        result['participants'] = [dict(key=person(c)['key'], title=person(c)['title'], role=c.get('role') or c.get('custom_role') or 'participant') for c in entry.get('credits', [])]
        result['offline_url'] = file_url(entry['folder'] + '/page.html')
        result['folder_label'] = 'library/' + entry['folder']
        result['media'] = [dict(m, url=file_url(m['path'])) for m in entry.get('media', [])]
        result['images'] = [dict(m, local_url=file_url(m['path'])) for m in entry.get('images', [])]
        if detail:
            result['description_html'] = rich(entry.get('description'), images)
            result['body_html'] = rich(entry.get('body'), images)
            result['notes_html'] = rich(entry.get('notes'), images)
            result['position'] = store.user_data().get('positions', {}).get(entry['key'], 0)
            positions = store.user_data().get('positions', {})
            result['positions'] = {m['id']: positions.get(entry['key'] + ':file:' + m['id'], positions.get(entry['key'], 0) if i == 0 else 0) for i, m in enumerate(entry.get('media', []))}
            result['profile_links'] = [l for l in (entry.get('profile') or {}).get('links', []) if l.get('url', '').startswith(('https://', 'http://'))]
        for k in ('description', 'body', 'notes', 'profile', 'credits'):
            result.pop(k, None)
        return result

    class Handler(BaseHTTPRequestHandler):
        server_version = 'AudiochanArchive/0.1'

        def log_message(self, fmt, *args):
            pass

        def valid_host(self):
            return self.headers.get('Host') in allowed_hosts

        def json_response(self, data, status=200):
            payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(payload)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self.valid_host():
                return self.json_response({'error': 'Неверный адрес сервера'}, 403)
            parsed = urlsplit(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)
            arg = lambda key, default='': qs.get(key, [default])[0]
            try:
                if path == '/api/bootstrap':
                    return self.json_response(dict(token=token, **store.info()))
                if path == '/api/library':
                    kind = arg('kind', 'audio')
                    if kind not in ('audio', 'script', 'author'):
                        raise ValueError('Неизвестный раздел')
                    data = store.query(kind=kind, q=arg('q'), tags=qs.get('tag', []), exclude=qs.get('exclude', []), person_key=arg('person'), state=arg('state'), offset=int(arg('offset', '0')))
                    data['items'] = [prepare(e) for e in data['items']]
                    return self.json_response(data)
                if path == '/api/entry':
                    e = store.get(arg('key'))
                    return self.json_response(prepare(e, detail=True) if e else {'error': 'Карточка не найдена'}, 200 if e else 404)
                if path == '/api/queue':
                    return self.json_response(jobs.status(limit=0 if arg('summary') == '1' else 100, offset=max(0, int(arg('offset', '0')))))
                if path == '/api/export':
                    urls = list(dict.fromkeys([t['url'] for t in jobs.snapshot()['items']]))
                    data = ('\n'.join(urls) + ('\n' if urls else '')).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/plain; charset=utf-8')
                    self.send_header('Content-Disposition', 'attachment; filename="audiochan-urls.txt"')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    if self.command != 'HEAD':
                        self.wfile.write(data)
                    return
                if path.startswith('/files/'):
                    return self.send_file(store.path(unquote(path[len('/files/'):])) )
                assets = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}
                if path in assets:
                    return self.send_file(web / assets[path])
                return self.json_response({'error': 'Не найдено'}, 404)
            except (ValueError, KeyError) as exc:
                return self.json_response({'error': str(exc)}, 400)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            if not self.valid_host() or (self.headers.get('Origin') and self.headers['Origin'] not in origins) or not secrets.compare_digest(self.headers.get('X-Archive-Token', ''), token):
                return self.json_response({'error': 'Запрос разрешён только из локального приложения'}, 403)
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 2 * 1024 * 1024:
                    raise ValueError('Слишком большой или пустой запрос')
                body = json.loads(self.rfile.read(length))
                path = urlsplit(self.path).path
                if path == '/api/import':
                    return self.json_response(jobs.add(str(body.get('text', '')), media=body.get('media', True)))
                if path == '/api/queue':
                    jobs.change(body.get('action'), body.get('id'))
                    return self.json_response({'ok': True})
                if path == '/api/entry-action':
                    e = store.get(body.get('key', ''))
                    if not e or not e.get('source_url'):
                        raise ValueError('У карточки нет подтверждённой ссылки на источник')
                    jobs.add(e['source_url'], media=True)
                    task = next(t for t in jobs.snapshot()['items'] if t['url'] == e['source_url'])
                    jobs.change('media', task['id'])
                    return self.json_response({'ok': True})
                if path == '/api/position':
                    key = body.get('key', '')
                    if not store.get(key):
                        raise ValueError('Карточка не найдена')
                    value = float(body.get('position', 0))
                    if not 0 <= value <= 1000000:
                        raise ValueError('Неверная позиция')
                    file_id = body.get('file_id')
                    if file_id and not any(m['id'] == file_id for m in store.get(key).get('media', [])):
                        raise ValueError('Аудиоверсия не найдена')
                    store.position(key + ':file:' + file_id if file_id else key, value)
                    return self.json_response({'ok': True})
                if path == '/api/rebuild':
                    # Index is disposable; durable JSON/media and user data remain untouched.
                    store.rebuild()
                    render_all(store)
                    return self.json_response({'ok': True})
                return self.json_response({'error': 'Неизвестное действие'}, 404)
            except (ValueError, TypeError, KeyError) as exc:
                self.json_response({'error': str(exc)}, 400)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def send_file(self, path):
            if not path.is_file():
                return self.json_response({'error': 'Файл пока не сохранён'}, 404)
            size = path.stat().st_size
            start, end, status = 0, size - 1, 200
            requested = self.headers.get('Range')
            if requested:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
                if match and any(match.groups()):
                    if match[1]:
                        start = int(match[1])
                        end = min(int(match[2]) if match[2] else end, end)
                    else:
                        start = max(0, size - int(match[2]))
                    status = 206
                if not match or not any(match.groups()) or start >= size or start > end:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
            self.send_response(status)
            mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            if path.suffix == '.js':
                mime = 'text/javascript'
            self.send_header('Content-Type', mime + ('; charset=utf-8' if mime.startswith('text/') else ''))
            self.send_header('Content-Length', str(max(0, end - start + 1)))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-cache')
            if path.suffix == '.html':
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            if status == 206:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            if self.command == 'HEAD':
                return
            with path.open('rb') as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = f.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    class Server(ThreadingHTTPServer):
        allow_reuse_address = False

        def server_bind(self):
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    render_all(store)
    httpd = Server(('127.0.0.1', port), Handler)
    print(f'Audiochan Archive: http://127.0.0.1:{port}', flush=True)
    print('Данные: ' + str(store.library), flush=True)
    if open_browser:
        threading.Timer(.4, lambda: webbrowser.open(f'http://127.0.0.1:{port}')).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nСервер остановлен. Очередь и библиотека сохранены.')
    finally:
        jobs.stop.set()
        jobs.event.set()
        if jobs.thread:
            jobs.thread.join(timeout=3)
        httpd.server_close()
