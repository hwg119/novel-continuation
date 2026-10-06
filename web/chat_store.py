"""Durable local conversation journal. Event IDs are scoped to a session."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class ChatStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL,
                    chapter INTEGER, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL,
                    kind TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS chat_events_session ON events(session, seq);
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, data TEXT NOT NULL,
                    status TEXT NOT NULL, job TEXT);
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(sessions)')}
            for name in ('renamed', 'deleted'):
                if name not in columns:
                    db.execute(f'ALTER TABLE sessions ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
            # In-flight calls cannot survive a backend restart.
            for row in db.execute("SELECT id,session FROM turns WHERE status='running'").fetchall():
                self._event(db, row[1], 'interrupted', {'turn_id': row[0],
                    'text': '服务重启，上一条回复已中断；历史内容已保留。'})
            db.execute("UPDATE turns SET status='interrupted' WHERE status='running'")
            for row in db.execute("SELECT id,session FROM actions WHERE status='starting'").fetchall():
                self._event(db,row[1],'action_status',{'action_id':row[0],'status':'interrupted',
                    'text':'服务重启，操作是否已启动无法确认；请查看运行记录，勿直接重复执行。'})
            db.execute("UPDATE actions SET status='interrupted' WHERE status='starting'")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    @staticmethod
    def _event(db, session, kind, data):
        cursor = db.execute('INSERT INTO events(session,kind,data,created) VALUES(?,?,?,?)',
                            (session, kind, json.dumps(data, ensure_ascii=False), time.time()))
        return cursor.lastrowid

    def emit(self, session, kind, data):
        with self.db() as db: return self._event(db, session, kind, data)

    def create(self, project, chapter=None):
        ident = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO sessions(id,project,title,chapter,created) VALUES(?,?,?,?,?)',
                       (ident, project, '新的创作对话', chapter, time.time()))
        return ident

    def sessions(self, project):
        with self.db() as db:
            return [dict(row) for row in db.execute(
                'SELECT * FROM sessions WHERE project=? AND deleted=0 ORDER BY created DESC', (project,))]

    def get(self, project, session):
        with self.db() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=? AND project=? AND deleted=0', (session, project)).fetchone()
            return dict(row) if row else None

    def rename(self, project, session, title):
        title = title.strip()
        if not title or len(title) > 80:
            raise ValueError('对话名称需为 1～80 个字符')
        with self.db() as db:
            changed = db.execute('UPDATE sessions SET title=?,renamed=1 WHERE id=? AND project=? AND deleted=0',
                                 (title, session, project)).rowcount
            if not changed: raise ValueError('对话不存在')

    def delete(self, project, session):
        # Soft deletion preserves a recoverable journal and never removes novel files.
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM sessions WHERE id=? AND project=? AND deleted=0',
                              (session, project)).fetchone(): raise ValueError('对话不存在')
            if db.execute("SELECT 1 FROM turns WHERE session=? AND status='running'", (session,)).fetchone() or db.execute(
                    "SELECT 1 FROM actions WHERE session=? AND status='starting'", (session,)).fetchone():
                raise ValueError('对话仍在回复或启动任务，请等待完成或停止回复后删除')
            db.execute('UPDATE sessions SET deleted=1 WHERE id=?', (session,))

    def events(self, session, after=0):
        with self.db() as db:
            return [dict(id=row['seq'], kind=row['kind'], data=json.loads(row['data']),
                         time=time.strftime('%H:%M:%S', time.localtime(row['created'])))
                    for row in db.execute('SELECT * FROM events WHERE session=? AND seq>? ORDER BY seq',
                                          (session, after))]

    def begin(self, session, text, chapter):
        ident = uuid.uuid4().hex
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM sessions WHERE id=? AND deleted=0', (session,)).fetchone():
                raise ValueError('对话不存在')
            if db.execute("SELECT 1 FROM turns WHERE session=? AND status='running'", (session,)).fetchone():
                raise ValueError('当前会话仍在回复，请等待完成或停止回复')
            db.execute('INSERT INTO turns VALUES(?,?,?)', (ident, session, 'running'))
            db.execute('UPDATE sessions SET chapter=?,title=CASE WHEN renamed=1 THEN title ELSE ? END WHERE id=?', (chapter, text[:28], session))
            self._event(db, session, 'user', {'turn_id': ident, 'text': text, 'chapter': chapter})
        return ident

    def running(self, turn):
        with self.db() as db:
            row = db.execute('SELECT status FROM turns WHERE id=?', (turn,)).fetchone()
            return bool(row and row[0] == 'running')

    def finish(self, session, turn, status, text=''):
        with self.db() as db:
            changed = db.execute("UPDATE turns SET status=? WHERE id=? AND session=? AND status='running'",
                                 (status, turn, session)).rowcount
            if changed: self._event(db, session, status, {'turn_id': turn, 'text': text})

    def action(self, session, data):
        ident = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO actions VALUES(?,?,?,?,?)',
                       (ident, session, json.dumps(data, ensure_ascii=False), 'pending', None))
            self._event(db, session, 'action', {'action_id': ident, **data})
        return ident

    def claim(self, session, ident):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM sessions WHERE id=? AND deleted=0', (session,)).fetchone():
                raise ValueError('对话不存在')
            row = db.execute('SELECT * FROM actions WHERE id=? AND session=?', (ident, session)).fetchone()
            if not row: raise ValueError('操作不存在')
            if row['status'] != 'pending': raise ValueError('该操作已处理，请勿重复执行')
            db.execute("UPDATE actions SET status='starting' WHERE id=?", (ident,))
            return json.loads(row['data'])

    def get_action(self, session, ident):
        with self.db() as db:
            row = db.execute('SELECT * FROM actions WHERE id=? AND session=?', (ident, session)).fetchone()
            return {**dict(row), 'data': json.loads(row['data'])} if row else None

    def actions(self, session):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM actions WHERE session=?', (session,))]

    def action_status(self, session, ident, status, job=None):
        with self.db() as db:
            db.execute('UPDATE actions SET status=?,job=? WHERE id=? AND session=?',
                       (status, job, ident, session))
            self._event(db, session, 'action_status', {'action_id': ident, 'status': status, 'job_id': job})
