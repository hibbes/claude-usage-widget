# claude-usage-widget

System tray widget that shows your [Claude AI](https://claude.ai) usage limits at a glance.

![Python](https://img.shields.io/badge/python-3.8+-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)

## What it shows

- **Session limit (5h)** — current utilization with reset countdown
- **Weekly limit (7d)** — overall weekly usage
- **Model-specific limits** — Opus, Sonnet weekly caps (when applicable)
- **Extra usage** — dollar amount used vs. monthly cap

The tray icon always shows your **session (5h) utilization** percentage and changes color accordingly:
- 🟢 Green: < 50%
- 🟡 Yellow: 50–80%
- 🔴 Red: > 80%

Left-click the tray icon for a detail popup. Right-click for menu.

### Local session stats

Reads your local Claude Code session files to show:
- **Tokens used** in the active session
- **Tokens per minute** throughput
- **Today's total** across all sessions

### Conky integration

Exports all data to `~/.config/claude-usage-widget/conky.txt` for use in [Conky](https://github.com/brndnmtthws/conky) dashboards. Add to your `conky.conf`:

```lua
${exec awk -F= '/^session_pct=/{print $2}' ~/.config/claude-usage-widget/conky.txt}%
${exec awk -F= '/^weekly_pct=/{print $2}' ~/.config/claude-usage-widget/conky.txt}%
${exec awk -F= '/^extra_display=/{print $2}' ~/.config/claude-usage-widget/conky.txt}
${exec awk -F= '/^tokens_per_min=/{print $2}' ~/.config/claude-usage-widget/conky.txt}/min
```

Available keys: `session_pct`, `session_reset`, `weekly_pct`, `weekly_reset`, `extra_pct`, `extra_used`, `extra_limit`, `extra_display`, `session_tokens`, `tokens_per_min`, `session_duration`, `today_tokens`.

## Requirements

- Python 3.8+
- GTK 3 with GObject introspection (`python3-gi`, `python3-gi-cairo`)
- A system tray (works with IceWM, LXQt, XFCE, MATE, KDE, etc.)

### Install dependencies

**Debian/Ubuntu:**
```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0
```

**Fedora:**
```bash
sudo dnf install python3-gobject python3-cairo
```

**Arch:**
```bash
sudo pacman -S python-gobject python-cairo
```

**Gentoo:**
```bash
sudo emerge dev-python/pygobject
```

## Install

```bash
git clone https://github.com/hibbes/claude-usage-widget.git
cd claude-usage-widget
chmod +x setup.sh
./setup.sh
```

## Cookie setup

The widget reads your usage from the claude.ai API using a browser cookie.

1. Open [claude.ai/settings/usage](https://claude.ai/settings/usage) in your browser
2. Open DevTools (`F12`) → **Network** tab → reload the page
3. Click on the `usage` request → **Headers** tab
4. Copy the full `Cookie` header value
5. Save it:

```bash
echo 'your-cookie-value' > ~/.config/claude-usage-widget/cookie
chmod 600 ~/.config/claude-usage-widget/cookie
```

> **Note:** The cookie expires periodically. When the icon turns red with an "Auth expired" tooltip, repeat the steps above to refresh it.

## Usage

```bash
# Run directly
claude-usage-widget

# Or just log out and back in (autostart is set up by setup.sh)
```

## How it works

- Calls `claude.ai/api/organizations/{org_id}/usage` every 60 seconds
- Auto-detects your organization ID on first run
- Reads local Claude Code session JSONL files for token throughput stats
- Renders a color-coded icon with the session (5h) utilization percentage
- Click popup shows all limits with progress bars and reset countdowns
- Writes `conky.txt` for desktop dashboard integration
- Zero external dependencies beyond GTK3

## Security

- Cookie is stored in `~/.config/claude-usage-widget/cookie` with `600` permissions
- Cookie file is in `.gitignore` — never committed
- No data is sent anywhere except to `claude.ai`

## License

MIT
