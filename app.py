"""
vault/app.py
─────────────
Main Vault Gallery application window.
"""

import tkinter as tk
from typing import Optional

import customtkinter as ctk

from vault import database as db
from vault.cache_engine import get_worker
from vault.views import (
    AllMediaView, AlbumsView, AlbumDetailView,
    FavoritesView, DuplicatesView, TagManagerView,
    TimelineView,
)

# ── Design tokens — Dark ───────────────────────────────────────────────────────
DARK = {
    "ACCENT":   "#7C6AF7",
    "ACCENT2":  "#A594F9",
    "SURFACE":  "#1A1A2E",
    "SURFACE2": "#16213E",
    "SIDEBAR":  "#0F0E1F",
    "TEXT":     "#E2E0FF",
    "SUBTEXT":  "#9896B8",
    "BORDER":   "#2A2850",
}

# ── Design tokens — Light ──────────────────────────────────────────────────────
LIGHT = {
    "ACCENT":   "#5A48D4",
    "ACCENT2":  "#7C6AF7",
    "SURFACE":  "#F0EEFF",
    "SURFACE2": "#E4E0FF",
    "SIDEBAR":  "#DDD8FF",
    "TEXT":     "#1A1440",
    "SUBTEXT":  "#5A5480",
    "BORDER":   "#C4BEFF",
}

NAV_ITEMS = [
    ("Library",    "📷", "media"),
    ("Timeline",   "🕓", "timeline"),
    ("Albums",     "🗂", "albums"),
    ("Favorites",  "♥",  "favorites"),
    ("Tags",       "🏷", "tags"),
    ("Duplicates", "⚡", "duplicates"),
]


# ── Theme Toggle Switch ────────────────────────────────────────────────────────

class ThemeSwitch(tk.Canvas):
    """Smooth pill-shaped toggle: 🌙 Dark ↔ ☀ Light"""

    PILL_W = 92
    PILL_H = 28
    KNOB_R = 11
    STEPS  = 8

    DARK_BG    = "#2A2850"
    DARK_KNOB  = "#A594F9"
    DARK_LABEL = "#9896B8"

    LIGHT_BG    = "#C4BEFF"
    LIGHT_KNOB  = "#5A48D4"
    LIGHT_LABEL = "#3A2FA0"

    def __init__(self, parent, on_toggle=None, initial_dark=True, bg="#0F0E1F", **kwargs):
        super().__init__(
            parent,
            width=self.PILL_W, height=self.PILL_H,
            bg=bg,
            highlightthickness=0,
            cursor="hand2",
            **kwargs
        )
        self._dark      = initial_dark
        self._on_toggle = on_toggle
        self._animating = False
        self._x_dark    = self.KNOB_R + 6
        self._x_light   = self.PILL_W - self.KNOB_R - 6
        self._knob_x    = self._x_dark if initial_dark else self._x_light

        self._draw()
        self.bind("<Button-1>", self._clicked)

    def _draw(self):
        self.delete("all")
        if self._dark:
            bg, knob, tcol = self.DARK_BG, self.DARK_KNOB, self.DARK_LABEL
            label, lx, anchor = "🌙 Dark", self.PILL_W - 6, "e"
        else:
            bg, knob, tcol = self.LIGHT_BG, self.LIGHT_KNOB, self.LIGHT_LABEL
            label, lx, anchor = "☀ Light", 6, "w"

        r = self.PILL_H // 2
        self._round_rect(0, 0, self.PILL_W, self.PILL_H, r, fill=bg, outline="")
        self.create_text(lx, self.PILL_H // 2, text=label, anchor=anchor,
                         font=("Courier", 8, "bold"), fill=tcol)
        cx, cy = self._knob_x, self.PILL_H // 2
        self.create_oval(cx - self.KNOB_R, cy - self.KNOB_R,
                         cx + self.KNOB_R, cy + self.KNOB_R,
                         fill=knob, outline="")

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        self.create_arc(x1,     y1,     x1+2*r, y1+2*r, start=90,  extent=90,  style="pieslice", **kw)
        self.create_arc(x2-2*r, y1,     x2,     y1+2*r, start=0,   extent=90,  style="pieslice", **kw)
        self.create_arc(x1,     y2-2*r, x1+2*r, y2,     start=180, extent=90,  style="pieslice", **kw)
        self.create_arc(x2-2*r, y2-2*r, x2,     y2,     start=270, extent=90,  style="pieslice", **kw)
        self.create_rectangle(x1+r, y1, x2-r, y2, **kw)
        self.create_rectangle(x1,   y1+r, x2,  y2-r, **kw)

    def _clicked(self, _=None):
        if self._animating:
            return
        self._animating = True
        target = self._x_light if self._dark else self._x_dark
        self._animate(target)

    def _animate(self, target_x):
        delta = (target_x - self._knob_x) / self.STEPS

        def _step(n):
            if n == 0:
                self._knob_x    = target_x
                self._dark      = not self._dark
                self._animating = False
                self._draw()
                if self._on_toggle:
                    self._on_toggle(self._dark)
                return
            self._knob_x += delta
            self._draw()
            self.after(16, lambda: _step(n - 1))

        _step(self.STEPS)

    def update_bg(self, bg: str):
        self.configure(bg=bg)
        self._draw()


# ── Main App ──────────────────────────────────────────────────────────────────

class VaultApp(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("Vault")
        self.geometry("1280x800")
        self.minsize(900, 600)

        db.init_db()

        self._is_dark           = True
        self._T                 = DARK
        self._current_view_name = ""
        self._content_widget: Optional[ctk.CTkFrame] = None

        self._build_layout()
        self._navigate("media")

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_layout(self):
        T = self._T

        # Topbar
        self._topbar = ctk.CTkFrame(self, fg_color=T["SIDEBAR"],
                                     corner_radius=0, height=56)
        self._topbar.pack(fill="x", side="top")
        self._topbar.pack_propagate(False)

        self._logo = ctk.CTkLabel(self._topbar, text="  🔒 VAULT",
                                   font=("Courier", 18, "bold"),
                                   text_color=T["ACCENT2"])
        self._logo.pack(side="left", padx=20)

        self._status_lbl = ctk.CTkLabel(self._topbar, text="",
                                         font=("Courier", 10),
                                         text_color=T["SUBTEXT"])
        self._status_lbl.pack(side="right", padx=20)

        self._theme_switch = ThemeSwitch(
            self._topbar,
            on_toggle=self._toggle_theme,
            initial_dark=True,
            bg=T["SIDEBAR"]
        )
        self._theme_switch.pack(side="right", padx=(0, 12))

        self._worker_indicator = ctk.CTkLabel(
            self._topbar, text="●", font=("Arial", 10), text_color="#2A4870"
        )
        self._worker_indicator.pack(side="right", padx=(0, 6))
        self._poll_worker_status()

        # Body
        self._body = ctk.CTkFrame(self, fg_color=T["SURFACE"])
        self._body.pack(fill="both", expand=True)

        # Sidebar
        self._sidebar = ctk.CTkFrame(self._body, fg_color=T["SIDEBAR"],
                                      corner_radius=0, width=180)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        self._nav_label = ctk.CTkLabel(self._sidebar, text="NAVIGATE",
                                        font=("Courier", 9),
                                        text_color=T["BORDER"])
        self._nav_label.pack(pady=(24, 6), padx=16, anchor="w")

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        for label, icon, key in NAV_ITEMS:
            btn = ctk.CTkButton(
                self._sidebar,
                text=f"  {icon}  {label}",
                height=40, anchor="w",
                fg_color="transparent",
                hover_color=T["SURFACE2"],
                text_color=T["SUBTEXT"],
                font=("Courier", 12),
                corner_radius=8,
                command=lambda k=key: self._navigate(k)
            )
            btn.pack(fill="x", padx=8, pady=2)
            self._nav_buttons[key] = btn

        self._divider = ctk.CTkFrame(self._sidebar, height=1,
                                      fg_color=T["BORDER"])
        self._divider.pack(fill="x", padx=12, pady=16)

        self._stats_lbl = ctk.CTkLabel(self._sidebar, text="",
                                        font=("Courier", 9),
                                        text_color=T["SUBTEXT"],
                                        justify="left")
        self._stats_lbl.pack(anchor="w", padx=16)
        self._refresh_stats()

        # Content area
        self._content_area = ctk.CTkFrame(self._body, fg_color=T["SURFACE"],
                                           corner_radius=0)
        self._content_area.pack(side="left", fill="both", expand=True,
                                padx=16, pady=12)

    # ── Theme toggle ──────────────────────────────────────────────────────────

    def _toggle_theme(self, is_dark: bool):
        self._is_dark = is_dark
        self._T = DARK if is_dark else LIGHT
        T = self._T

        # 1. Switch all CTk widgets globally
        ctk.set_appearance_mode("dark" if is_dark else "light")

        # 2. Repaint every hardcoded frame/label in the shell
        self._topbar.configure(fg_color=T["SIDEBAR"])
        self._logo.configure(text_color=T["ACCENT2"])
        self._status_lbl.configure(text_color=T["SUBTEXT"])
        self._theme_switch.update_bg(T["SIDEBAR"])

        self._body.configure(fg_color=T["SURFACE"])
        self._sidebar.configure(fg_color=T["SIDEBAR"])
        self._nav_label.configure(text_color=T["BORDER"])
        self._divider.configure(fg_color=T["BORDER"])
        self._stats_lbl.configure(text_color=T["SUBTEXT"])
        self._content_area.configure(fg_color=T["SURFACE"])

        # 3. Repaint nav buttons
        active = self._current_view_name
        for k, btn in self._nav_buttons.items():
            btn.configure(
                fg_color=T["ACCENT"] if k == active else "transparent",
                hover_color=T["SURFACE2"],
                text_color=T["TEXT"] if k == active else T["SUBTEXT"],
            )

        # 4. Reload current view so its child widgets also repaint
        self._navigate(self._current_view_name)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _navigate(self, key: str, album_id: Optional[int] = None):
        T = self._T

        for k, btn in self._nav_buttons.items():
            btn.configure(
                fg_color=T["ACCENT"] if k == key else "transparent",
                text_color=T["TEXT"] if k == key else T["SUBTEXT"],
            )

        if self._content_widget:
            self._content_widget.destroy()
            self._content_widget = None

        self._current_view_name = key

        if key == "media":
            view = AllMediaView(self._content_area)
        elif key == "timeline":
            view = TimelineView(self._content_area)
        elif key == "albums":
            view = AlbumsView(self._content_area,
                              on_open_album=lambda aid: self._navigate("album_detail", aid))
        elif key == "album_detail" and album_id is not None:
            view = AlbumDetailView(
                self._content_area,
                album_id=album_id,
                on_back=lambda: self._navigate("albums")
            )
        elif key == "favorites":
            view = FavoritesView(self._content_area)
        elif key == "tags":
            view = TagManagerView(self._content_area)
        elif key == "duplicates":
            view = DuplicatesView(self._content_area)
        else:
            view = ctk.CTkFrame(self._content_area, fg_color="transparent")
            ctk.CTkLabel(view, text="Coming soon…",
                         font=("Courier", 16),
                         text_color=T["SUBTEXT"]).pack(expand=True)

        view.pack(fill="both", expand=True)
        self._content_widget = view
        self._refresh_stats()

    # ── Stats ─────────────────────────────────────────────────────────────────

    def _refresh_stats(self):
        try:
            conn = db.get_conn()
            total = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
            imgs  = conn.execute("SELECT COUNT(*) FROM media WHERE media_type='image'").fetchone()[0]
            vids  = conn.execute("SELECT COUNT(*) FROM media WHERE media_type='video'").fetchone()[0]
            favs  = conn.execute("SELECT COUNT(*) FROM media WHERE is_favorite=1").fetchone()[0]
            albs  = conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
            tags  = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
            self._stats_lbl.configure(
                text=f"📷 {imgs} images\n🎬 {vids} videos\n♥  {favs} favorites\n"
                     f"🗂 {albs} albums\n🏷 {tags} tags\n\n📦 {total} total"
            )
        except Exception:
            pass

    # ── Worker indicator ──────────────────────────────────────────────────────

    def _poll_worker_status(self):
        busy  = not get_worker()._queue.empty()
        color = self._T["ACCENT"] if busy else "#2A4870"
        self._worker_indicator.configure(text_color=color)
        self.after(800, self._poll_worker_status)

    # ── Status bar ────────────────────────────────────────────────────────────

    def set_status(self, msg: str, clear_after_ms: int = 4000):
        self._status_lbl.configure(text=msg)
        if clear_after_ms > 0:
            self.after(clear_after_ms, lambda: self._status_lbl.configure(text=""))


def run():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = VaultApp()
    app.mainloop()