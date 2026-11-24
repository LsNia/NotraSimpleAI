import sqlite3
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

TURN_COLUMNS = "request_id, question, asked_at, answer, answered_at"


@dataclass(frozen=True)
class Turn:
    request_id: str
    question: str
    asked_at: str
    answer: str
    answered_at: str


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    question TEXT NOT NULL,
                    asked_at TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    answered_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash BLOB PRIMARY KEY,
                    expires_at INTEGER NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def history(self, limit: int | None = None) -> list[Turn]:
        with self.connect() as db:
            if limit is None:
                rows = db.execute(
                    f"SELECT {TURN_COLUMNS} FROM turns ORDER BY id"
                ).fetchall()
            else:
                rows = db.execute(
                    f"SELECT {TURN_COLUMNS} FROM turns ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()[::-1]
        return [Turn(**row) for row in rows]

    def find_turn(self, request_id: str) -> Turn | None:
        with self.connect() as db:
            row = db.execute(
                f"SELECT {TURN_COLUMNS} FROM turns WHERE request_id = ?", (request_id,)
            ).fetchone()
        return Turn(**row) if row else None

    def save_turn(self, turn: Turn) -> Turn:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO turns (request_id, question, asked_at, answer, answered_at)
                VALUES (:request_id, :question, :asked_at, :answer, :answered_at)
                ON CONFLICT(request_id) DO NOTHING
            """,
                asdict(turn),
            )
            row = db.execute(
                f"SELECT {TURN_COLUMNS} FROM turns WHERE request_id = ?",
                (turn.request_id,),
            ).fetchone()
        return Turn(**row)

    def add_session(self, token_hash: bytes, now: int, lifetime: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            db.execute(
                "INSERT INTO sessions (token_hash, expires_at) VALUES (?, ?)",
                (token_hash, now + lifetime),
            )

    def has_session(self, token_hash: bytes, now: int) -> bool:
        with self.connect() as db:
            row = db.execute(
                "SELECT 1 FROM sessions WHERE token_hash = ? AND expires_at > ?",
                (token_hash, now),
            ).fetchone()
        return row is not None

    def remove_session(self, token_hash: bytes) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
