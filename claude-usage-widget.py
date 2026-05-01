#!/usr/bin/env python3
"""claude-usage-widget — daemon that writes Claude AI usage to conky.txt.

Native Wayland: no GTK, no tray icon. Display is handled by the Waybar
custom/claude module (~/.config/waybar-labwc/claude-status.sh) and conky,
both of which read ~/.config/claude-usage-widget/conky.txt.
"""

import json
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "claude-usage-widget"
COOKIE_FILE = CONFIG_DIR / "cookie"
CONKY_FILE = CONFIG_DIR / "conky.txt"
CLAUDE_DIR = Path.home() / ".claude"
REFRESH_SECONDS = 60
API_BASE = "https://claude.ai/api"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def format_reset(iso_str):
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


def format_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def read_local_sessions():
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
                    "duration_min": duration_min,
                    "tokens_per_min": round(total / duration_min) if duration_min > 0 else 0,
                }
        except (OSError, PermissionError):
            continue

    return {"today_tokens": today_tokens, "active_session": active_session}


class UsageDaemon:
    def __init__(self):
        self.org_id = None
        self.usage_data = None
        self.error_msg = None
        self.local_data = None

    def read_cookie(self):
        if not COOKIE_FILE.exists():
            self.error_msg = f"Cookie file not found: {COOKIE_FILE}"
            return None
        cookie = COOKIE_FILE.read_text().strip()
        if not cookie:
            self.error_msg = "Cookie file is empty"
            return None
        return cookie

    def api_request(self, path):
        cookie = self.read_cookie()
        if not cookie:
            return None
        req = urllib.request.Request(f"{API_BASE}/{path}")
        req.add_header("Cookie", cookie)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Accept", "application/json")
        req.add_header("Referer", "https://claude.ai/settings/usage")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    def fetch_org_id(self):
        orgs = self.api_request("organizations")
        if orgs:
            return orgs[0]["uuid"]
        return None

    def fetch_usage(self):
        if not self.org_id:
            self.org_id = self.fetch_org_id()
        if not self.org_id:
            self.error_msg = "Could not determine organization ID"
            return None
        return self.api_request(f"organizations/{self.org_id}/usage")

    def refresh(self):
        try:
            data = self.fetch_usage()
            if data:
                self.usage_data = data
                self.error_msg = None
        except urllib.error.HTTPError as e:
            self.error_msg = "Auth expired — update cookie" if e.code == 403 else f"HTTP {e.code}"
        except Exception as e:
            self.error_msg = str(e)

        try:
            self.local_data = read_local_sessions()
        except Exception:
            self.local_data = None

        self.write_conky()

    def write_conky(self):
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
                util = extra.get("utilization")
                used = (extra.get("used_credits") or 0) / 100
                limit = (extra.get("monthly_limit") or 0) / 100
                sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get(extra.get("currency"), "$")
                lines["extra_pct"] = f"{util:.0f}" if util is not None else f"{(used / limit * 100) if limit else 0:.0f}"
                lines["extra_used"] = f"{used:.0f}"
                lines["extra_limit"] = f"{limit:.0f}"
                lines["extra_display"] = f"{sym}{used:.0f}/{sym}{limit:.0f}"

        if self.local_data:
            sess = self.local_data.get("active_session")
            if sess:
                lines["session_tokens"] = format_tokens(sess["tokens"])
                lines["tokens_per_min"] = format_tokens(sess["tokens_per_min"])
                lines["session_duration"] = f"{sess['duration_min']:.0f}"
            lines["today_tokens"] = format_tokens(self.local_data.get("today_tokens", 0))

        if self.error_msg:
            lines["error"] = self.error_msg

        tmp = CONKY_FILE.with_suffix(".tmp")
        tmp.write_text("\n".join(f"{k}={v}" for k, v in lines.items()) + "\n")
        tmp.replace(CONKY_FILE)

    def run(self):
        signal.signal(signal.SIGUSR1, lambda *_: self.refresh())
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        while True:
            self.refresh()
            time.sleep(REFRESH_SECONDS)


def setup_cookie():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    print("claude-usage-widget — First-time setup")
    print("=" * 40)
    print()
    print("To show your Claude usage, this daemon needs a browser cookie.")
    print()
    print("Steps:")
    print("  1. Open https://claude.ai/settings/usage in your browser")
    print("  2. Open DevTools (F12) -> Network tab -> reload the page")
    print("  3. Click on the 'usage' request -> Headers tab")
    print("  4. Copy the full 'Cookie' header value")
    print(f"  5. Paste it into: {COOKIE_FILE}")
    print()
    print(f"  Example: echo 'your-cookie-here' > \"{COOKIE_FILE}\"")
    print(f"           chmod 600 \"{COOKIE_FILE}\"")
    sys.exit(1)


def main():
    if not COOKIE_FILE.exists():
        setup_cookie()
    UsageDaemon().run()


if __name__ == "__main__":
    main()
