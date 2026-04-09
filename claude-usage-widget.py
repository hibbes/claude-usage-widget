#!/usr/bin/env python3
"""
claude-usage-widget — System tray widget showing Claude AI usage limits.

Displays session (5h), weekly, and extra usage utilization from claude.ai
as a color-coded tray icon with detailed tooltip.

Requirements: Python 3, GTK3 (python3-gi, python3-gi-cairo)
License: MIT
"""

import glob
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
CONKY_FILE = CONFIG_DIR / "conky.txt"
CLAUDE_DIR = Path.home() / ".claude"
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


def read_local_sessions():
    """Read token usage from local Claude Code session JSONL files.

    Returns dict with:
      today_tokens: total tokens used today (all sessions)
      active_session: dict with tokens, duration_min, tokens_per_min for newest session
    """
    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.exists():
        return None

    today = datetime.now().strftime("%Y-%m-%d")
    today_tokens = 0
    active_session = None
    active_mtime = 0

    for jsonl in projects_dir.rglob("*.jsonl"):
        try:
            mtime = jsonl.stat().st_mtime
            mdate = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
            if mdate != today:
                continue

            session_in = session_out = session_cache_r = session_cache_c = 0
            first_ts = last_ts = None

            with open(jsonl) as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") == "assistant" and "message" in d:
                        u = d["message"].get("usage", {})
                        if u:
                            session_in += u.get("input_tokens", 0)
                            session_out += u.get("output_tokens", 0)
                            session_cache_r += u.get("cache_read_input_tokens", 0)
                            session_cache_c += u.get("cache_creation_input_tokens", 0)
                    # Track timestamps from any message type
                    ts = d.get("timestamp")
                    if ts:
                        if first_ts is None or ts < first_ts:
                            first_ts = ts
                        if last_ts is None or ts > last_ts:
                            last_ts = ts

            total = session_in + session_out + session_cache_r + session_cache_c
            today_tokens += total

            if mtime > active_mtime and total > 0:
                active_mtime = mtime
                duration_min = 0
                if first_ts and last_ts:
                    # Timestamps are ISO strings or epoch ms
                    try:
                        if isinstance(first_ts, str):
                            t0 = datetime.fromisoformat(first_ts).timestamp()
                            t1 = datetime.fromisoformat(last_ts).timestamp()
                        else:
                            t0 = first_ts / 1000 if first_ts > 1e12 else first_ts
                            t1 = last_ts / 1000 if last_ts > 1e12 else last_ts
                        duration_min = max(1, (t1 - t0) / 60)
                    except (ValueError, TypeError):
                        duration_min = 0

                active_session = {
                    "tokens": total,
                    "input": session_in,
                    "output": session_out,
                    "cache_read": session_cache_r,
                    "cache_create": session_cache_c,
                    "duration_min": duration_min,
                    "tokens_per_min": round(total / duration_min) if duration_min > 0 else 0,
                }
        except (OSError, PermissionError):
            continue

    return {
        "today_tokens": today_tokens,
        "active_session": active_session,
    }


def format_tokens(n):
    """Format token count as human-readable string."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def severity_color(pct):
    """Return (r, g, b) for a utilization percentage."""
    if pct >= 80:
        return (0.9, 0.2, 0.2)  # red
    if pct >= 50:
        return (0.9, 0.7, 0.1)  # yellow
    return (0.3, 0.8, 0.3)  # green


def draw_icon(pct):
    """Draw a tray icon with the peak utilization percentage.

    If pct is None, draws a dash (offline/not connected).
    """
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, ICON_SIZE, ICON_SIZE)
    ctx = cairo.Context(surface)

    if pct is None:
        r, g, b = (0.5, 0.5, 0.5)  # gray for offline
    else:
        r, g, b = severity_color(pct)

    # Background — full square with rounded corners for max space
    radius = 4
    w, h = ICON_SIZE, ICON_SIZE
    ctx.new_sub_path()
    ctx.arc(w - radius, radius, radius, -math.pi / 2, 0)
    ctx.arc(w - radius, h - radius, radius, 0, math.pi / 2)
    ctx.arc(radius, h - radius, radius, math.pi / 2, math.pi)
    ctx.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
    ctx.close_path()
    ctx.set_source_rgba(r, g, b, 0.95)
    ctx.fill()

    # Percentage text — fill as much of the square as possible
    text = "—" if pct is None else str(round(pct))
    ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)

    # Auto-size: try large, shrink until it fits
    for font_size in (16, 14, 12, 10):
        ctx.set_font_size(font_size)
        extents = ctx.text_extents(text)
        if extents.width <= w - 4 and extents.height <= h - 4:
            break

    # Draw text shadow for contrast
    cx, cy = w / 2, h / 2
    tx = cx - extents.width / 2 - extents.x_bearing
    ty = cy - extents.height / 2 - extents.y_bearing
    ctx.set_source_rgba(0, 0, 0, 0.4)
    ctx.move_to(tx + 1, ty + 1)
    ctx.show_text(text)

    # Draw text
    ctx.set_source_rgba(1, 1, 1, 1)
    ctx.move_to(tx, ty)
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
        self.icon.set_from_pixbuf(draw_icon(None))
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
        except urllib.error.HTTPError as e:
            if e.code == 403:
                self.error_msg = "Auth expired — update cookie"
            else:
                self.error_msg = f"HTTP {e.code}"
        except Exception as e:
            self.error_msg = str(e)

        # Always refresh local session data (works without API)
        try:
            self.local_data = read_local_sessions()
        except Exception:
            self.local_data = None

        self.update_icon()
        self.write_conky()
        return True  # keep timer running

    def write_conky(self):
        """Write usage data to a file for conky to read."""
        try:
            lines = {}
            d = self.usage_data
            if d:
                five = d.get("five_hour")
                if five:
                    lines["session_pct"] = f"{five['utilization']:.0f}"
                    lines["session_reset"] = format_reset(five.get("resets_at"))
                seven = d.get("seven_day")
                if seven:
                    lines["weekly_pct"] = f"{seven['utilization']:.0f}"
                    lines["weekly_reset"] = format_reset(seven.get("resets_at"))
                extra = d.get("extra_usage")
                if extra and extra.get("is_enabled"):
                    lines["extra_pct"] = f"{extra['utilization']:.0f}"
                    lines["extra_used"] = f"{extra['used_credits'] / 100:.0f}"
                    lines["extra_limit"] = f"{extra['monthly_limit'] / 100:.0f}"
                    lines["extra_display"] = f"${extra['used_credits'] / 100:.0f}/${extra['monthly_limit'] / 100:.0f}"

            local = getattr(self, "local_data", None)
            if local:
                sess = local.get("active_session")
                if sess:
                    lines["session_tokens"] = format_tokens(sess["tokens"])
                    lines["tokens_per_min"] = format_tokens(sess["tokens_per_min"])
                    lines["session_duration"] = f"{sess['duration_min']:.0f}"
                lines["today_tokens"] = format_tokens(local.get("today_tokens", 0))

            if self.error_msg:
                lines["error"] = self.error_msg

            CONKY_FILE.write_text(
                "\n".join(f"{k}={v}" for k, v in lines.items()) + "\n"
            )
        except Exception:
            pass

    def peak_utilization(self):
        """Return the utilization value for the icon color.

        Focus on session + weekly limits. Only show extra usage
        when session or weekly has hit 100% (rate-limited).
        """
        if not self.usage_data:
            return 0
        primary = []
        for key in ("five_hour", "seven_day"):
            entry = self.usage_data.get(key)
            if entry:
                primary.append(entry["utilization"])
        peak = max(primary) if primary else 0
        if peak >= 100:
            extra = self.usage_data.get("extra_usage")
            if extra and extra.get("is_enabled"):
                return extra["utilization"]
        return peak

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

        # Local session stats
        local = getattr(self, "local_data", None)
        if local:
            lines.append("━" * 32)
            sess = local.get("active_session")
            if sess:
                tpm = sess["tokens_per_min"]
                dur = sess["duration_min"]
                tok = format_tokens(sess["tokens"])
                lines.append(f"This session:  {tok} tokens, {dur:.0f} min")
                lines.append(f"Speed:         {format_tokens(tpm)} tokens/min")
            today = local.get("today_tokens", 0)
            if today:
                lines.append(f"Today total:   {format_tokens(today)} tokens")

        return "\n".join(lines)

    def session_utilization(self):
        """Return the 5-hour session utilization percentage."""
        if not self.usage_data:
            return 0
        five = self.usage_data.get("five_hour")
        return five["utilization"] if five else 0

    def update_icon(self):
        """Update tray icon color and tooltip."""
        pct = self.session_utilization()
        if self.error_msg or not self.usage_data:
            pct = None  # show dash when offline/error
        self.icon.set_from_pixbuf(draw_icon(pct))
        self.icon.set_tooltip_text(self.build_tooltip())

    def on_left_click(self, icon):
        """Left click: show usage popup."""
        self.refresh()
        self.show_popup()

    def show_popup(self):
        """Show a small popup window with usage details."""
        # Close existing popup if any
        if hasattr(self, "_popup") and self._popup:
            self._popup.destroy()

        win = Gtk.Window(type=Gtk.WindowType.POPUP)
        win.set_decorated(False)
        win.set_resizable(False)
        win.set_keep_above(True)
        win.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)

        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.OUT)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(10)

        text = self.build_tooltip()
        label = Gtk.Label()
        label.set_markup(f"<tt>{GLib.markup_escape_text(text)}</tt>")
        label.set_justify(Gtk.Justification.LEFT)
        label.set_halign(Gtk.Align.START)
        box.pack_start(label, False, False, 0)

        frame.add(box)
        win.add(frame)
        win.show_all()

        # Position above the click point (tray is at bottom)
        display = Gdk.Display.get_default()
        seat = display.get_default_seat()
        _, x, y = seat.get_pointer().get_position()
        win_width, win_height = win.get_size()
        screen = Gdk.Screen.get_default()
        screen_width = screen.get_width()

        # Place above cursor, keep on screen
        popup_x = min(x - win_width // 2, screen_width - win_width - 5)
        popup_x = max(5, popup_x)
        popup_y = y - win_height - 10
        if popup_y < 5:
            popup_y = y + 10  # fallback: below cursor
        win.move(popup_x, popup_y)

        self._popup = win

        # Auto-close after 5 seconds or on click
        win.connect("button-press-event", lambda *_: win.destroy())
        GLib.timeout_add(15000, self._close_popup)

    def _close_popup(self):
        if hasattr(self, "_popup") and self._popup:
            self._popup.destroy()
            self._popup = None
        return False

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
