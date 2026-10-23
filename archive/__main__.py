import argparse
import time
import sys
from pathlib import Path
from .store import Store
from .jobs import Jobs
from .render import render_all


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Локальный архив Audiochan')
    parser.add_argument('command', nargs='?', default='serve', choices=['serve', 'import', 'download', 'rebuild', 'verify'])
    parser.add_argument('input', nargs='?', help='TXT со ссылками или URL')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--text-only', action='store_true')
    args = parser.parse_args()
    from .instance import InstanceLock
    try:
        instance = InstanceLock(args.root)
    except OSError:
        print('Этот архив уже открыт другой программой. Используйте существующее окно или сначала остановите сервер.')
        raise SystemExit(2)
    store = Store(args.root)
    if args.command == 'rebuild':
        render_all(store)
        print('Индекс и автономные страницы восстановлены из JSON.')
        return
    if args.command == 'verify':
        import hashlib
        failures = 0
        for path in store.library.glob('*/*/entry.json'):
            import json
            entry = json.loads(path.read_text(encoding='utf-8'))
            for item in entry.get('media', []) + entry.get('images', []):
                file = store.path(item['path'])
                good = file.is_file() and file.stat().st_size == item['bytes']
                if good:
                    with file.open('rb') as f:
                        good = hashlib.file_digest(f, 'sha256').hexdigest() == item['sha256']
                if not good:
                    failures += 1
                    print('Повреждён или отсутствует: ' + item['path'])
        print(f'Проверка завершена. Проблем: {failures}')
        raise SystemExit(1 if failures else 0)
    jobs = Jobs(store)
    if args.command == 'serve':
        from .server import serve
        serve(store, jobs, args.port, not args.no_open)
        return
    if args.command == 'import':
        if not args.input:
            parser.error('Укажите TXT-файл или URL')
        text = args.input if args.input.startswith('https://') else Path(args.input).read_text(encoding='utf-8-sig')
        print(jobs.add(text, media=not args.text_only))
    if args.command == 'download':
        jobs.change('retry')
        jobs.change('media')
    try:
        while any(t['stage'] in ('queued', 'reading', 'media', 'downloading') for t in jobs.snapshot()['items']):
            time.sleep(.4)
    except KeyboardInterrupt:
        print('Прервано. Очередь сохранена.')
    finally:
        jobs.stop.set()
        jobs.event.set()
        if jobs.thread:
            jobs.thread.join(timeout=3)
    for task in jobs.snapshot()['items']:
        print(task['stage'], task['title'], task.get('error') or '')
    render_all(store)
    if any(t['stage'] == 'error' for t in jobs.snapshot()['items']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
