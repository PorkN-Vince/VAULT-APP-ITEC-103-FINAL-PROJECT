"""
vault/cache_engine.py
──────────────────────
Intelligent caching + perceptual-hash duplicate detection.

• Thumbnail generation with LRU memory cache + SQLite persistence
• pHash / aHash computation (pure-Pillow, no imagehash dep required)
• Hamming-distance duplicate detection
• Background worker thread with a task queue
"""

import hashlib
import io
import queue
import struct
import threading
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

from vault import database as db

THUMB_SIZE = (220, 160)
SUPPORTED_IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}
SUPPORTED_VIDEOS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}


# ─── Perceptual Hashing (pure Pillow) ─────────────────────────────────────────

def _ahash(img: Image.Image, hash_size: int = 8) -> str:
    """Average hash."""
    img = img.convert("L").resize((hash_size, hash_size), Image.LANCZOS)
    pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if p >= avg else "0" for p in pixels)
    return hex(int(bits, 2))[2:].zfill(hash_size * hash_size // 4)


def _phash(img: Image.Image, hash_size: int = 8, highfreq: int = 4) -> str:
    """DCT-based perceptual hash (simplified without scipy)."""
    img_size = hash_size * highfreq
    img = img.convert("L").resize((img_size, img_size), Image.LANCZOS)
    pixels = list(img.getdata())

    # Simple DCT approximation via row/col means (portable, no numpy needed)
    # For a proper pHash with numpy available:
    try:
        import numpy as np
        arr = np.array(pixels, dtype=float).reshape(img_size, img_size)
        # 2D DCT via FFT (good approximation)
        from numpy.fft import fft2
        dct = np.real(fft2(arr))[:hash_size, :hash_size]
        flat = dct.flatten()
        flat = flat[1:]  # exclude DC component
        avg = flat.mean()
        bits = "".join("1" if v >= avg else "0" for v in flat)
        return hex(int(bits, 2) if bits else 0)[2:].zfill(hash_size * hash_size // 4)
    except ImportError:
        # Fallback: use aHash when numpy is unavailable
        return _ahash(img, hash_size)


def hamming_distance(h1: str, h2: str) -> int:
    """Hamming distance between two hex hash strings."""
    try:
        n1 = int(h1, 16)
        n2 = int(h2, 16)
        xor = n1 ^ n2
        return bin(xor).count("1")
    except (ValueError, TypeError):
        return 999


def compute_hashes(img: Image.Image) -> tuple[str, str]:
    """Return (phash, ahash) for an image."""
    return _phash(img), _ahash(img)


# ─── Thumbnail cache ──────────────────────────────────────────────────────────

class ThumbnailCache:
    """Two-level cache: in-memory LRU + SQLite blob persistence."""

    def __init__(self, maxsize: int = 512):
        self._lock = threading.Lock()
        self._mem: dict[int, bytes] = {}
        self._maxsize = maxsize
        self._order: list[int] = []

    def get(self, media_id: int) -> Optional[bytes]:
        with self._lock:
            if media_id in self._mem:
                # LRU bump
                self._order.remove(media_id)
                self._order.append(media_id)
                return self._mem[media_id]
        # Try SQLite
        blob = db.get_thumbnail(media_id)
        if blob:
            self._put_mem(media_id, blob)
        return blob

    def put(self, media_id: int, thumb_bytes: bytes) -> None:
        self._put_mem(media_id, thumb_bytes)
        db.save_thumbnail(media_id, thumb_bytes)

    def _put_mem(self, media_id: int, data: bytes) -> None:
        with self._lock:
            if media_id in self._mem:
                self._order.remove(media_id)
            elif len(self._mem) >= self._maxsize:
                evict = self._order.pop(0)
                del self._mem[evict]
            self._mem[media_id] = data
            self._order.append(media_id)

    def invalidate(self, media_id: int) -> None:
        with self._lock:
            self._mem.pop(media_id, None)
            if media_id in self._order:
                self._order.remove(media_id)


_cache = ThumbnailCache(maxsize=512)


def generate_thumbnail(path: str, size: tuple = THUMB_SIZE) -> Optional[bytes]:
    """Open image → resize → PNG bytes."""
    try:
        img = Image.open(path)
        img.thumbnail(size, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return None


def get_or_generate_thumbnail(media_id: int, path: str,
                               size: tuple = THUMB_SIZE) -> Optional[bytes]:
    """Return cached thumbnail or generate + cache it."""
    cached = _cache.get(media_id)
    if cached:
        return cached
    blob = generate_thumbnail(path, size)
    if blob:
        _cache.put(media_id, blob)
    return blob


def invalidate_thumbnail(media_id: int) -> None:
    _cache.invalidate(media_id)


# ─── Background Worker ────────────────────────────────────────────────────────

class BackgroundWorker:
    """
    Single daemon thread consuming a priority task queue.
    Tasks: callable + optional completion callback.
    """

    def __init__(self):
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="VaultWorker")
        self._thread.start()
        self._counter = 0
        self._lock = threading.Lock()

    def _next_seq(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter

    def submit(self, fn: Callable, callback: Optional[Callable] = None,
               priority: int = 5) -> None:
        """Priority 1=high, 10=low."""
        self._queue.put((priority, self._next_seq(), fn, callback))

    def _run(self) -> None:
        while True:
            priority, seq, fn, callback = self._queue.get()
            try:
                result = fn()
                if callback:
                    callback(result)
            except Exception as e:
                print(f"[BackgroundWorker] Error: {e}")
            finally:
                self._queue.task_done()


_worker = BackgroundWorker()


def get_worker() -> BackgroundWorker:
    return _worker


# ─── Duplicate detection ──────────────────────────────────────────────────────

DUPLICATE_THRESHOLD = 10  # Hamming distance ≤ 10 → duplicate


def find_duplicates_in_db(threshold: int = DUPLICATE_THRESHOLD) -> list[list[int]]:
    """
    Compare all phashes in the database.
    Returns list of groups (each group = list of media_ids that are near-duplicates).
    O(n²) — fine for libraries up to ~10k items.
    """
    rows = db.get_conn().execute(
        "SELECT id, phash FROM media WHERE phash IS NOT NULL AND phash != ''"
    ).fetchall()

    visited = set()
    groups: list[list[int]] = []

    for i, r1 in enumerate(rows):
        if r1["id"] in visited:
            continue
        group = [r1["id"]]
        for r2 in rows[i + 1:]:
            if r2["id"] in visited:
                continue
            if hamming_distance(r1["phash"], r2["phash"]) <= threshold:
                group.append(r2["id"])
                visited.add(r2["id"])
        if len(group) > 1:
            visited.add(r1["id"])
            groups.append(group)

    return groups


# ─── Import helpers ───────────────────────────────────────────────────────────

def is_supported_media(path: str) -> tuple[bool, str]:
    """Return (is_supported, media_type)."""
    ext = Path(path).suffix.lower()
    if ext in SUPPORTED_IMAGES:
        return True, "image"
    if ext in SUPPORTED_VIDEOS:
        return True, "video"
    return False, ""


def import_file(path: str,
                album_id: Optional[int] = None,
                on_done: Optional[Callable] = None) -> None:
    """
    Queue a file for background import:
    1. Register in DB
    2. Generate thumbnail
    3. Compute hashes
    4. Optionally add to album
    5. Fire on_done(media_id)
    """
    supported, media_type = is_supported_media(path)
    if not supported:
        return

    def _do_import():
        try:
            p = Path(path)
            size = p.stat().st_size

            w, h = 0, 0
            if media_type == "image":
                try:
                    img = Image.open(path)
                    w, h = img.size
                except Exception:
                    pass

            media_id = db.upsert_media(path, media_type, size, w, h)

            # Thumbnail
            if media_type == "image":
                blob = generate_thumbnail(path)
                if blob:
                    _cache.put(media_id, blob)

                # Hashes
                try:
                    img = Image.open(path)
                    ph, ah = compute_hashes(img)
                    db.save_hashes(media_id, ph, ah)
                except Exception:
                    pass

            # Album
            if album_id is not None:
                db.add_to_album(album_id, media_id)

            return media_id
        except Exception as e:
            print(f"[import_file] {path}: {e}")
            return None

    get_worker().submit(_do_import, callback=on_done, priority=3)