#!/usr/bin/env python3
"""
claude-usage-widget — System tray widget showing Claude AI usage limits.

Displays session (5h), weekly, and extra usage utilization from claude.ai
as a color-coded tray icon with detailed tooltip.

Requirements: Python 3, GTK3 (python3-gi, python3-gi-cairo)
License: MIT
"""

import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

CONFIG_DIR = Path.home() / ".config" / "claude-usage-widget"
COOKIE_FILE = CONFIG_DIR / "cookie"
ICON_SIZE = 22
REFRESH_SECONDS = 60
API_BASE = "https://claude.ai/api"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def make_bar(pct, width=20):
    """Create a text progress bar."""
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def format_reset(iso_str):
    """Format reset time as human-readable relative string."""
    try:
        reset = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        delta = reset - now
        total_seconds = int(delta.total_seconds())
        if total_seconds <= 0:
            return "now"
        hours, remainder = divmod(total_seconds, 3600)
        minutes = remainder // 60
        if hours >= 24:
            days = hours // 24
            return f"{days}d {hours % 24}h"
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except (ValueError, TypeError):
        return "?"


def severity_color(pct):
    """Return (r, g, b) for a utilization percentage."""
    if pct >= 80:
        return (0.9, 0.2, 0.2)  # red
    if pct >= 50:
        return (0.9, 0.7, 0.1)  # yellow
    return (0.3, 0.8, 0.3)  # green


def draw_icon(pct):
    """Draw a tray icon with the peak utilization percentage."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, ICON_SIZE, ICON_SIZE)
    ctx = cairo.Context(surface)

    r, g, b = severity_color(pct)

    # Background circle
    cx, cy = ICON_SIZE / 2, ICON_SIZE / 2
    radius = ICON_SIZE / 2 - 1
    ctx.arc(cx, cy, radius, 0, 2 * math.pi)
    ctx.set_source_rgba(r, g, b, 0.9)
    ctx.fill()

    # Percentage text
    ctx.set_source_rgba(1, 1, 1, 1)
    text = str(round(pct))
    font_size = 9 if pct < 100 else 7.5
    ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(font_size)
    extents = ctx.text_extents(text)
    ctx.move_to(cx - extents.width / 2 - extents.x_bearing,
                cy - extents.height / 2 - extents.y_bearing)
    ctx.show_text(text)

    # Convert to pixbuf
    pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, ICON_SIZE, ICON_SIZE)
    return pixbuf


class ClaudeUsageWidget:
    def __init__(self):
        self.org_id = None
        self.usage_data = None
        self.error_msg = None

        self.icon = Gtk.StatusIcon()
        self.icon.set_tooltip_text("Claude Usage Widget\nLoading...")
        self.icon.connect("popup-menu", self.on_right_click)
        self.icon.connect("activate", self.on_left_click)
        self.icon.set_from_pixbuf(draw_icon(0))
        self.icon.set_visible(True)

        self.refresh()
        GLib.timeout_add_seconds(REFRESH_SECONDS, self.refresh)

    def read_cookie(self):
        """Read the auth cookie from config."""
        if not COOKIE_FILE.exists():
            self.error_msg = f"Cookie file not found:\n{COOKIE_FILE}"
            return None
        cookie = COOKIE_FILE.read_text().strip()
        if not cookie:
            self.error_msg = "Cookie file is empty"
            return None
        return cookie

    def api_request(self, path):
        """Make an authenticated request to claude.ai."""
        cookie = self.read_cookie()
        if not cookie:
            return None
        req = urllib.request.Request(f"{API_BASE}/{path}")
        req.add_header("Cookie", cookie)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/json")
        req.add_header("Referer", "https://claude.ai/settings/usage")
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())

    def fetch_org_id(self):
        """Fetch the organization UUID."""
        orgs = self.api_request("organizations")
        if orgs and len(orgs) > 0:
            return orgs[0]["uuid"]
        return None

    def fetch_usage(self):
        """Fetch current usage data."""
        if not self.org_id:
            self.org_id = self.fetch_org_id()
        if not self.org_id:
            self.error_msg = "Could not determine organization ID"
            return None
        return self.api_request(f"organizations/{self.org_id}/usage")

    def refresh(self):
        """Refresh usage data and update the tray icon."""
        try:
            data = self.fetch_usage()
            if data:
                self.usage_data = data
                self.error_msg = None
                self.update_icon()
        except urllib.error.HTTPError as e:
            if e.code == 403:
                self.error_msg = "Auth expired — update cookie"
            else:
                self.error_msg = f"HTTP {e.code}"
            self.update_icon()
        except Exception as e:
            self.error_msg = str(e)
            self.update_icon()
        return True  # keep timer running

    def peak_utilization(self):
        """Return the highest utilization value."""
        if not self.usage_data:
            return 0
        values = []
        for key in ("five_hour", "seven_day"):
            entry = self.usage_data.get(key)
            if entry:
                values.append(entry["utilization"])
        extra = self.usage_data.get("extra_usage")
        if extra and extra.get("is_enabled"):
            values.append(extra["utilization"])
        return max(values) if values else 0

    def build_tooltip(self):
        """Build the tooltip text."""
        if self.error_msg:
            return f"Claude Usage Widget\n⚠ {self.error_msg}"
        if not self.usage_data:
            return "Claude Usage Widget\nLoading..."

        d = self.usage_data
        lines = ["Claude Usage"]
        lines.append("━" * 32)

        # Session (5h)
        five = d.get("five_hour")
        if five:
            pct = five["utilization"]
            reset = format_reset(five.get("resets_at"))
            lines.append(f"Session (5h):  {make_bar(pct, 14)} {pct:4.0f}%  ↻{reset}")

        # Weekly
        seven = d.get("seven_day")
        if seven:
            pct = seven["utilization"]
            reset = format_reset(seven.get("resets_at"))
            lines.append(f"Weekly:        {make_bar(pct, 14)} {pct:4.0f}%  ↻{reset}")

        # Weekly Sonnet
        sonnet = d.get("seven_day_sonnet")
        if sonnet:
            pct = sonnet["utilization"]
            reset = format_reset(sonnet.get("resets_at"))
            lines.append(f"Sonnet (7d):   {make_bar(pct, 14)} {pct:4.0f}%  ↻{reset}")

        # Weekly Opus
        opus = d.get("seven_day_opus")
        if opus:
            pct = opus["utilization"]
            reset = format_reset(opus.get("resets_at"))
            lines.append(f"Opus (7d):     {make_bar(pct, 14)} {pct:4.0f}%  ↻{reset}")

        # Extra usage
        extra = d.get("extra_usage")
        if extra and extra.get("is_enabled"):
            pct = extra["utilization"]
            used = extra["used_credits"] / 100
            limit = extra["monthly_limit"] / 100
            lines.append(f"Extra Usage:   {make_bar(pct, 14)} {pct:4.0f}%  ${used:.0f}/${limit:.0f}")

        return "\n".join(lines)

    def update_icon(self):
        """Update tray icon color and tooltip."""
        pct = self.peak_utilization()
        if self.error_msg:
            pct = 100  # show red on error
        self.icon.set_from_pixbuf(draw_icon(pct))
        self.icon.set_tooltip_text(self.build_tooltip())

    def on_left_click(self, icon):
        """Left click: refresh immediately."""
        self.refresh()

    def on_right_click(self, icon, button, time):
        """Right click: show context menu."""
        menu = Gtk.Menu()

        # Refresh
        item_refresh = Gtk.MenuItem(label="Refresh")
        item_refresh.connect("activate", lambda _: self.refresh())
        menu.append(item_refresh)

        # Open in browser
        item_browser = Gtk.MenuItem(label="Open claude.ai Usage")
        item_browser.connect(
            "activate",
            lambda _: os.system("xdg-open https://claude.ai/settings/usage &"),
        )
        menu.append(item_browser)

        menu.append(Gtk.SeparatorMenuItem())

        # Quit
        item_quit = Gtk.MenuItem(label="Quit")
        item_quit.connect("activate", lambda _: Gtk.main_quit())
        menu.append(item_quit)

        menu.show_all()
        menu.popup(None, None, None, None, button, time)

    def run(self):
        Gtk.main()


def setup_cookie():
    """Interactive first-run: help user set up the cookie."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    print("claude-usage-widget — First-time setup")
    print("=" * 40)
    print()
    print("To show your Claude usage, this widget needs a browser cookie.")
    print()
    print("Steps:")
    print("  1. Open https://claude.ai/settings/usage in your browser")
    print("  2. Open DevTools (F12) → Network tab → reload the page")
    print("  3. Click on the 'usage' request → Headers tab")
    print("  4. Copy the full 'Cookie' header value")
    print(f"  5. Paste it into: {COOKIE_FILE}")
    print()
    print(f'  Example: echo \'your-cookie-here\' > "{COOKIE_FILE}"')
    print(f'           chmod 600 "{COOKIE_FILE}"')
    print()
    sys.exit(1)


def main():
    if not COOKIE_FILE.exists():
        setup_cookie()

    widget = ClaudeUsageWidget()
    widget.run()


if __name__ == "__main__":
    main()
