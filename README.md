# claude-usage-widget

Headless daemon that fetches your [Claude AI](https://claude.ai) usage limits and writes them to a file for desktop dashboards (Conky, Waybar, etc.).

![Python](https://img.shields.io/badge/python-3.8+-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)

## What it does

A small Python daemon polls `claude.ai/api/organizations/{org_id}/usage` every 60 seconds and writes key=value pairs to `~/.config/claude-usage-widget/conky.txt`. Your desktop bar or Conky panel reads that file and displays the values however you like.

No GTK, no tray icon, no XEmbed/SNI dependencies — works natively on Wayland (Sway, labwc, Hyprland, …) and X11 alike.

### Data exposed

| Key | Description |
|---|---|
| `session_pct` / `session_reset` | 5-hour session utilization and reset countdown |
| `weekly_pct` / `weekly_reset` | 7-day weekly utilization and reset countdown |
| `extra_pct` / `extra_used` / `extra_limit` / `extra_display` | Pay-as-you-go credits |
| `session_tokens` / `tokens_per_min` / `session_duration` | Local Claude Code session throughput |
| `today_tokens` | Total tokens across all local sessions today |
| `error` | Set when the daemon hits an API error (e.g. expired cookie) |

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

## Cookie setup

The daemon reads usage from the claude.ai API using a browser cookie.

1. Open [claude.ai/settings/usage](https://claude.ai/settings/usage) in your browser
2. Open DevTools (`F12`) → **Network** tab → reload the page
3. Click on the `usage` request → **Headers** tab
4. Copy the full `Cookie` header value
5. Save it:

```bash
echo 'your-cookie-value' > ~/.config/claude-usage-widget/cookie
chmod 600 ~/.config/claude-usage-widget/cookie
```

> **Note:** The cookie expires periodically. When the daemon writes `error=Auth expired — update cookie` to `conky.txt`, repeat the steps above.

## Signals

- `SIGUSR1` → immediate refresh (skip the 60s wait)
- `SIGTERM` → clean exit

## How it works

- Calls `claude.ai/api/organizations/{org_id}/usage` every 60 seconds
- Auto-detects your organization ID on first run
- Reads local Claude Code session JSONL files for token throughput stats
- Atomically writes `~/.config/claude-usage-widget/conky.txt` (tmp + rename, so readers never see a half-written file)

## Security

- Cookie is stored in `~/.config/claude-usage-widget/cookie` with `600` permissions
- Cookie file is in `.gitignore` — never committed
- No data is sent anywhere except to `claude.ai`

## License

MIT
