# claude-usage-widget

Headless daemon that fetches your [Claude AI](https://claude.ai) usage limits and writes them to a file for desktop dashboards (Conky, Waybar, etc.).

![Python](https://img.shields.io/badge/python-3.8+-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)

## What it does

A small Python daemon polls Anthropic's OAuth usage endpoint (`api.anthropic.com/api/oauth/usage`) every few minutes and writes key=value pairs to `~/.config/claude-usage-widget/conky.txt`. Your desktop bar or Conky panel reads that file and displays the values however you like.

Authentication reuses Claude Code's own OAuth token from `~/.claude/.credentials.json`, which Claude Code keeps auto-refreshed, so there is no cookie to paste and nothing to renew by hand.

No GTK, no tray icon, no XEmbed/SNI dependencies — works natively on Wayland (Sway, labwc, Hyprland, …) and X11 alike.

### Data exposed

| Key | Description |
|---|---|
| `session_pct` / `session_reset` | 5-hour session utilization and reset countdown |
| `weekly_pct` / `weekly_reset` | 7-day weekly utilization and reset countdown |
| `session_resets_at` / `weekly_resets_at` | Raw ISO 8601 reset timestamps as returned by the API (only present after a successful fetch). Lets consumers compute absolute local reset times, e.g. for an on-click popup showing "resets today at 15:33" |
| `extra_pct` / `extra_used` / `extra_limit` / `extra_display` / `extra_enabled` / `extra_reason` | Pay-as-you-go extra usage. Emitted even when disabled: `extra_enabled=0`, `extra_display` gains an `(off)` suffix, and `extra_reason` carries the API's `disabled_reason`. `extra_pct` is utilization of the monthly cap, but pegs to `100` when the credit pool is exhausted (`out_of_credits`), since the bar then means "fully spent". |
| `session_tokens` / `tokens_per_min` / `session_duration` | Local Claude Code session throughput |
| `today_tokens` | Total tokens across all local sessions today |
| `error` | Set when the daemon hits an API error (e.g. expired login) |

The daemon also reads your local Claude Code session JSONL files for the throughput stats, so those work even if the API call fails.

## Conky integration

Add to your `conky.conf`:

```lua
${exec awk -F= '/^session_pct=/{print $2}' ~/.config/claude-usage-widget/conky.txt}%
${exec awk -F= '/^weekly_pct=/{print $2}' ~/.config/claude-usage-widget/conky.txt}%
${exec awk -F= '/^extra_display=/{print $2}' ~/.config/claude-usage-widget/conky.txt}
${exec awk -F= '/^tokens_per_min=/{print $2}' ~/.config/claude-usage-widget/conky.txt}/min
```

## Waybar integration

Add a custom module to `~/.config/waybar/config`:

```jsonc
"modules-right": ["custom/claude", /* ... */],

"custom/claude": {
    "exec": "~/.config/waybar/claude-status.sh",
    "return-type": "json",
    "interval": 30,
    "format": "\uf544 {}"
}
```

The `\uf544` glyph is Font Awesome's "robot" — requires a Font Awesome (or Nerd Font) family in your Waybar style.

Then `~/.config/waybar/claude-status.sh`:

```sh
#!/bin/sh
awk -F= '
/^session_pct=/{s=$2}
/^weekly_pct=/{w=$2}
/^extra_display=/{e=$2}
/^session_tokens=/{t=$2}
/^tokens_per_min=/{m=$2}
END{
  printf "{\"text\": \"%s%%\", \"tooltip\": \"Session: %s%%\\nWeekly: %s%%\\nExtra: %s\\nTokens: %s (%s/min)\"}", s, s, w, e, t, m
}' ~/.config/claude-usage-widget/conky.txt 2>/dev/null
```

`chmod +x` it. Optional: add CSS class states (`ok`/`warning`/`critical`) by branching on `s` in the script and emitting a `class` field.

## Requirements

- Python 3.8+
- Standard library only — no GTK, no Cairo, no GObject

## Install

```bash
git clone https://github.com/hibbes/claude-usage-widget.git
cd claude-usage-widget
chmod +x setup.sh
./setup.sh
```

## Authentication

The daemon reuses **Claude Code's** OAuth token, so there is nothing to set up
beyond having logged in to Claude Code at least once:

```bash
claude        # run it once; /login if prompted
```

This creates `~/.claude/.credentials.json`. Claude Code keeps the token
refreshed in place; the daemon re-reads it every cycle, so it never goes stale
while you use Claude Code.

### Legacy cookie fallback (optional)

If `~/.claude/.credentials.json` is absent, the daemon falls back to the old
claude.ai browser-cookie method. Grab the `Cookie` header from
[claude.ai/settings/usage](https://claude.ai/settings/usage) via DevTools and
save it:

```bash
echo 'your-cookie-value' > ~/.config/claude-usage-widget/cookie
chmod 600 ~/.config/claude-usage-widget/cookie
```

This cookie expires periodically; the OAuth token does not, so prefer it.

## Signals

- `SIGUSR1` → immediate refresh (skip the 60s wait)
- `SIGTERM` → clean exit

## How it works

- Calls `api.anthropic.com/api/oauth/usage` every 5 minutes (rate-limit friendly) with Claude Code's OAuth bearer token, and refreshes local token-throughput stats every 60 seconds
- On any transient API error (429, 500, 529, brief auth-refresh blips), keeps the last good numbers on the bar instead of blanking; a dash appears only when there is no data at all (cold start or sustained outage)
- Re-reads `~/.claude/.credentials.json` on each API call, so token refreshes are picked up automatically
- Falls back to the claude.ai cookie endpoint only if no Claude Code token is present
- Reads local Claude Code session JSONL files for token throughput stats
- Atomically writes `~/.config/claude-usage-widget/conky.txt` (tmp + rename, so readers never see a half-written file)

## Security

- The OAuth token is read (never written) from Claude Code's own `~/.claude/.credentials.json`; no credentials are copied or stored by this tool
- The optional fallback cookie lives in `~/.config/claude-usage-widget/cookie` with `600` permissions and is in `.gitignore`, never committed
- No data is sent anywhere except to `api.anthropic.com` (and `claude.ai` for the legacy fallback)

## License

MIT
