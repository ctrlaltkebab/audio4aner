from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from .model import plain, person, safe_name, now


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.library = self.root / 'library'
        self.data = self.root / 'data'
        self.lock = threading.RLock()
        for p in (self.library, self.data, self.root / 'private', self.root / 'var'):
            p.mkdir(parents=True, exist_ok=True)
        self.dbfile = self.data / 'index.sqlite'
        try:
            self.init_db()
        except sqlite3.DatabaseError:
            self.dbfile.replace(self.data / ('index-damaged-' + now().replace(':', '-') + '.sqlite'))
            self.init_db()
        self.rebuild()

    def connect(self):
        db = sqlite3.connect(self.dbfile, timeout=30)
        db.row_factory = sqlite3.Row
        db.create_function('casefold', 1, lambda value: str(value or '').casefold())
        return db

    def init_db(self):
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS entries (
                key TEXT PRIMARY KEY, kind TEXT, title TEXT, description TEXT,
                state TEXT, saved_at TEXT, folder TEXT, source_url TEXT, payload TEXT);
              CREATE INDEX IF NOT EXISTS entries_kind ON entries(kind, saved_at);
              CREATE TABLE IF NOT EXISTS tags (entry_key TEXT, name TEXT, category TEXT);
              CREATE INDEX IF NOT EXISTS tags_name ON tags(name, entry_key);
              CREATE TABLE IF NOT EXISTS credits (entry_key TEXT, person_key TEXT, role TEXT, name TEXT);
              CREATE INDEX IF NOT EXISTS credits_person ON credits(person_key, entry_key);
            ''')

    def path(self, relative):
        path = (self.library / relative).resolve()
        if not path.is_relative_to(self.library.resolve()):
            raise ValueError('Путь выходит за пределы библиотеки')
        return path

    def _index(self, db, entry):
        db.execute('INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?,?,?,?)', (
            entry['key'], entry['kind'], entry['title'], plain(entry.get('description')) + '\n' + plain(entry.get('body')),
            entry['state'], entry['saved_at'], entry['folder'], entry.get('source_url', ''), json.dumps(entry, ensure_ascii=False)))
        db.execute('DELETE FROM tags WHERE entry_key=?', (entry['key'],))
        db.execute('DELETE FROM credits WHERE entry_key=?', (entry['key'],))
        for tag in entry.get('tags', []):
            db.execute('INSERT INTO tags VALUES (?,?,?)', (entry['key'], tag.get('name', ''), tag.get('category', 'content')))
        for c in entry.get('credits', []):
            p = person(c)
            db.execute('INSERT INTO credits VALUES (?,?,?,?)', (entry['key'], p['key'], c.get('role') or c.get('custom_role') or 'participant', p['title']))

    def rebuild(self):
        with self.lock, self.connect() as db:
            db.execute('DELETE FROM entries')
            db.execute('DELETE FROM tags')
            db.execute('DELETE FROM credits')
            for path in self.library.glob('*/*/entry.json'):
                try:
                    entry = json.loads(path.read_text(encoding='utf-8'))
                    entry['folder'] = path.parent.relative_to(self.library).as_posix()
                    self._index(db, entry)
                except (OSError, ValueError, KeyError, TypeError):
                    continue

    def get(self, key):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM entries WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def by_url(self, url):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM entries WHERE source_url=?', (url,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, entry, raw=None, sync_people=True):
        from .render import write_entry, write_index
        with self.lock:
            old = self.get(entry['key'])
            entry = dict(entry)
            if old:
                entry['folder'] = old['folder']
                entry['saved_at'] = old['saved_at']
            else:
                folder = f"{entry['kind']}/{safe_name(entry.get('slug') or entry['id'])} - {safe_name(entry['title'])}"
                # IDs, not display names alone, distinguish authors with linked profiles.
                if entry['kind'] == 'author':
                    folder = f"author/{safe_name(entry['id'])} - {safe_name(entry['title'])}"
                entry['folder'] = folder
            entry['updated_at'] = now()
            folder = self.path(entry['folder'])
            folder.mkdir(parents=True, exist_ok=True)
            if raw is not None:
                atomic_json(folder / 'source.json', raw)
            atomic_json(folder / 'entry.json', entry)
            with self.connect() as db:
                self._index(db, entry)
            write_entry(self, entry)
            if sync_people:
                for credit in entry.get('credits', []):
                    p = person(credit)
                    previous = self.get(p['key'])
                    if not previous:
                        previous = self.save(p, raw=credit, sync_people=False)
                    profile_images = [m for m in entry.get('images', []) if m.get('url') in ((p.get('profile') or {}).get('avatar'), (p.get('profile') or {}).get('background'))]
                    if profile_images:
                        previous['images'] = list({m['url']: m for m in previous.get('images', []) + profile_images}.values())
                        previous = self.save(previous, sync_people=False)
                    write_entry(self, previous)
                # First write preceded automatic author creation; resolve links now.
                write_entry(self, entry)
            write_index(self)
            return entry

    def query(self, kind='audio', q='', tags=None, exclude=None, person_key='', state='', offset=0, limit=36):
        clauses, params = ['e.kind=?'], [kind]
        if q:
            # Escape LIKE metacharacters: search means a literal substring.
            term = '%' + q.casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            clauses.append("(casefold(e.title) LIKE ? ESCAPE '\\' OR casefold(e.description) LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM credits c WHERE c.entry_key=e.key AND casefold(c.name) LIKE ? ESCAPE '\\'))")
            params.extend([term] * 3)
        for tag in tags or []:
            clauses.append('EXISTS(SELECT 1 FROM tags t WHERE t.entry_key=e.key AND t.name=?)')
            params.append(tag)
        for tag in exclude or []:
            clauses.append('NOT EXISTS(SELECT 1 FROM tags t WHERE t.entry_key=e.key AND t.name=?)')
            params.append(tag)
        if person_key:
            clauses.append('EXISTS(SELECT 1 FROM credits c WHERE c.entry_key=e.key AND c.person_key=?)')
            params.append(person_key)
        if state:
            clauses.append('e.state=?')
            params.append(state)
        where = ' AND '.join(clauses)
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM entries e WHERE ' + where, params).fetchone()[0]
            rows = db.execute('SELECT payload FROM entries e WHERE ' + where + ' ORDER BY saved_at DESC, key LIMIT ? OFFSET ?', params + [min(100, limit), max(0, offset)]).fetchall()
        return {'items': [json.loads(r[0]) for r in rows], 'total': total}

    def info(self):
        with self.connect() as db:
            counts = dict(db.execute('SELECT kind,COUNT(*) FROM entries GROUP BY kind').fetchall())
            tags = [dict(r) for r in db.execute('SELECT name,category,COUNT(DISTINCT entry_key) AS count FROM tags GROUP BY name,category ORDER BY count DESC,name')]
        return {'counts': counts, 'tags': tags}

    def user_data(self):
        try:
            return json.loads((self.data / 'user.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {'positions': {}}

    def position(self, key, value):
        with self.lock:
            data = self.user_data()
            data.setdefault('positions', {})[key] = value
            atomic_json(self.data / 'user.json', data)
