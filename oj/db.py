"""
OJ 数据库 schema 初始化
运行：python3 -I oj/db.py init
"""
import sqlite3, os, sys, hashlib, secrets

DB_PATH = os.environ.get("OJ_DB", os.path.join(os.path.dirname(__file__), "oj.db"))


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    token       TEXT UNIQUE NOT NULL,       -- Bearer 认证 token
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS submissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id      INTEGER REFERENCES teams(id),
    phase        TEXT NOT NULL DEFAULT 'A', -- A / B
    submitted_at TEXT DEFAULT (datetime('now')),
    status       TEXT DEFAULT 'queued',     -- queued / running / done / error
    score_F2     REAL,
    score_F3     REAL,
    score_F6     REAL,
    combined     REAL,
    error_msg    TEXT,
    policy_path  TEXT,                      -- 上传的策略文件路径
    label        TEXT
);

-- 每队每阶段最多保留的 displayed 记录（取 combined 最高的一条用于排行榜）
CREATE INDEX IF NOT EXISTS idx_sub_team_phase ON submissions(team_id, phase);
CREATE INDEX IF NOT EXISTS idx_sub_status ON submissions(status);
"""

# 增量迁移：后加的列用 ALTER TABLE（忽略已存在错误）
_MIGRATIONS = [
    "ALTER TABLE submissions ADD COLUMN viz_path TEXT",
]


def _migrate(conn):
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # 列已存在


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    print(f"[db] 初始化完成: {DB_PATH}")
    return conn


def create_team(name: str) -> dict:
    token = secrets.token_hex(20)
    conn = get_conn()
    try:
        cur = conn.execute("INSERT INTO teams (name, token) VALUES (?, ?)", (name, token))
        conn.commit()
        tid = cur.lastrowid
        print(f"[db] 创建队伍: {name}  token={token}")
        return {"id": tid, "name": name, "token": token}
    except sqlite3.IntegrityError:
        row = conn.execute("SELECT id, name, token FROM teams WHERE name=?", (name,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def enqueue(team_id: int, phase: str, policy_path: str, label: str = "") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO submissions (team_id, phase, policy_path, label) VALUES (?,?,?,?)",
        (team_id, phase, policy_path, label)
    )
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def get_leaderboard(phase: str = "A") -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT t.name, s.combined, s.score_F2, s.score_F3, s.score_F6,
               s.submitted_at, s.label, s.id
        FROM submissions s
        JOIN teams t ON t.id = s.team_id
        WHERE s.phase = ? AND s.status = 'done' AND s.combined IS NOT NULL
        GROUP BY s.team_id
        HAVING s.id = (
            SELECT id FROM submissions s2
            WHERE s2.team_id = s.team_id AND s2.phase = ? AND s2.status = 'done'
            ORDER BY s2.combined DESC LIMIT 1
        )
        ORDER BY s.combined DESC
    """, (phase, phase)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_submission(sid: int) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT s.*, t.name as team_name FROM submissions s JOIN teams t ON t.id=s.team_id WHERE s.id=?",
        (sid,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def set_result(sid: int, scores: dict, error: str | None = None, viz_path: str | None = None):
    conn = get_conn()
    if error:
        conn.execute(
            "UPDATE submissions SET status='error', error_msg=? WHERE id=?",
            (error, sid)
        )
    else:
        conn.execute("""
            UPDATE submissions
            SET status='done', score_F2=?, score_F3=?, score_F6=?, combined=?, viz_path=?
            WHERE id=?
        """, (scores["F2"], scores["F3"], scores["F6"], scores["combined"], viz_path, sid))
    conn.commit()
    conn.close()


def set_running(sid: int):
    conn = get_conn()
    conn.execute("UPDATE submissions SET status='running' WHERE id=?", (sid,))
    conn.commit()
    conn.close()


def next_queued() -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM submissions WHERE status='queued' ORDER BY id LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_teams() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM teams ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_team(team_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM submissions WHERE team_id=?", (team_id,))
    conn.execute("DELETE FROM teams WHERE id=?", (team_id,))
    conn.commit()
    conn.close()


def get_all_submissions(limit: int = 100) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT s.*, t.name as team_name FROM submissions s
        JOIN teams t ON t.id = s.team_id
        ORDER BY s.id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        init_db()
    elif len(sys.argv) > 2 and sys.argv[1] == "addteam":
        init_db()
        t = create_team(sys.argv[2])
        print(f"  token: {t['token']}")
    else:
        print("用法: python3 oj/db.py init")
        print("      python3 oj/db.py addteam <队名>")
