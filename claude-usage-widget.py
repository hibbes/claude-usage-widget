#!/usr/bin/env python3
"""claude-usage-widget: daemon that writes Claude usage to conky.txt.

Usage data comes from Claude Code's own OAuth token
(~/.claude/.credentials.json), which Claude Code keeps auto-refreshed, via
https://api.anthropic.com/api/oauth/usage. No browser cookie needed; an
optional claude.ai cookie is used only as a legacy fallback.

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
CREDENTIALS_FILE = CLAUDE_DIR / ".credentials.json"
REFRESH_SECONDS = 60  # local-stats / conky-write cadence
API_INTERVAL_SECONDS = 300  # min seconds between calls to the rate-limited usage API
OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
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

    @staticmethod
    def read_oauth_token():
        """Read Claude Code's OAuth access token. Claude Code keeps it
        refreshed in place, so we re-read it every cycle (never cache)."""
        try:
            data = json.loads(CREDENTIALS_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return (data.get("claudeAiOauth") or {}).get("accessToken") or None

    def read_cookie(self):
        if not COOKIE_FILE.exists():
            return None
        return COOKIE_FILE.read_text().strip() or None

    @staticmethod
    def _get_json(url, headers):
        req = urllib.request.Request(url)
        for key, value in headers.items():
            req.add_header(key, value)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    def fetch_usage_oauth(self, token):
        return self._get_json(OAUTH_USAGE_URL, {
            "Authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
            "Accept": "application/json",
            "User-Agent": "claude-usage-widget",
        })

    def fetch_usage_cookie(self):
        """Legacy fallback: claude.ai web API via browser cookie."""
        cookie = self.read_cookie()
        if not cookie:
            return None
        headers = {
            "Cookie": cookie,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://claude.ai/settings/usage",
        }
        if not self.org_id:
            orgs = self._get_json(f"{API_BASE}/organizations", headers)
            self.org_id = orgs[0]["uuid"] if orgs else None
        if not self.org_id:
            return None
        return self._get_json(
            f"{API_BASE}/organizations/{self.org_id}/usage", headers
        )

    def fetch_usage(self):
        token = self.read_oauth_token()
        if token:
            return self.fetch_usage_oauth(token)
        data = self.fetch_usage_cookie()
        if data is None:
            self.error_msg = "Not logged in to Claude Code and no cookie"
        return data

    def refresh(self, fetch_api=True):
        if fetch_api:
            try:
                data = self.fetch_usage()
                if data:
                    self.usage_data = data
                    self.error_msg = None
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    self.error_msg = "Auth expired, run `claude` to refresh login"
                elif e.code == 429:
                    # Rate limited: transient. Keep the last good numbers on the
                    # bar instead of blanking; only flag it if we have nothing yet.
                    self.error_msg = None if self.usage_data else "Rate limited"
                else:
                    self.error_msg = f"HTTP {e.code}"
            except Exception as e:
                # Network blip etc.: keep the last good numbers if we have them.
                self.error_msg = None if self.usage_data else str(e)

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
        signal.signal(signal.SIGUSR1, lambda *_: self.refresh(fetch_api=True))
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        last_api = 0.0
        while True:
            now = time.monotonic()
            # Poll often until we have data, then throttle to spare the rate limit.
            interval = API_INTERVAL_SECONDS if self.usage_data else REFRESH_SECONDS
            do_api = (now - last_api) >= interval
            if do_api:
                last_api = now
            self.refresh(fetch_api=do_api)
            time.sleep(REFRESH_SECONDS)


def print_setup_help():
    print("claude-usage-widget setup")
    print("=" * 40)
    print()
    print("This widget reads your Claude usage from Claude Code's own")
    print("auto-refreshed OAuth token at:")
    print(f"  {CREDENTIALS_FILE}")
    print()
    print("That file appears once you have logged in to Claude Code:")
    print("  claude        # run it once, /login if prompted")
    print()
    print("Then just start this daemon; no browser or cookie required.")
    print()
    print("Optional legacy fallback: a claude.ai 'Cookie' header value at")
    print(f"  {COOKIE_FILE}")


def main():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not UsageDaemon.read_oauth_token() and not COOKIE_FILE.exists():
        print_setup_help()
        sys.exit(1)
    UsageDaemon().run()


if __name__ == "__main__":
    main()
