from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from .client import Client
from .model import urls_in, identify, make_entry, audio_files, image_urls, now, plain
from .store import atomic_json


class Jobs:
    def __init__(self, store, start=True):
        self.store = store
        self.file = store.data / 'queue.json'
        self.lock = threading.RLock()
        self.event = threading.Event()
        self.stop = threading.Event()
        self.paused = False
        try:
            self.tasks = json.loads(self.file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.tasks = []
        for t in self.tasks:
            if t['stage'] in ('reading', 'downloading'):
                t['stage'] = 'queued' if not t.get('entry_key') else 'media'
                t['progress'] = ''
        self.persist()
        self.thread = None
        if start:
            self.thread = threading.Thread(target=self.run, daemon=True, name='archive-worker')
            self.thread.start()
            self.event.set()

    def persist(self):
        atomic_json(self.file, self.tasks)

    def snapshot(self):
        with self.lock:
            return {'paused': self.paused, 'items': json.loads(json.dumps(self.tasks))}

    def status(self, limit=0, offset=0):
        with self.lock:
            pending = sum(t['stage'] in ('queued', 'reading', 'media', 'downloading') for t in self.tasks)
            active = next((dict(t) for t in self.tasks if t['stage'] in ('reading', 'downloading')), None)
            signature = hashlib.sha256(''.join(t['id'] + ':' + t['stage'] for t in self.tasks).encode()).hexdigest()[:20]
            end = max(0, len(self.tasks) - offset)
            items = self.tasks[max(0, end - limit):end] if limit else []
            return {'paused': self.paused, 'items': json.loads(json.dumps(items)), 'total': len(self.tasks), 'pending': pending, 'active': active, 'signature': signature}

    def add(self, text, media=True):
        urls = urls_in(text)
        added = 0
        with self.lock:
            existing = {t['url']: t for t in self.tasks}
            for url in urls:
                if url in existing:
                    continue
                entry = self.store.by_url(url)
                task = {'id': hashlib.sha256(url.encode()).hexdigest()[:24], 'url': url,
                        'kind': identify(url)[0], 'stage': 'queued', 'media': bool(media),
                        'entry_key': entry['key'] if entry else None, 'added_at': now(),
                        'error': '', 'title': entry['title'] if entry else url, 'progress': ''}
                self.tasks.append(task)
                existing[url] = task
                added += 1
            self.persist()
            self.event.set()
        return {'found': len(urls), 'added': added, 'duplicates': len(urls) - added}

    def change(self, action, task_id=None):
        with self.lock:
            if action == 'pause':
                self.paused = True
            elif action == 'resume':
                self.paused = False
            elif action in ('retry', 'media', 'refresh'):
                for task in self.tasks:
                    if task_id and task['id'] != task_id:
                        continue
                    if task['stage'] in ('reading', 'downloading'):
                        continue
                    if action == 'retry' and not task.get('error'):
                        continue
                    task['stage'] = 'queued' if action == 'refresh' or not task.get('entry_key') else 'media'
                    if action == 'media':
                        task['media'] = True
                    task['error'] = ''
                    task['progress'] = ''
            else:
                raise ValueError('Неизвестное действие очереди')
            self.persist()
            self.event.set()

    def update(self, task, **values):
        with self.lock:
            task.update(values)
            self.persist()

    def fetch_metadata(self, task, client):
        self.update(task, stage='reading', progress='Получаю текст и сведения о карточке')
        kind, slug, url = identify(task['url'])
        raw = client.metadata(url)
        entry = make_entry(kind, slug, url, raw)
        old = self.store.get(entry['key'])
        if old:
            for name in ('media', 'images', 'state', 'saved_at'):
                entry[name] = old.get(name, entry.get(name))
        entry = self.store.save(entry, raw)
        self.update(task, entry_key=entry['key'], title=entry['title'],
                    stage='media' if task['media'] else 'done', progress='', error='')

    def fetch_media(self, task, client):
        entry = self.store.get(task['entry_key'])
        if not entry:
            self.update(task, entry_key=None, stage='queued')
            return
        self.update(task, stage='downloading', progress='Проверяю вложения')
        source = self.store.path(entry['folder']) / 'source.json'
        raw = json.loads(source.read_text(encoding='utf-8'))
        folder = self.store.path(entry['folder']) / 'media'
        errors = []
        if entry['kind'] == 'audio':
            files = audio_files(raw)
            if not files:
                errors.append('В карточке не найдены доступные аудиофайлы')
            for file in files:
                file_id = str(file['id'])
                old = next((m for m in entry['media'] if m.get('id') == file_id and self.store.path(m['path']).is_file()), None)
                if old:
                    old.update(label=file.get('label', 'Аудио'), variant_ids=file.get('variant_ids', []))
                    entry = self.store.save(entry)
                    continue
                try:
                    url = client.mint(entry['id'], file_id)
                    def progress(count, total):
                        self.update(task, progress=f"Аудио: {count / 1048576:.1f} / {total / 1048576:.1f} МБ")
                    result = client.download(url, folder / file_id, audio=True, expected_duration=file.get('duration') or 0, progress=progress)
                    result['path'] = result['path'].relative_to(self.store.library).as_posix()
                    result.update(id=file_id, label=file.get('label', 'Аудио'), variant_ids=file.get('variant_ids', []), original_filename=file.get('filename', ''), saved_at=now())
                    entry['media'] = [m for m in entry['media'] if m.get('id') != file_id] + [result]
                    entry = self.store.save(entry)
                except Exception as exc:
                    errors.append(f'Аудио {file_id}: {safe_error(exc)}')
        for url in image_urls(raw):
            if any(m.get('url') == url and self.store.path(m['path']).is_file() for m in entry['images']):
                continue
            try:
                self.update(task, progress='Сохраняю иллюстрации')
                result = client.download(url, folder / hashlib.sha256(url.encode()).hexdigest()[:24])
                result['path'] = result['path'].relative_to(self.store.library).as_posix()
                result.update(url=url, saved_at=now())
                entry['images'].append(result)
                entry = self.store.save(entry)
            except Exception as exc:
                errors.append('Иллюстрация: ' + safe_error(exc))
        entry['error'] = '; '.join(errors)
        entry['state'] = 'partial' if errors else 'complete'
        entry = self.store.save(entry)
        self.update(task, stage='error' if errors else 'done', error=entry['error'], progress='')

    def next_task(self):
        # All queued text is saved before any pending media job.
        for stage in ('queued', 'media'):
            for task in self.tasks:
                if task['stage'] == stage:
                    return task
        return None

    def run(self):
        client = Client()
        while not self.stop.is_set():
            with self.lock:
                task = None if self.paused else self.next_task()
            if not task:
                self.event.wait(1)
                self.event.clear()
                continue
            try:
                if task['stage'] == 'queued':
                    self.fetch_metadata(task, client)
                else:
                    self.fetch_media(task, client)
            except Exception as exc:
                error = safe_error(exc)
                if task.get('entry_key'):
                    entry = self.store.get(task['entry_key'])
                    if entry:
                        entry.update(error=error, state='partial')
                        self.store.save(entry)
                self.update(task, stage='error', error=error, progress='')


def safe_error(exc):
    # Do not copy signed URLs or cookie values from third-party exceptions to UI/logs.
    from .client import SiteError
    if isinstance(exc, (SiteError, ValueError, FileNotFoundError)):
        return str(exc)[:600]
    return f'Ошибка {type(exc).__name__}. Можно повторить; сохранённые данные остаются на диске.'
