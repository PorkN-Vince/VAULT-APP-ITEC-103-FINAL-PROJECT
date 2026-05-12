"""
vault/views.py
───────────────
All major view panels for Vault Gallery.

Views:
  AllMediaView    – full library grid with smart search
  AlbumsView      – album grid
  AlbumDetailView – photos inside one album
  FavoritesView   – favorited items
  DuplicatesView  – detected near-duplicate groups
  MediaViewer     – full-screen lightbox (image + video info)
  TagManagerView  – browse + filter by tag
  TimelineView    – media grouped chronologically by month/year
"""

import io
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from typing import Callable, Optional

import customtkinter as ctk
from PIL import Image, ImageTk

from vault import cache_engine, database as db
from vault import theme as th
from vault.widgets import (
    AlbumCard, LazyGrid, MediaCard, SearchBar, TagChip,
    CARD_W, CARD_H,
)

# ── Color helpers — always read live from theme so light/dark works ────────────
def _T():
    return th.get()

def _c(key):
    """Shortcut: get a color from the active theme."""
    return th.get()[key]

# Keep these as fallback constants for widgets that don't rebuild on theme change
ACCENT  = "#7C6AF7"
ACCENT2 = "#A594F9"
SURFACE = "#1A1A2E"
SURFACE2= "#16213E"
TEXT    = "#E2E0FF"
SUBTEXT = "#9896B8"
FAV_ON  = "#FF6B9D"
DANGER  = "#FF4D6D"

# ── Shared popup helpers ───────────────────────────────────────────────────────

def _ask_text(parent, title: str, label: str, initial: str = "") -> Optional[str]:
    return simpledialog.askstring(title, label, initialvalue=initial, parent=parent)


def _confirm(parent, msg: str) -> bool:
    return messagebox.askyesno("Confirm", msg, parent=parent)


def _error(parent, msg: str):
    messagebox.showerror("Error", msg, parent=parent)


# ─── Tag popup ────────────────────────────────────────────────────────────────

class TagPopup(ctk.CTkToplevel):
    def __init__(self, parent, media_id: int, on_changed: Optional[Callable] = None):
        super().__init__(parent)
        self.media_id = media_id
        self.on_changed = on_changed
        self.title("Manage Tags")
        self.geometry("340x280")
        self.resizable(False, False)
        self.configure(fg_color=SURFACE)
        self._build()
        self.after(100, self._refresh_chips)

    def _build(self):
        ctk.CTkLabel(self, text="Tags", font=("Courier", 14, "bold"),
                     text_color=TEXT).pack(pady=(16, 6))
        self._chip_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._chip_frame.pack(fill="x", padx=16)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(pady=12, padx=16, fill="x")
        self._entry = ctk.CTkEntry(row, placeholder_text="Add tag…",
                                   fg_color=SURFACE2, text_color=TEXT,
                                   border_color="#2A2850", font=("Courier", 12))
        self._entry.pack(side="left", expand=True, fill="x")
        self._entry.bind("<Return>", lambda _: self._add_tag())
        ctk.CTkButton(row, text="Add", width=60, fg_color=ACCENT,
                      hover_color=ACCENT2, font=("Courier", 11),
                      command=self._add_tag).pack(side="left", padx=(6, 0))

        # All-tag suggestions
        all_tags = [t["name"] for t in db.get_all_tags()]
        if all_tags:
            ctk.CTkLabel(self, text="Suggestions", font=("Courier", 10),
                         text_color=SUBTEXT).pack(anchor="w", padx=16)
            sug = ctk.CTkFrame(self, fg_color="transparent")
            sug.pack(fill="x", padx=16, pady=4)
            for t in all_tags[:8]:
                TagChip(sug, t,
                        on_click=lambda tag: self._apply_suggestion(tag)
                        ).pack(side="left", padx=2, pady=2)

    def _refresh_chips(self):
        for w in self._chip_frame.winfo_children():
            w.destroy()
        for tag in db.get_media_tags(self.media_id):
            TagChip(self._chip_frame, tag,
                    on_remove=self._remove_tag).pack(side="left", padx=2, pady=2)

    def _add_tag(self):
        name = self._entry.get().strip()
        if name:
            db.tag_media(self.media_id, name)
            self._entry.delete(0, "end")
            self._refresh_chips()
            if self.on_changed:
                self.on_changed()

    def _remove_tag(self, tag: str):
        db.remove_tag_from_media(self.media_id, tag)
        self._refresh_chips()
        if self.on_changed:
            self.on_changed()

    def _apply_suggestion(self, tag: str):
        db.tag_media(self.media_id, tag)
        self._refresh_chips()


# ─── MediaViewer (Lightbox) ────────────────────────────────────────────────────

class MediaViewer(ctk.CTkToplevel):
    """Full-screen lightbox for image / video media."""

    def __init__(self, parent, media_id: int, media_list: list):
        super().__init__(parent)
        self.title("Vault — Viewer")
        self.geometry("1100x750")
        self.configure(fg_color=_c("SURFACE"))
        self.resizable(True, True)

        self._media_list = [r["id"] for r in media_list]
        self._idx = self._media_list.index(media_id) if media_id in self._media_list else 0
        self._ctk_img = None

        self._build()
        self._load_current()
        self.bind("<Left>",  lambda _: self._nav(-1))
        self.bind("<Right>", lambda _: self._nav(1))
        self.bind("<Escape>", lambda _: self.destroy())

    def _build(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=10)

        self._title_lbl = ctk.CTkLabel(top, text="", font=("Courier", 13, "bold"),
                                        text_color=TEXT)
        self._title_lbl.pack(side="left")

        nav = ctk.CTkFrame(top, fg_color="transparent")
        nav.pack(side="right")
        ctk.CTkButton(nav, text="←", width=40, fg_color=SURFACE2,
                      hover_color=SURFACE, text_color=TEXT,
                      command=lambda: self._nav(-1)).pack(side="left", padx=4)
        ctk.CTkButton(nav, text="→", width=40, fg_color=SURFACE2,
                      hover_color=SURFACE, text_color=TEXT,
                      command=lambda: self._nav(1)).pack(side="left")

        self._img_label = ctk.CTkLabel(self, text="Loading…", text_color=SUBTEXT)
        self._img_label.pack(expand=True, fill="both", padx=20)

        # Bottom info bar
        bottom = ctk.CTkFrame(self, fg_color=_c("SURFACE2"), corner_radius=0)
        bottom.pack(fill="x", side="bottom")

        self._info_lbl = ctk.CTkLabel(bottom, text="", font=("Courier", 10),
                                       text_color=_c("SUBTEXT"))
        self._info_lbl.pack(side="left", padx=16, pady=8)

        self._fav_btn = ctk.CTkButton(
            bottom, text="♡", width=36, height=30,
            fg_color="transparent", hover_color=SURFACE,
            text_color="#4A4870", font=("Arial", 18),
            command=self._toggle_fav
        )
        self._fav_btn.pack(side="right", padx=12, pady=4)

        ctk.CTkButton(bottom, text="Open in Explorer", width=140, height=30,
                      fg_color=_c("SURFACE"), hover_color=_c("SURFACE2"),
                      text_color=_c("ACCENT2"), font=("Courier", 10),
                      command=self._open_in_explorer).pack(side="right", padx=6, pady=4)

        self._tag_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        self._tag_frame.pack(side="left", padx=8, pady=6)

    def _current_row(self):
        return db.get_media_by_id(self._media_list[self._idx])

    def _load_current(self):
        row = self._current_row()
        if not row:
            return
        self._title_lbl.configure(text=row["filename"])
        info = f"{row['width']}×{row['height']}  |  {row['size_bytes']//1024} KB  |  {row['media_type']}"
        self._info_lbl.configure(text=info)
        is_fav = bool(row["is_favorite"])
        self._fav_btn.configure(
            text="♥" if is_fav else "♡",
            text_color=FAV_ON if is_fav else "#4A4870"
        )
        self._load_tags(row["id"])

        if row["media_type"] == "image":
            img_path = row["path"]
            def _do():
                try:
                    img = Image.open(img_path).convert("RGB")
                    img.thumbnail((1024, 768), Image.LANCZOS)
                    return img
                except Exception as e:
                    print(f"[MediaViewer] {e}")
                    return None
            def _apply(img):
                if img and self.winfo_exists():
                    try:
                        ctkimg = ctk.CTkImage(
                            light_image=img, dark_image=img,
                            size=(img.width, img.height)
                        )
                        self._ctk_img = ctkimg
                        self.after(0, lambda: self._img_label.configure(image=ctkimg, text=""))
                    except Exception as e:
                        print(f"[MediaViewer apply] {e}")
            cache_engine.get_worker().submit(_do, callback=_apply, priority=1)
        else:
            self._img_label.configure(
                text=f"▶  Video: {row['filename']}\n\nDouble-click to open in system player",
                image=None
            )
            self._img_label.bind("<Double-Button-1>", lambda _: self._open_in_explorer())

    def _load_tags(self, media_id: int):
        for w in self._tag_frame.winfo_children():
            w.destroy()
        for tag in db.get_media_tags(media_id):
            TagChip(self._tag_frame, tag).pack(side="left", padx=2)

    def _nav(self, direction: int):
        self._idx = (self._idx + direction) % len(self._media_list)
        self._img_label.configure(image=None, text="Loading…")
        self._load_current()

    def _toggle_fav(self):
        row = self._current_row()
        if row:
            new = not bool(row["is_favorite"])
            db.set_favorite(row["id"], new)
            self._fav_btn.configure(
                text="♥" if new else "♡",
                text_color=FAV_ON if new else "#4A4870"
            )

    def _open_in_explorer(self):
        row = self._current_row()
        if not row:
            return
        path = row["path"]
        if sys.platform == "win32":
            subprocess.run(["explorer", "/select,", path])
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", path])
        else:
            subprocess.run(["xdg-open", os.path.dirname(path)])


# ─── AllMediaView ─────────────────────────────────────────────────────────────

class AllMediaView(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._current_rows: list = []
        self._filter = "all"
        self._build()
        self.refresh()

    def _build(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=4, pady=(0, 8))

        ctk.CTkLabel(top, text="All Media",
                     font=("Courier", 22, "bold"), text_color=TEXT).pack(side="left")

        # Filter tabs
        tab_frame = ctk.CTkFrame(top, fg_color="transparent")
        tab_frame.pack(side="left", padx=20)
        for label, val in [("All", "all"), ("Images", "image"), ("Videos", "video")]:
            ctk.CTkButton(tab_frame, text=label, width=70, height=28,
                          fg_color=SURFACE2, hover_color=SURFACE,
                          text_color=TEXT, font=("Courier", 10),
                          corner_radius=8,
                          command=lambda v=val: self._set_filter(v)
                          ).pack(side="left", padx=3)

        right = ctk.CTkFrame(top, fg_color="transparent")
        right.pack(side="right")

        self._search = SearchBar(right, on_search=self._on_search)
        self._search.pack(side="left")

        ctk.CTkButton(right, text="+ Import", width=90, height=36,
                      fg_color=ACCENT, hover_color=ACCENT2,
                      font=("Courier", 11, "bold"), corner_radius=10,
                      command=self._import_files).pack(side="left", padx=8)

        self._count_lbl = ctk.CTkLabel(self, text="", font=("Courier", 10),
                                        text_color=_c("SUBTEXT"))
        self._count_lbl.pack(anchor="w", padx=4, pady=(0, 4))

        self._grid = LazyGrid(
            self,
            on_media_click=self._open_viewer,
            on_tag=self._open_tag_popup,
            on_delete=self._delete_media,
            on_favorite_change=lambda *_: None
        )
        self._grid.pack(fill="both", expand=True)

    def _set_filter(self, val: str):
        self._filter = val
        self.refresh()

    def _on_search(self, query: str):
        if query.strip():
            rows = db.search_media(query)
        else:
            rows = db.get_all_media(
                media_type=None if self._filter == "all" else self._filter
            )
        self._current_rows = rows
        self._grid.load(rows)
        self._count_lbl.configure(text=f"{len(rows)} item{'s' if len(rows)!=1 else ''}")

    def refresh(self):
        rows = db.get_all_media(
            media_type=None if self._filter == "all" else self._filter
        )
        self._current_rows = rows
        self._grid.load(rows)
        self._count_lbl.configure(text=f"{len(rows)} item{'s' if len(rows)!=1 else ''}")

    def _import_files(self):
        paths = filedialog.askopenfilenames(
            title="Import Media",
            filetypes=[
                ("All supported", "*.jpg *.jpeg *.png *.gif *.bmp *.webp *.mp4 *.avi *.mov *.mkv"),
                ("Images", "*.jpg *.jpeg *.png *.gif *.bmp *.webp"),
                ("Videos", "*.mp4 *.avi *.mov *.mkv"),
            ]
        )
        for p in paths:
            cache_engine.import_file(p, on_done=lambda _: self.after(500, self.refresh))

    def _open_viewer(self, media_id: int):
        MediaViewer(self, media_id, self._current_rows)

    def _open_tag_popup(self, media_id: int):
        TagPopup(self, media_id)

    def _delete_media(self, media_id: int):
        if _confirm(self, "Delete this item from the library? (File on disk is kept.)"):
            db.delete_media(media_id)
            self._grid.remove_card(media_id)
            self._current_rows = [r for r in self._current_rows if r["id"] != media_id]
            self._count_lbl.configure(text=f"{len(self._current_rows)} items")


# ─── AlbumsView ───────────────────────────────────────────────────────────────

class AlbumsView(ctk.CTkFrame):
    def __init__(self, parent, on_open_album: Callable, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self.on_open_album = on_open_album
        self._build()
        self.refresh()

    def _build(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=4, pady=(0, 12))

        ctk.CTkLabel(top, text="Albums",
                     font=("Courier", 22, "bold"), text_color=TEXT).pack(side="left")
        ctk.CTkButton(top, text="+ New Album", width=110, height=36,
                      fg_color=ACCENT, hover_color=ACCENT2,
                      font=("Courier", 11, "bold"), corner_radius=10,
                      command=self._create_album).pack(side="right")

        self._grid = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._grid.pack(fill="both", expand=True)

    def refresh(self):
        for w in self._grid.winfo_children():
            w.destroy()
        albums = db.get_all_albums()
        cols = 4
        for i, album in enumerate(albums):
            r, c = divmod(i, cols)
            card = AlbumCard(
                self._grid, album,
                on_click=self.on_open_album,
                on_rename=self._rename_album,
                on_delete=self._delete_album
            )
            card.grid(row=r, column=c, padx=8, pady=8, sticky="nw")

    def _create_album(self):
        name = _ask_text(self, "New Album", "Album name:")
        if name and name.strip():
            db.create_album(name.strip())
            self.refresh()

    def _rename_album(self, album_id: int):
        albums = db.get_all_albums()
        cur = next((a["name"] for a in albums if a["id"] == album_id), "")
        new_name = _ask_text(self, "Rename Album", "New name:", initial=cur)
        if new_name and new_name.strip():
            db.rename_album(album_id, new_name.strip())
            self.refresh()

    def _delete_album(self, album_id: int):
        if _confirm(self, "Delete this album? (Media files are kept in the library.)"):
            db.delete_album(album_id)
            self.refresh()


# ─── AlbumDetailView ──────────────────────────────────────────────────────────

class AlbumDetailView(ctk.CTkFrame):
    def __init__(self, parent, album_id: int, on_back: Callable, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self.album_id = album_id
        self.on_back = on_back
        self._current_rows: list = []
        self._build()
        self.refresh()

    def _build(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=4, pady=(0, 8))

        ctk.CTkButton(top, text="← Albums", width=90, height=32,
                      fg_color=SURFACE2, hover_color=SURFACE,
                      text_color=ACCENT2, font=("Courier", 11),
                      corner_radius=8, command=self.on_back).pack(side="left")

        albums = db.get_all_albums()
        album = next((a for a in albums if a["id"] == self.album_id), None)
        name = album["name"] if album else "Album"

        ctk.CTkLabel(top, text=name,
                     font=("Courier", 20, "bold"), text_color=TEXT).pack(side="left", padx=12)

        ctk.CTkButton(top, text="+ Add Media", width=100, height=32,
                      fg_color=ACCENT, hover_color=ACCENT2,
                      font=("Courier", 10, "bold"), corner_radius=8,
                      command=self._add_media).pack(side="right")

        self._grid = LazyGrid(
            self,
            on_media_click=self._open_viewer,
            on_tag=lambda mid: TagPopup(self, mid),
            on_delete=self._remove_from_album
        )
        self._grid.pack(fill="both", expand=True)

    def refresh(self):
        rows = db.get_album_media(self.album_id)
        self._current_rows = rows
        self._grid.load(rows)
        # Set first image as album cover
        if rows and rows[0]["media_type"] == "image":
            db.set_album_cover(self.album_id, rows[0]["id"])

    def _open_viewer(self, media_id: int):
        MediaViewer(self, media_id, self._current_rows)

    def _add_media(self):
        paths = filedialog.askopenfilenames(
            title="Add to Album",
            filetypes=[("Images & Videos",
                        "*.jpg *.jpeg *.png *.gif *.bmp *.webp *.mp4 *.avi *.mov *.mkv")]
        )
        for p in paths:
            cache_engine.import_file(
                p, album_id=self.album_id,
                on_done=lambda _: self.after(600, self.refresh)
            )

    def _remove_from_album(self, media_id: int):
        if _confirm(self, "Remove from album? (Stays in library.)"):
            db.remove_from_album(self.album_id, media_id)
            self._grid.remove_card(media_id)
            self._current_rows = [r for r in self._current_rows if r["id"] != media_id]


# ─── FavoritesView ────────────────────────────────────────────────────────────

class FavoritesView(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._rows: list = []
        self._build()
        self.refresh()

    def _build(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=4, pady=(0, 8))
        ctk.CTkLabel(top, text="♥  Favorites",
                     font=("Courier", 22, "bold"), text_color=FAV_ON).pack(side="left")

        self._grid = LazyGrid(
            self,
            on_media_click=self._open_viewer,
            on_tag=lambda mid: TagPopup(self, mid),
            on_delete=self._unfavorite,
            on_favorite_change=lambda mid, state: self.refresh() if not state else None
        )
        self._grid.pack(fill="both", expand=True)

    def refresh(self):
        self._rows = db.get_all_media(favorites_only=True)
        self._grid.load(self._rows)

    def _open_viewer(self, media_id: int):
        MediaViewer(self, media_id, self._rows)

    def _unfavorite(self, media_id: int):
        db.set_favorite(media_id, False)
        self.refresh()


# ─── DuplicatesView ───────────────────────────────────────────────────────────

class DuplicatesView(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._build()

    def _build(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=4, pady=(0, 12))
        ctk.CTkLabel(top, text="Duplicate Detector",
                     font=("Courier", 22, "bold"), text_color=TEXT).pack(side="left")
        ctk.CTkButton(top, text="Scan Now", width=100, height=36,
                      fg_color=ACCENT, hover_color=ACCENT2,
                      font=("Courier", 11, "bold"), corner_radius=10,
                      command=self._scan).pack(side="right")

        self._status = ctk.CTkLabel(self, text="Press 'Scan Now' to detect near-duplicates.",
                                     font=("Courier", 11), text_color=_c("SUBTEXT"))
        self._status.pack(pady=10)

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True)

    def _scan(self):
        self._status.configure(text="Scanning… (this may take a moment)")
        for w in self._scroll.winfo_children():
            w.destroy()

        def _do():
            return cache_engine.find_duplicates_in_db()

        def _done(groups):
            if not groups:
                self.after(0, lambda: self._status.configure(
                    text="✓ No near-duplicates found."
                ))
                return
            self.after(0, lambda: self._render_groups(groups))

        cache_engine.get_worker().submit(_do, callback=_done, priority=2)

    def _render_groups(self, groups: list[list[int]]):
        self._status.configure(text=f"Found {len(groups)} duplicate group(s).")
        for g_idx, group in enumerate(groups):
            grp_frame = ctk.CTkFrame(self._scroll, fg_color=SURFACE2,
                                      corner_radius=10)
            grp_frame.pack(fill="x", padx=8, pady=6)
            ctk.CTkLabel(grp_frame, text=f"Group {g_idx+1}  ({len(group)} files)",
                         font=("Courier", 11, "bold"), text_color=ACCENT2
                         ).pack(anchor="w", padx=12, pady=(8, 4))
            row_f = ctk.CTkFrame(grp_frame, fg_color="transparent")
            row_f.pack(fill="x", padx=8, pady=(0, 8))
            for mid in group:
                media = db.get_media_by_id(mid)
                if media:
                    card = MediaCard(
                        row_f, media,
                        on_click=lambda m=mid: MediaViewer(self, m, [media]),
                        on_delete=self._delete_one
                    )
                    card.pack(side="left", padx=6, pady=4)

    def _delete_one(self, media_id: int):
        if _confirm(self, "Remove this item from the library?"):
            db.delete_media(media_id)
            self._scan()


# ─── TagManagerView ───────────────────────────────────────────────────────────

class TagManagerView(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._rows: list = []
        self._active_tag: Optional[str] = None
        self._build()
        self.refresh()

    def _build(self):
        pane = ctk.CTkFrame(self, fg_color="transparent")
        pane.pack(fill="both", expand=True)

        # Left: tag list
        left = ctk.CTkFrame(pane, width=200, fg_color=SURFACE2, corner_radius=12)
        left.pack(side="left", fill="y", padx=(0, 8), pady=4)
        left.pack_propagate(False)

        ctk.CTkLabel(left, text="Tags", font=("Courier", 14, "bold"),
                     text_color=TEXT).pack(pady=12)

        self._tag_scroll = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self._tag_scroll.pack(fill="both", expand=True, padx=4)

        # Right: media in tag
        right = ctk.CTkFrame(pane, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True)

        self._tag_title = ctk.CTkLabel(right, text="Select a tag",
                                        font=("Courier", 18, "bold"), text_color=TEXT)
        self._tag_title.pack(anchor="w", pady=(4, 8))

        self._grid = LazyGrid(right,
                               on_media_click=lambda mid: MediaViewer(self, mid, self._rows),
                               on_tag=lambda mid: TagPopup(self, mid, on_changed=self.refresh))
        self._grid.pack(fill="both", expand=True)

    def refresh(self):
        for w in self._tag_scroll.winfo_children():
            w.destroy()
        for tag in db.get_all_tags():
            btn = ctk.CTkButton(
                self._tag_scroll,
                text=f"#{tag['name']}  ({tag['usage']})",
                height=30, anchor="w",
                fg_color="transparent",
                hover_color=SURFACE,
                text_color=ACCENT2,
                font=("Courier", 11),
                corner_radius=6,
                command=lambda t=tag["name"]: self._load_tag(t)
            )
            btn.pack(fill="x", pady=2, padx=4)
        if self._active_tag:
            self._load_tag(self._active_tag)

    def _load_tag(self, tag: str):
        self._active_tag = tag
        self._tag_title.configure(text=f"#{tag}")
        rows = db.search_media(tag)
        self._rows = rows
        self._grid.load(rows)


# ─── TimelineView ─────────────────────────────────────────────────────────────

class TimelineView(ctk.CTkFrame):
    """
    Chronological timeline: media grouped by Month + Year,
    sorted newest-first. Each group shows a header with the
    date label and item count, then a horizontal strip of thumbnails.
    Clicking any thumbnail opens the full MediaViewer lightbox.
    A filter bar lets you jump to a specific year quickly.
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._all_rows: list = []
        self._grouped: dict = {}        # "January 2025" → [rows]
        self._active_year: str = "All"
        self._build()
        self.refresh()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        # ── Header bar ────────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=4, pady=(0, 6))

        ctk.CTkLabel(
            header, text="🕓  Timeline",
            font=("Courier", 22, "bold"), text_color=TEXT
        ).pack(side="left")

        self._count_lbl = ctk.CTkLabel(
            header, text="", font=("Courier", 10), text_color=SUBTEXT
        )
        self._count_lbl.pack(side="left", padx=14)

        ctk.CTkButton(
            header, text="↺ Refresh", width=90, height=32,
            fg_color=SURFACE2, hover_color=SURFACE,
            text_color=ACCENT2, font=("Courier", 10),
            corner_radius=8, command=self.refresh
        ).pack(side="right")

        # ── Year filter bar ───────────────────────────────────────────────────
        self._year_bar = ctk.CTkFrame(self, fg_color=SURFACE2, corner_radius=10)
        self._year_bar.pack(fill="x", padx=4, pady=(0, 10))

        self._year_btns: dict[str, ctk.CTkButton] = {}
        self._build_year_bar()

        # ── Scrollable timeline body ───────────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True)

    def _build_year_bar(self):
        for w in self._year_bar.winfo_children():
            w.destroy()
        self._year_btns.clear()

        years = self._get_years()
        all_years = ["All"] + years

        for y in all_years:
            is_active = (y == self._active_year)
            btn = ctk.CTkButton(
                self._year_bar,
                text=y,
                width=60, height=28,
                fg_color=ACCENT if is_active else "transparent",
                hover_color=SURFACE,
                text_color=TEXT if is_active else SUBTEXT,
                font=("Courier", 10, "bold" if is_active else "normal"),
                corner_radius=8,
                command=lambda yr=y: self._filter_year(yr)
            )
            btn.pack(side="left", padx=4, pady=4)
            self._year_btns[y] = btn

    def _get_years(self) -> list[str]:
        """Extract distinct years from imported_at, newest first."""
        seen, result = set(), []
        for row in self._all_rows:
            yr = self._parse_date(row["imported_at"])[1]   # year string
            if yr not in seen:
                seen.add(yr)
                result.append(yr)
        return result

    # ── Data ──────────────────────────────────────────────────────────────────

    def refresh(self):
        self._all_rows = db.get_all_media()          # newest first
        self._grouped  = self._group_by_month(self._all_rows)
        self._build_year_bar()
        self._render()

    def _group_by_month(self, rows: list) -> dict:
        """Return OrderedDict: 'Month YYYY' → [rows], newest group first."""
        from collections import OrderedDict
        groups: dict[str, list] = OrderedDict()
        for row in rows:
            month, year = self._parse_date(row["imported_at"])
            key = f"{month} {year}"
            groups.setdefault(key, []).append(row)
        return groups

    @staticmethod
    def _parse_date(dt_str: str) -> tuple[str, str]:
        """
        Parse 'YYYY-MM-DD HH:MM:SS' → ('January', '2025').
        Falls back gracefully on bad data.
        """
        MONTHS = [
            "January","February","March","April","May","June",
            "July","August","September","October","November","December"
        ]
        try:
            parts = (dt_str or "").split(" ")[0].split("-")
            year  = parts[0]
            month = MONTHS[int(parts[1]) - 1]
            return month, year
        except Exception:
            return "Unknown", "????"

    def _filter_year(self, year: str):
        self._active_year = year
        # Re-highlight buttons
        for y, btn in self._year_btns.items():
            is_active = (y == year)
            btn.configure(
                fg_color=ACCENT if is_active else "transparent",
                text_color=TEXT if is_active else SUBTEXT,
                font=("Courier", 10, "bold" if is_active else "normal"),
            )
        self._render()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self):
        for w in self._scroll.winfo_children():
            w.destroy()

        visible_count = 0
        for label, rows in self._grouped.items():
            # Year filter
            if self._active_year != "All":
                if not label.endswith(self._active_year):
                    continue

            visible_count += len(rows)
            self._render_group(label, rows)

        total = len(self._all_rows)
        shown = visible_count
        self._count_lbl.configure(
            text=f"{shown} of {total} item{'s' if total != 1 else ''}"
        )

        if visible_count == 0:
            ctk.CTkLabel(
                self._scroll,
                text="No media found for this period.\nImport some files from the Library view.",
                font=("Courier", 12), text_color=SUBTEXT, justify="center"
            ).pack(expand=True, pady=60)

    def _render_group(self, label: str, rows: list):
        """Render one month-group: header + horizontal thumbnail strip."""

        # ── Group header ──────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self._scroll, fg_color="transparent")
        hdr.pack(fill="x", padx=6, pady=(16, 4))

        # Vertical timeline dot + line
        dot_col = ctk.CTkFrame(hdr, width=24, fg_color="transparent")
        dot_col.pack(side="left", fill="y")
        dot_col.pack_propagate(False)

        ctk.CTkFrame(
            dot_col, width=12, height=12,
            fg_color=ACCENT, corner_radius=6
        ).place(x=6, y=8)

        ctk.CTkFrame(
            dot_col, width=2,
            fg_color="#2A2850", corner_radius=1
        ).place(x=11, y=20, relheight=1.0)

        # Label + count
        text_col = ctk.CTkFrame(hdr, fg_color="transparent")
        text_col.pack(side="left", padx=(6, 0), fill="x", expand=True)

        ctk.CTkLabel(
            text_col, text=label,
            font=("Courier", 14, "bold"), text_color=ACCENT2,
            anchor="w"
        ).pack(anchor="w")

        n = len(rows)
        ctk.CTkLabel(
            text_col,
            text=f"{n} item{'s' if n != 1 else ''}",
            font=("Courier", 9), text_color=SUBTEXT, anchor="w"
        ).pack(anchor="w")

        # ── Horizontal thumbnail strip ─────────────────────────────────────────
        strip_outer = ctk.CTkFrame(
            self._scroll,
            fg_color=SURFACE2, corner_radius=10
        )
        strip_outer.pack(fill="x", padx=6, pady=(0, 4))

        # Horizontally scrollable inner frame
        strip = ctk.CTkScrollableFrame(
            strip_outer, fg_color="transparent",
            orientation="horizontal", height=150
        )
        strip.pack(fill="x", padx=8, pady=8)

        for row in rows:
            self._render_thumb(strip, row)

    def _render_thumb(self, parent, row):
        """One mini-card in the horizontal strip."""
        MINI_W, MINI_H = 120, 100

        card = ctk.CTkFrame(
            parent,
            width=MINI_W, height=MINI_H + 22,
            fg_color="#12102A", corner_radius=8,
            border_width=1, border_color="#2A2850"
        )
        card.pack(side="left", padx=5, pady=4)
        card.pack_propagate(False)

        # Thumb label
        lbl = ctk.CTkLabel(
            card, text="⋯", width=MINI_W, height=MINI_H,
            text_color=SUBTEXT, fg_color="transparent"
        )
        lbl.place(x=0, y=0)

        # Async thumbnail load
        media_id = row["id"]
        path     = row["path"]

        def _fetch():
            import io as _io
            from vault.cache_engine import get_or_generate_thumbnail
            blob = get_or_generate_thumbnail(media_id, path,
                                             size=(MINI_W, MINI_H))
            return blob

        def _apply(blob):
            if blob and card.winfo_exists():
                try:
                    import io as _io
                    img = Image.open(_io.BytesIO(blob)).resize(
                        (MINI_W, MINI_H), Image.LANCZOS
                    )
                    ctkimg = ctk.CTkImage(
                        light_image=img, dark_image=img,
                        size=(MINI_W, MINI_H)
                    )
                    card.after(0, lambda: lbl.configure(image=ctkimg, text=""))
                except Exception:
                    pass

        from vault.cache_engine import get_worker
        get_worker().submit(_fetch, callback=_apply, priority=6)

        # Filename label
        fname = row["filename"]
        if len(fname) > 14:
            fname = fname[:12] + "…"
        ctk.CTkLabel(
            card, text=fname,
            font=("Courier", 8), text_color=SUBTEXT,
            width=MINI_W, anchor="center"
        ).place(x=0, y=MINI_H)

        # Video badge
        if row["media_type"] == "video":
            ctk.CTkLabel(
                card, text="▶", width=20, height=16,
                font=("Arial", 9), text_color="white",
                fg_color=ACCENT, corner_radius=3
            ).place(x=4, y=4)

        # Favorite dot
        if row["is_favorite"]:
            ctk.CTkLabel(
                card, text="♥", width=16, height=16,
                font=("Arial", 9), text_color=FAV_ON,
                fg_color="transparent"
            ).place(x=MINI_W - 20, y=4)

        # Hover + click
        def _on_enter(_):
            card.configure(border_color=ACCENT)

        def _on_leave(_):
            card.configure(border_color="#2A2850")

        def _on_click(_):
            # Open viewer with all rows in this group as navigation context
            # We pass the full library list so ← → still works across months
            all_rows = self._all_rows
            MediaViewer(self, media_id, all_rows)

        for w in [card, lbl]:
            w.bind("<Enter>",    _on_enter)
            w.bind("<Leave>",    _on_leave)
            w.bind("<Button-1>", _on_click)