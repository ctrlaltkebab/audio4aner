from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urlsplit, quote
from curl_cffi import requests
from mutagen import File as AudioInfo

from .model import identify

API = 'https://api.audiochan.com'
ORIGIN = 'https://audiochan.com'


class SiteError(Exception):
    pass


def allowed(url):
    u = urlsplit(url)
    return u.scheme == 'https' and (u.hostname == 'audiochan.com' or (u.hostname or '').endswith('.audiochan.com')) and not u.username


class Client:
    def __init__(self):
        self.session = requests.Session(impersonate='chrome', timeout=45)

    def json(self, method, path, payload=None):
        for attempt in range(3):
            r = self.session.request(method, API + path, json=payload, headers={'Origin': ORIGIN, 'Referer': ORIGIN + '/'})
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < 2:
                    time.sleep(min(10, 2 ** attempt))
                    continue
            if r.status_code != 200:
                if r.status_code in (401, 403):
                    raise SiteError('Сайт требует вход или проверку доступа. Карточка и очередь сохранены.')
                raise SiteError(f'Сайт вернул HTTP {r.status_code}')
            data = r.json()
            if isinstance(data, dict) and data.get('loadingHold'):
                raise SiteError('Сайт временно не отдаёт карточку; повторите позже')
            return data
        raise SiteError('Сайт временно недоступен')

    def metadata(self, url):
        kind, slug, _ = identify(url)
        path = {'audio': '/audios/slug/', 'script': '/scripts/', 'author': '/users/'}[kind]
        return self.json('GET', path + quote(slug, safe=''))

    def mint(self, audio_id, file_id):
        path = '/audios/' + quote(audio_id, safe='')
        params = {'file_id': file_id} if file_id else {}
        intent = self.json('POST', path + '/play-intent', params)
        data = self.json('POST', path + '/stream-url', dict(params, play_intent=intent['play_intent']))
        if not allowed(data.get('url', '')):
            raise SiteError('Неожиданный адрес аудиосервера')
        return data['url']

    def download(self, url, base: Path, audio=False, expected_duration=0, progress=None):
        if not allowed(url):
            raise SiteError('Внешнее вложение оставлено ссылкой: автоматическая загрузка поддерживает домены Audiochan')
        headers = {'Origin': ORIGIN, 'Referer': ORIGIN + '/', 'Sec-Fetch-Site': 'same-site', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'audio' if audio else 'image'}
        if audio:
            # The playback service requires a media Range request, even from byte zero.
            headers['Range'] = 'bytes=0-'
        part = base.with_suffix('.part')
        base.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.session.stream('GET', url, headers=headers, timeout=300) as r:
                if r.status_code not in (200, 206):
                    raise SiteError(f'Загрузка файла: HTTP {r.status_code}. Метаданные сохранены; можно повторить докачивание.')
                mime = r.headers.get('content-type', '').split(';')[0].lower()
                if audio and not (mime.startswith('audio/') or mime in ('video/mp4', 'application/octet-stream')):
                    raise SiteError('Вместо аудио получен ответ другого типа')
                if not audio and mime not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/avif'):
                    raise SiteError('Формат иллюстрации не поддерживается для локального просмотра')
                expected = int(r.headers.get('content-length', 0))
                content_range = r.headers.get('content-range', '')
                if r.status_code == 206 and not re.fullmatch(r'bytes 0-\d+/\d+', content_range):
                    raise SiteError('Сервер вернул неожиданный диапазон файла')
                if content_range and int(content_range.rsplit('/', 1)[1]) != expected:
                    raise SiteError('Сервер вернул только часть файла; полная копия пока не получена')
                count = 0
                digest = hashlib.sha256()
                last = 0
                with part.open('wb') as f:
                    for chunk in r.iter_content(chunk_size=256 * 1024):
                        f.write(chunk)
                        digest.update(chunk)
                        count += len(chunk)
                        if progress and time.monotonic() - last > .6:
                            progress(count, expected)
                            last = time.monotonic()
                if count == 0 or (expected and count != expected):
                    raise SiteError('Файл загрузился не полностью')
            duration = 0
            if audio:
                info = AudioInfo(part)
                if not info or not getattr(info, 'info', None):
                    raise SiteError('Скачанный файл не распознан как аудио')
                duration = float(info.info.length)
                if expected_duration and abs(duration - float(expected_duration)) > max(3, float(expected_duration) * .01):
                    raise SiteError('Длительность файла не соответствует карточке')
                detected = info.__class__.__name__
                extension = {'MP4': '.m4a', 'MP3': '.mp3', 'WAVE': '.wav', 'FLAC': '.flac', 'OggOpus': '.opus', 'OggVorbis': '.ogg', 'AAC': '.aac'}.get(detected)
                if not extension:
                    raise SiteError('Неизвестный контейнер аудио: ' + detected)
            else:
                extension = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif', 'image/avif': '.avif'}[mime]
            path = base.with_suffix(extension)
            part.replace(path)
            return {'path': path, 'bytes': count, 'sha256': digest.hexdigest(), 'mime': mime, 'duration': duration}
        except Exception:
            # A failed .part never becomes a playable library file.
            raise
