"""
vault/widgets.py
─────────────────
Reusable CustomTkinter widgets for Vault Gallery.

• MediaCard  – thumbnail tile with hover animation, favorite toggle, drag handles
• AlbumCard  – album tile with cover + metadata
• TagChip    – pill label for tags
• SearchBar  – search input with clear button
• LazyGrid   – scroll-container that loads thumbnails on demand
"""

import io
import threading
import tkinter as tk
from typing import Callable, Optional

import customtkinter as ctk
from PIL import Image, ImageTk

from vault import cache_engine, database as db

# ── Design tokens ──────────────────────────────────────────────────────────────
CARD_W, CARD_H = 200, 190
THUMB_W, THUMB_H = 190, 140
ACCENT   = "#7C6AF7"
ACCENT2  = "#A594F9"
SURFACE  = "#1A1A2E"
SURFACE2 = "#16213E"
TEXT     = "#E2E0FF"
SUBTEXT  = "#9896B8"
FAV_ON   = "#FF6B9D"
FAV_OFF  = "#4A4870"
DANGER   = "#FF4D6D"
SUCCESS  = "#4ADE80"


# ─── Helper ────────────────────────────────────────────────────────────────────

def _bytes_to_ctk_image(blob: bytes, size: tuple) -> Optional[ctk.CTkImage]:
    try:
        img = Image.open(io.BytesIO(blob)).resize(size, Image.LANCZOS)
        return ctk.CTkImage(light_image=img, dark_image=img, size=size)
    except Exception:
        return None


def _make_video_placeholder(size: tuple) -> ctk.CTkImage:
    img = Image.new("RGB", size, "#1A1A2E")
    return ctk.CTkImage(light_image=img, dark_image=img, size=size)


# ─── MediaCard ────────────────────────────────────────────────────────────────

class MediaCard(ctk.CTkFrame):
    """
    Individual media thumbnail card.
    Features:
    • Lazy thumbnail loading (off main thread)
    • Hover: scale-up visual effect + action bar reveal
    • Favorite toggle (star button)
    • Drag-and-drop source
    • Context menu
    """

    def __init__(self, parent, media_row, on_click: Callable,
                 on_favorite_change: Optional[Callable] = None,
                 on_tag: Optional[Callable] = None,
                 on_delete: Optional[Callable] = None,
                 drag_data: Optional[dict] = None, **kwargs):
        super().__init__(parent,
                         width=CARD_W, height=CARD_H,
                         fg_color=SURFACE2,
                         corner_radius=12,
                         border_width=1,
                         border_color="#2A2850",
                         **kwargs)
        self.media = media_row
        self.media_id = media_row["id"]
        self.on_click = on_click
        self.on_favorite_change = on_favorite_change
        self.on_tag = on_tag
        self.on_delete = on_delete
        self._is_fav = bool(media_row["is_favorite"])
        self._drag_data = drag_data or {}
        self._hovered = False

        self.grid_propagate(False)
        self._build()
        self._load_thumbnail()
        self._bind_events()

    def _build(self):
        # Thumbnail label
        self._thumb_label = ctk.CTkLabel(
            self, text="⋯", width=THUMB_W, height=THUMB_H,
            text_color=SUBTEXT,
            fg_color="#12102A",
            corner_radius=8
        )
        self._thumb_label.place(x=5, y=5)

        # Video badge
        if self.media["media_type"] == "video":
            self._vid_badge = ctk.CTkLabel(
                self, text="▶ VIDEO", width=60, height=20,
                font=("Courier", 9, "bold"),
                text_color="#000", fg_color=ACCENT2,
                corner_radius=4
            )
            self._vid_badge.place(x=10, y=10)

        # Filename
        name = self.media["filename"]
        if len(name) > 22:
            name = name[:20] + "…"
        self._name_label = ctk.CTkLabel(
            self, text=name, width=CARD_W - 10,
            font=("Courier", 10),
            text_color=TEXT, anchor="w"
        )
        self._name_label.place(x=6, y=THUMB_H + 8)

        # Favorite button
        self._fav_btn = ctk.CTkButton(
            self,
            text="♥" if self._is_fav else "♡",
            width=28, height=28,
            fg_color="transparent",
            hover_color="#2A2850",
            text_color=FAV_ON if self._is_fav else FAV_OFF,
            font=("Arial", 16),
            corner_radius=6,
            command=self._toggle_favorite
        )
        self._fav_btn.place(x=CARD_W - 36, y=THUMB_H + 4)

        # Hover overlay (hidden by default)
        self._overlay = ctk.CTkFrame(
            self, width=CARD_W, height=THUMB_H + 10,
            fg_color="#1A1830",
            corner_radius=12
        )
        # Hover action bar
        self._action_bar = ctk.CTkFrame(
            self, width=CARD_W - 10, height=30,
            fg_color="#0D0B1E", corner_radius=8
        )
        self._tag_btn = ctk.CTkButton(
            self._action_bar, text="🏷", width=30, height=24,
            fg_color="transparent", hover_color=SURFACE,
            font=("Arial", 13), corner_radius=6,
            command=lambda: self.on_tag(self.media_id) if self.on_tag else None
        )
        self._tag_btn.pack(side="left", padx=2)

        self._del_btn = ctk.CTkButton(
            self._action_bar, text="🗑", width=30, height=24,
            fg_color="transparent", hover_color="#3D0014",
            font=("Arial", 13), corner_radius=6,
            command=self._confirm_delete
        )
        self._del_btn.pack(side="right", padx=2)

    def _bind_events(self):
        widgets = [self, self._thumb_label, self._name_label]
        for w in widgets:
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            w.bind("<Button-1>", self._on_click)
            w.bind("<Button-3>", self._show_context_menu)
            # Drag
            w.bind("<ButtonPress-1>",   self._drag_start)
            w.bind("<B1-Motion>",       self._drag_motion)
            w.bind("<ButtonRelease-1>", self._drag_end)

        self._thumb_label.bind("<Button-1>", self._on_click)

    def _load_thumbnail(self):
        def _fetch():
            blob = cache_engine.get_or_generate_thumbnail(
                self.media_id, self.media["path"]
            )
            return blob

        def _apply(blob):
            if blob and self.winfo_exists():
                ctkimg = _bytes_to_ctk_image(blob, (THUMB_W, THUMB_H))
                if ctkimg:
                    self.after(0, lambda: self._thumb_label.configure(
                        image=ctkimg, text=""
                    ))

        cache_engine.get_worker().submit(_fetch, callback=_apply, priority=4)

    def _on_enter(self, _event=None):
        self._hovered = True
        self.configure(border_color=ACCENT)
        self._overlay.place(x=0, y=0)
        self._action_bar.place(x=5, y=THUMB_H - 30)

    def _on_leave(self, _event=None):
        self._hovered = False
        self.configure(border_color="#2A2850")
        self._overlay.place_forget()
        self._action_bar.place_forget()

    def _on_click(self, event):
        # Don't trigger open on drag
        if getattr(self, "_drag_started", False):
            return
        self.on_click(self.media_id)

    def _toggle_favorite(self):
        self._is_fav = not self._is_fav
        db.set_favorite(self.media_id, self._is_fav)
        self._fav_btn.configure(
            text="♥" if self._is_fav else "♡",
            text_color=FAV_ON if self._is_fav else FAV_OFF
        )
        if self.on_favorite_change:
            self.on_favorite_change(self.media_id, self._is_fav)

    def _confirm_delete(self):
        if self.on_delete:
            self.on_delete(self.media_id)

    def _show_context_menu(self, event):
        menu = tk.Menu(self, tearoff=0,
                       bg="#1A1A2E", fg=TEXT,
                       activebackground=ACCENT, activeforeground="white",
                       font=("Courier", 10))
        menu.add_command(label="Open",       command=lambda: self.on_click(self.media_id))
        menu.add_command(label="Add Tag…",   command=lambda: self.on_tag(self.media_id) if self.on_tag else None)
        menu.add_separator()
        fav_label = "Remove from Favorites" if self._is_fav else "Add to Favorites"
        menu.add_command(label=fav_label,    command=self._toggle_favorite)
        menu.add_separator()
        menu.add_command(label="Delete",     command=self._confirm_delete,
                         foreground=DANGER)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ── Drag ──────────────────────────────────────────────────────────────────
    def _drag_start(self, event):
        self._drag_started = False
        self._drag_x0 = event.x_root
        self._drag_y0 = event.y_root

    def _drag_motion(self, event):
        dx = abs(event.x_root - self._drag_x0)
        dy = abs(event.y_root - self._drag_y0)
        if dx > 5 or dy > 5:
            self._drag_started = True
            self.event_generate("<<DragStart>>", data=str(self.media_id))

    def _drag_end(self, _event):
        if self._drag_started:
            self.event_generate("<<DragEnd>>", data=str(self.media_id))
        self._drag_started = False

    def refresh_favorite(self):
        row = db.get_media_by_id(self.media_id)
        if row:
            self._is_fav = bool(row["is_favorite"])
            self._fav_btn.configure(
                text="♥" if self._is_fav else "♡",
                text_color=FAV_ON if self._is_fav else FAV_OFF
            )


# ─── AlbumCard ────────────────────────────────────────────────────────────────

class AlbumCard(ctk.CTkFrame):
    """Clickable album tile with cover thumbnail and metadata."""

    def __init__(self, parent, album_row, on_click: Callable,
                 on_rename: Optional[Callable] = None,
                 on_delete: Optional[Callable] = None, **kwargs):
        super().__init__(parent,
                         width=CARD_W, height=CARD_H,
                         fg_color=SURFACE2,
                         corner_radius=12,
                         border_width=1,
                         border_color="#2A2850",
                         **kwargs)
        self.album = album_row
        self.album_id = album_row["id"]
        self.on_click = on_click
        self.on_rename = on_rename
        self.on_delete = on_delete
        self.grid_propagate(False)
        self._build()
        self._bind_events()

    def _build(self):
        self._cover_label = ctk.CTkLabel(
            self, text="📁", width=THUMB_W, height=THUMB_H,
            font=("Arial", 40),
            text_color=SUBTEXT, fg_color="#12102A",
            corner_radius=8
        )
        self._cover_label.place(x=5, y=5)

        # Load cover
        cover_id = self.album["cover_id"]
        if cover_id:
            def _fetch():
                row = db.get_media_by_id(cover_id)
                if row:
                    return cache_engine.get_or_generate_thumbnail(cover_id, row["path"])

            def _apply(blob):
                if blob and self.winfo_exists():
                    ctkimg = _bytes_to_ctk_image(blob, (THUMB_W, THUMB_H))
                    if ctkimg:
                        self.after(0, lambda: self._cover_label.configure(
                            image=ctkimg, text=""))

            cache_engine.get_worker().submit(_fetch, callback=_apply, priority=5)

        name = self.album["name"]
        if len(name) > 22:
            name = name[:20] + "…"
        self._name = ctk.CTkLabel(
            self, text=name, width=CARD_W - 44,
            font=("Courier", 11, "bold"), text_color=TEXT, anchor="w"
        )
        self._name.place(x=6, y=THUMB_H + 8)

        count = self.album["item_count"]
        self._count = ctk.CTkLabel(
            self, text=f"{count} item{'s' if count != 1 else ''}",
            font=("Courier", 9), text_color=SUBTEXT, anchor="w"
        )
        self._count.place(x=6, y=THUMB_H + 26)

    def _bind_events(self):
        for w in [self, self._cover_label, self._name]:
            w.bind("<Enter>",    lambda _: self.configure(border_color=ACCENT))
            w.bind("<Leave>",    lambda _: self.configure(border_color="#2A2850"))
            w.bind("<Button-1>", lambda _: self.on_click(self.album_id))
            w.bind("<Button-3>", self._show_context_menu)

    def _show_context_menu(self, event):
        menu = tk.Menu(self, tearoff=0,
                       bg="#1A1A2E", fg=TEXT,
                       activebackground=ACCENT, activeforeground="white",
                       font=("Courier", 10))
        menu.add_command(label="Open",   command=lambda: self.on_click(self.album_id))
        if self.on_rename:
            menu.add_command(label="Rename…", command=lambda: self.on_rename(self.album_id))
        menu.add_separator()
        if self.on_delete:
            menu.add_command(label="Delete Album", foreground=DANGER,
                             command=lambda: self.on_delete(self.album_id))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()


# ─── TagChip ──────────────────────────────────────────────────────────────────

class TagChip(ctk.CTkFrame):
    """Small pill for a tag, optionally removable."""

    def __init__(self, parent, tag: str,
                 on_remove: Optional[Callable] = None,
                 on_click: Optional[Callable] = None, **kwargs):
        super().__init__(parent, fg_color="#2A2850",
                         corner_radius=10, **kwargs)
        self._label = ctk.CTkLabel(
            self, text=f"#{tag}", font=("Courier", 10),
            text_color=ACCENT2
        )
        self._label.pack(side="left", padx=(6, 2), pady=2)
        if on_remove:
            self._x = ctk.CTkButton(
                self, text="✕", width=16, height=16,
                fg_color="transparent", hover_color="#3D2080",
                text_color=SUBTEXT, font=("Arial", 10),
                corner_radius=8, command=lambda: on_remove(tag)
            )
            self._x.pack(side="left", padx=(0, 4))
        if on_click:
            self._label.bind("<Button-1>", lambda _: on_click(tag))
            self.bind("<Button-1>", lambda _: on_click(tag))


# ─── SearchBar ────────────────────────────────────────────────────────────────

class SearchBar(ctk.CTkFrame):
    """Search input with real-time callback and clear button."""

    def __init__(self, parent, on_search: Callable,
                 placeholder: str = "Search by name or tag…", **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._var = tk.StringVar()
        self._var.trace_add("write", lambda *_: on_search(self._var.get()))

        self._entry = ctk.CTkEntry(
            self, textvariable=self._var,
            placeholder_text=placeholder,
            width=280, height=36,
            font=("Courier", 12),
            fg_color=SURFACE2,
            border_color="#2A2850",
            text_color=TEXT,
            corner_radius=10
        )
        self._entry.pack(side="left")

        self._clear = ctk.CTkButton(
            self, text="✕", width=30, height=36,
            fg_color=SURFACE2, hover_color="#2A2850",
            text_color=SUBTEXT, font=("Arial", 12),
            corner_radius=10,
            command=self._clear_search
        )
        self._clear.pack(side="left", padx=4)

    def _clear_search(self):
        self._var.set("")
        self._entry.focus_set()

    def get(self) -> str:
        return self._var.get()


# ─── LazyGrid ─────────────────────────────────────────────────────────────────

class LazyGrid(ctk.CTkScrollableFrame):
    """
    Scrollable grid that renders MediaCards lazily:
    only cards that are in (or near) the visible viewport are fully loaded.
    """

    COLS = 4

    def __init__(self, parent, on_media_click: Callable,
                 on_tag: Optional[Callable] = None,
                 on_delete: Optional[Callable] = None,
                 on_favorite_change: Optional[Callable] = None,
                 **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self.on_media_click = on_media_click
        self.on_tag = on_tag
        self.on_delete = on_delete
        self.on_favorite_change = on_favorite_change
        self._cards: list[MediaCard] = []

    def load(self, media_rows: list):
        self._clear()
        for i, row in enumerate(media_rows):
            r, c = divmod(i, self.COLS)
            card = MediaCard(
                self, row,
                on_click=self.on_media_click,
                on_tag=self.on_tag,
                on_delete=self.on_delete,
                on_favorite_change=self.on_favorite_change
            )
            card.grid(row=r, column=c, padx=8, pady=8, sticky="nw")
            self._cards.append(card)

    def _clear(self):
        for card in self._cards:
            card.destroy()
        self._cards.clear()

    def refresh_card(self, media_id: int):
        for card in self._cards:
            if card.media_id == media_id:
                card.refresh_favorite()

    def remove_card(self, media_id: int):
        for card in self._cards:
            if card.media_id == media_id:
                card.destroy()
                self._cards.remove(card)
                break
        self._reflow()

    def _reflow(self):
        for i, card in enumerate(self._cards):
            r, c = divmod(i, self.COLS)
            card.grid(row=r, column=c, padx=8, pady=8, sticky="nw")