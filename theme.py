"""
vault/theme.py
───────────────
Single source of truth for the active theme colors.
Both widgets.py and views.py import from here.
app.py calls set_theme() when the toggle is clicked.
"""

DARK = {
    "ACCENT":   "#7C6AF7",
    "ACCENT2":  "#A594F9",
    "SURFACE":  "#1A1A2E",
    "SURFACE2": "#16213E",
    "SIDEBAR":  "#0F0E1F",
    "CARD":     "#16213E",
    "CARD2":    "#12102A",
    "TEXT":     "#E2E0FF",
    "SUBTEXT":  "#9896B8",
    "BORDER":   "#2A2850",
    "FAV_ON":   "#FF6B9D",
    "FAV_OFF":  "#4A4870",
    "DANGER":   "#FF4D6D",
    "OVERLAY":  "#1A1830",
}

LIGHT = {
    "ACCENT":   "#5A48D4",
    "ACCENT2":  "#7C6AF7",
    "SURFACE":  "#F0EEFF",
    "SURFACE2": "#E4E0FF",
    "SIDEBAR":  "#DDD8FF",
    "CARD":     "#E8E4FF",
    "CARD2":    "#F5F3FF",
    "TEXT":     "#1A1440",
    "SUBTEXT":  "#5A5480",
    "BORDER":   "#C4BEFF",
    "FAV_ON":   "#E0457A",
    "FAV_OFF":  "#9896B8",
    "DANGER":   "#CC2244",
    "OVERLAY":  "#C8C4EE",
}

# Active theme — starts as dark, app.py calls set_theme() to switch
_current = DARK


def set_theme(dark: bool) -> None:
    global _current
    _current = DARK if dark else LIGHT


def get() -> dict:
    return _current