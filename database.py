"""
vault/database.py
─────────────────
SQLite-backed persistence layer for Vault Gallery.
Handles: media, albums, tags, favorites, cache metadata, duplicates.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Optional


DB_PATH = Path("vault_data") / "vault.db"
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection (WAL mode for concurrency)."""
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return _local.conn


def init_db() -> None:
    """Create all tables (idempotent)."""
    conn = get_conn()
    conn.executescript("""
    -- ── Media ──────────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS media (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        path        TEXT    UNIQUE NOT NULL,
        filename    TEXT    NOT NULL,
        media_type  TEXT    NOT NULL DEFAULT 'image',   -- 'image' | 'video'
        size_bytes  INTEGER,
        width       INTEGER,
        height      INTEGER,
        duration_s  REAL,                               -- video only
        created_at  TEXT    DEFAULT (datetime('now')),
        imported_at TEXT    DEFAULT (datetime('now')),
        phash       TEXT,                               -- perceptual hash
        ahash       TEXT,                               -- average hash
        is_favorite INTEGER DEFAULT 0,
        thumbnail   BLOB                                -- cached PNG bytes
    );
    CREATE INDEX IF NOT EXISTS idx_media_path   ON media(path);
    CREATE INDEX IF NOT EXISTS idx_media_phash  ON media(phash);
    CREATE INDEX IF NOT EXISTS idx_media_type   ON media(media_type);
    CREATE INDEX IF NOT EXISTS idx_media_fav    ON media(is_favorite);

    -- ── Albums ─────────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS albums (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT    UNIQUE NOT NULL,
        description TEXT,
        cover_id    INTEGER REFERENCES media(id) ON DELETE SET NULL,
        created_at  TEXT    DEFAULT (datetime('now'))
    );

    -- ── Album ↔ Media (many-to-many) ───────────────────────────────────────
    CREATE TABLE IF NOT EXISTS album_media (
        album_id  INTEGER NOT NULL REFERENCES albums(id)  ON DELETE CASCADE,
        media_id  INTEGER NOT NULL REFERENCES media(id)   ON DELETE CASCADE,
        added_at  TEXT    DEFAULT (datetime('now')),
        PRIMARY KEY (album_id, media_id)
    );

    -- ── Tags ───────────────────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS tags (
        id    INTEGER PRIMARY KEY AUTOINCREMENT,
        name  TEXT UNIQUE NOT NULL
    );

    -- ── Media ↔ Tags (many-to-many) ────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS media_tags (
        media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
        tag_id   INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
        PRIMARY KEY (media_id, tag_id)
    );

    -- ── Duplicate groups ───────────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS duplicate_groups (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        group_hash TEXT    NOT NULL,
        created_at TEXT    DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS duplicate_members (
        group_id  INTEGER NOT NULL REFERENCES duplicate_groups(id) ON DELETE CASCADE,
        media_id  INTEGER NOT NULL REFERENCES media(id)            ON DELETE CASCADE,
        PRIMARY KEY (group_id, media_id)
    );

    -- ── Thumbnail cache metadata ────────────────────────────────────────────
    CREATE TABLE IF NOT EXISTS thumb_cache (
        media_id     INTEGER PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,
        generated_at TEXT    DEFAULT (datetime('now')),
        thumb_size   INTEGER
    );
    """)
    conn.commit()


# ─── Media CRUD ────────────────────────────────────────────────────────────────

def upsert_media(path: str, media_type: str = "image",
                 size_bytes: int = 0, width: int = 0, height: int = 0,
                 duration_s: float = 0.0) -> int:
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO media (path, filename, media_type, size_bytes, width, height, duration_s)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            size_bytes = excluded.size_bytes,
            width      = excluded.width,
            height     = excluded.height
        RETURNING id
    """, (path, Path(path).name, media_type, size_bytes, width, height, duration_s))
    row = cur.fetchone()
    conn.commit()
    return row[0]


def get_all_media(media_type: Optional[str] = None,
                  favorites_only: bool = False) -> list:
    conn = get_conn()
    q = "SELECT * FROM media WHERE 1=1"
    params: list = []
    if media_type:
        q += " AND media_type=?"; params.append(media_type)
    if favorites_only:
        q += " AND is_favorite=1"
    q += " ORDER BY imported_at DESC"
    return conn.execute(q, params).fetchall()


def get_media_by_id(media_id: int):
    return get_conn().execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()


def set_favorite(media_id: int, state: bool) -> None:
    conn = get_conn()
    conn.execute("UPDATE media SET is_favorite=? WHERE id=?", (int(state), media_id))
    conn.commit()


def save_thumbnail(media_id: int, thumb_bytes: bytes) -> None:
    conn = get_conn()
    conn.execute("UPDATE media SET thumbnail=? WHERE id=?", (thumb_bytes, media_id))
    conn.execute("""
        INSERT INTO thumb_cache (media_id, thumb_size)
        VALUES (?, ?)
        ON CONFLICT(media_id) DO UPDATE SET
            generated_at = datetime('now'),
            thumb_size   = excluded.thumb_size
    """, (media_id, len(thumb_bytes)))
    conn.commit()


def get_thumbnail(media_id: int) -> Optional[bytes]:
    row = get_conn().execute(
        "SELECT thumbnail FROM media WHERE id=?", (media_id,)
    ).fetchone()
    return row["thumbnail"] if row else None


def save_hashes(media_id: int, phash: str, ahash: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE media SET phash=?, ahash=? WHERE id=?",
                 (phash, ahash, media_id))
    conn.commit()


def search_media(query: str) -> list:
    conn = get_conn()
    q = "%" + query.lower() + "%"
    return conn.execute("""
        SELECT DISTINCT m.*
        FROM media m
        LEFT JOIN media_tags mt ON mt.media_id = m.id
        LEFT JOIN tags t        ON t.id = mt.tag_id
        WHERE lower(m.filename) LIKE ?
           OR lower(t.name)     LIKE ?
        ORDER BY m.imported_at DESC
    """, (q, q)).fetchall()


def delete_media(media_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM media WHERE id=?", (media_id,))
    conn.commit()


# ─── Albums ────────────────────────────────────────────────────────────────────

def create_album(name: str, description: str = "") -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT OR IGNORE INTO albums (name, description) VALUES (?, ?) RETURNING id",
        (name, description)
    )
    row = cur.fetchone()
    conn.commit()
    if row:
        return row[0]
    return conn.execute("SELECT id FROM albums WHERE name=?", (name,)).fetchone()["id"]


def get_all_albums() -> list:
    return get_conn().execute("""
        SELECT a.*,
               COUNT(am.media_id) AS item_count
        FROM albums a
        LEFT JOIN album_media am ON am.album_id = a.id
        GROUP BY a.id
        ORDER BY a.created_at DESC
    """).fetchall()


def get_album_media(album_id: int) -> list:
    return get_conn().execute("""
        SELECT m.*
        FROM media m
        JOIN album_media am ON am.media_id = m.id
        WHERE am.album_id = ?
        ORDER BY am.added_at DESC
    """, (album_id,)).fetchall()


def add_to_album(album_id: int, media_id: int) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO album_media (album_id, media_id) VALUES (?, ?)",
        (album_id, media_id)
    )
    conn.commit()


def remove_from_album(album_id: int, media_id: int) -> None:
    conn = get_conn()
    conn.execute(
        "DELETE FROM album_media WHERE album_id=? AND media_id=?",
        (album_id, media_id)
    )
    conn.commit()


def set_album_cover(album_id: int, media_id: int) -> None:
    conn = get_conn()
    conn.execute("UPDATE albums SET cover_id=? WHERE id=?", (media_id, album_id))
    conn.commit()


def rename_album(album_id: int, new_name: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE albums SET name=? WHERE id=?", (new_name, album_id))
    conn.commit()


def delete_album(album_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM albums WHERE id=?", (album_id,))
    conn.commit()


# ─── Tags ──────────────────────────────────────────────────────────────────────

def ensure_tag(name: str) -> int:
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name.lower().strip(),))
    conn.commit()
    return conn.execute("SELECT id FROM tags WHERE name=?",
                        (name.lower().strip(),)).fetchone()["id"]


def tag_media(media_id: int, tag_name: str) -> None:
    tag_id = ensure_tag(tag_name)
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO media_tags (media_id, tag_id) VALUES (?, ?)",
                 (media_id, tag_id))
    conn.commit()


def get_media_tags(media_id: int) -> list[str]:
    rows = get_conn().execute("""
        SELECT t.name FROM tags t
        JOIN media_tags mt ON mt.tag_id = t.id
        WHERE mt.media_id = ?
    """, (media_id,)).fetchall()
    return [r["name"] for r in rows]


def get_all_tags() -> list:
    return get_conn().execute(
        "SELECT t.*, COUNT(mt.media_id) AS usage FROM tags t "
        "LEFT JOIN media_tags mt ON mt.tag_id=t.id GROUP BY t.id ORDER BY usage DESC"
    ).fetchall()


def remove_tag_from_media(media_id: int, tag_name: str) -> None:
    conn = get_conn()
    tag = conn.execute("SELECT id FROM tags WHERE name=?",
                       (tag_name.lower().strip(),)).fetchone()
    if tag:
        conn.execute("DELETE FROM media_tags WHERE media_id=? AND tag_id=?",
                     (media_id, tag["id"]))
        conn.commit()


# ─── Duplicates ────────────────────────────────────────────────────────────────

def find_duplicate_candidates() -> list[list]:
    """Return groups of media sharing the same phash (hamming=0 exact match)."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT phash, GROUP_CONCAT(id) AS ids, COUNT(*) AS cnt
        FROM media
        WHERE phash IS NOT NULL AND phash != ''
        GROUP BY phash
        HAVING cnt > 1
    """).fetchall()
    groups = []
    for row in rows:
        ids = [int(i) for i in row["ids"].split(",")]
        groups.append(ids)
    return groups