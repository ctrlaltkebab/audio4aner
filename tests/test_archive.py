import hashlib
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from archive.client import Client, SiteError
from archive.jobs import Jobs
from archive.model import normalize, urls_in, make_entry, person, rich, audio_files
from archive.store import Store

ROOT = Path(__file__).resolve().parents[1]
AUDIO_URL = 'https://audiochan.com/a/ExampleAudio'


@pytest.fixture
def raw():
    return {'id': 'audio-test-id', 'slug': 'ExampleAudio', 'title': 'Музыка 100% _',
            'description': {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Сохранённый текст'}]}]},
            'tags': [{'name': 'One', 'category': 'content'}, {'name': 'F4M', 'category': 'audience'}],
            'audioFile': {'id': 'file1', 'duration': '10', 'mime_type': 'audio/mp4'},
            'credits': [{'role': 'voice', 'display_name': 'First', 'user': {'id': 'user-1', 'username': 'First', 'display_name': 'First'}}]}


def test_links():
    urls = ['https://audiochan.com/a/ExampleAudio', 'https://audiochan.com/s/Text', 'https://audiochan.com/u/Seven_Veils_']
    assert urls_in('\n'.join(urls + [urls[0] + '?c=comment'])) == urls
    assert normalize(urls[0] + '/?c=1#test') == urls[0]
    assert normalize('https://other.example/a/ExampleAudio') is None
    assert normalize('https://audiochan.com/a/ExampleAudio/extra') is None


def test_durable_files_rebuild_and_people(tmp_path, raw):
    store = Store(tmp_path / 'first')
    entry = store.save(make_entry('audio', 'ExampleAudio', AUDIO_URL, raw), raw)
    assert store.get(person(raw['credits'][0])['key'])
    page = store.path(entry['folder']) / 'page.html'
    assert 'author/' in page.read_text(encoding='utf-8')
    store.position(entry['key'], 5)
    moved = tmp_path / 'moved'
    shutil.copytree(store.library, moved / 'library')
    fresh = Store(moved)
    assert fresh.get(entry['key'])['title'] == raw['title']
    assert fresh.query(person_key='author:user-1')['total'] == 1
    assert fresh.query(q='МУЗЫКА')['total'] == 1
    assert fresh.query(q='% _')['total'] == 1
    assert fresh.query(q='Nonexistent')['total'] == 0
    assert fresh.query(tags=['One'], exclude=['F4M'])['total'] == 0
    assert store.user_data()['positions'][entry['key']] == 5
    with pytest.raises(ValueError):
        fresh.path('../private/secret')


def test_queue_prioritizes_metadata_and_survives_media_error(tmp_path, raw):
    store = Store(tmp_path)
    jobs = Jobs(store, start=False)
    jobs.add(AUDIO_URL + '\nhttps://audiochan.com/s/Text')
    class FakeClient:
        def metadata(self, url):
            return raw
        def mint(self, *args):
            raise SiteError('Временно нет файла')
    first = jobs.tasks[0]
    jobs.fetch_metadata(first, FakeClient())
    assert jobs.next_task() is jobs.tasks[1]
    jobs.fetch_media(first, FakeClient())
    assert first['stage'] == 'error'
    entry = store.get(first['entry_key'])
    assert entry['state'] == 'partial'
    assert (store.path(entry['folder']) / 'source.json').exists()
    assert (store.path(entry['folder']) / 'README.md').exists()
    assert entry['description'] == raw['description']
    jobs.change('retry', first['id'])
    assert first['stage'] == 'media'
    jobs.update(first, stage='downloading')
    restored = Jobs(store, start=False)
    assert restored.tasks[0]['stage'] == 'media'


def test_rich_text_has_no_executable_html():
    doc = {'type': 'doc', 'content': [{'type': 'text', 'text': '<script>alert(1)</script>', 'marks': [{'type': 'link', 'attrs': {'href': 'javascript:alert(1)'}}]}]}
    result = rich(doc)
    assert '<script>' not in result
    assert 'javascript:' not in result
    assert '&lt;script&gt;' in result


def test_large_queue_pages_do_not_hide_pending_totals(tmp_path):
    jobs = Jobs(Store(tmp_path), start=False)
    jobs.add('\n'.join('https://audiochan.com/a/Track' + str(i) for i in range(205)))
    first = jobs.status(limit=100)
    second = jobs.status(limit=100, offset=100)
    assert first['total'] == first['pending'] == 205
    assert len(first['items']) == len(second['items']) == 100
    assert not {t['id'] for t in first['items']} & {t['id'] for t in second['items']}
    assert jobs.status()['items'] == []
    assert len(jobs.status(limit=100, offset=200)['items']) == 5


def test_download_rejects_error_instead_of_audio(tmp_path):
    class Response:
        status_code = 200
        headers = {'content-type': 'text/html', 'content-length': '10'}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    class Session:
        def stream(self, *args, **kwargs):
            return Response()
    client = Client()
    client.session = Session()
    with pytest.raises(SiteError, match='Вместо аудио'):
        client.download('https://stream.audiochan.com/', tmp_path / 'audio', audio=True)
    assert not list(tmp_path.iterdir())


@pytest.fixture
def server(tmp_path):
    root = tmp_path / 'server'
    store = Store(root)
    shutil.copytree(ROOT / 'web', root / 'web')
    media = store.library / 'sample.m4a'
    media.write_bytes(bytes(range(256)) * 20)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    process = subprocess.Popen([sys.executable, '-m', 'archive', 'serve', '--root', str(root), '--port', str(port), '--no-open'], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f'http://127.0.0.1:{port}'
    for _ in range(80):
        try:
            with urllib.request.urlopen(base + '/api/bootstrap', timeout=.3) as r:
                bootstrap = json.load(r)
            break
        except (OSError, urllib.error.URLError):
            if process.poll() is not None:
                pytest.fail(process.stderr.read().decode(errors='replace'))
            time.sleep(.05)
    else:
        process.terminate()
        pytest.fail('Local server did not start')
    yield base, bootstrap['token'], media
    process.terminate()
    process.wait(timeout=5)


def test_local_range_security_and_zero_network_view(server):
    base, token, media = server
    with urllib.request.urlopen(urllib.request.Request(base+'/files/sample.m4a', headers={'Range':'bytes=100-199'})) as r:
        assert r.status == 206
        assert r.headers['Content-Range'] == 'bytes 100-199/5120'
        assert r.read() == media.read_bytes()[100:200]
    with urllib.request.urlopen(urllib.request.Request(base+'/files/sample.m4a', headers={'Range':'bytes=-20'})) as r:
        assert r.read() == media.read_bytes()[-20:]
    for suffix, code in [('/files/sample.m4a',416),('/files/../../private/instance.lock',400),('/private/instance.lock',404)]:
        headers={'Range':'bytes=999999-'} if code==416 else {}
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(urllib.request.Request(base+suffix,headers=headers))
        assert exc.value.code==code
    request=urllib.request.Request(base+'/api/rebuild',data=b'{}',headers={'Content-Type':'application/json'})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert exc.value.code==403
    request.add_header('X-Archive-Token',token)
    with urllib.request.urlopen(request) as r:
        assert json.load(r)['ok']
    with urllib.request.urlopen(base+'/') as r:
        text=r.read().decode()
        assert 'Audiochan Archive' in text
        assert 'https://' not in text


def test_instance_lock(server):
    _, _, media = server
    root = media.parents[1]
    proc = subprocess.run([sys.executable,'-m','archive','rebuild','--root',str(root)],cwd=ROOT,capture_output=True)
    assert proc.returncode==2


def test_all_variants_are_preserved_and_retried_individually(tmp_path, raw):
    raw['variants'] = [
        {'id': 'variant1', 'audio_file_id': 'file1', 'label': 'With SFX', 'order': 0, 'audioFile': raw['audioFile']},
        {'id': 'variant2', 'audio_file_id': 'file2', 'label': 'No added sfx', 'order': 1, 'audioFile': dict(raw['audioFile'], id='file2')},
    ]
    manifest = audio_files(raw)
    assert [(f['id'], f['label']) for f in manifest] == [('file1','With SFX'), ('file2','No added sfx')]
    assert manifest[0]['variant_ids'] == ['variant1']
    jobs = Jobs(Store(tmp_path), start=False)
    jobs.add(AUDIO_URL)
    class FakeClient:
        failed = False
        downloaded = []
        def metadata(self, url):
            return raw
        def mint(self, audio_id, file_id):
            return 'https://stream.audiochan.com/' + file_id
        def download(self, url, base, **kwargs):
            file_id = url.rsplit('/', 1)[1]
            if file_id == 'file2' and not self.failed:
                self.failed = True
                raise SiteError('Временная ошибка второй версии')
            self.downloaded.append(file_id)
            base.parent.mkdir(parents=True, exist_ok=True)
            path = base.with_suffix('.m4a')
            path.write_bytes(b'audio')
            return {'path': path, 'bytes': 5, 'duration': 10, 'sha256': hashlib.sha256(b'audio').hexdigest()}
    client = FakeClient()
    task = jobs.tasks[0]
    jobs.fetch_metadata(task, client)
    assert len(jobs.store.get(task['entry_key'])['expected_media']) == 2
    jobs.fetch_media(task, client)
    entry = jobs.store.get(task['entry_key'])
    assert entry['state'] == 'partial' and len(entry['media']) == 1
    jobs.fetch_media(task, client)
    entry = jobs.store.get(task['entry_key'])
    assert entry['state'] == 'complete' and len(entry['media']) == 2
    assert client.downloaded == ['file1', 'file2']
