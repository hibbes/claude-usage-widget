# Reset times on click (Waybar popup)

Date: 2026-08-15
Status: approved (design options confirmed by Marek: click target = Waybar custom/claude module, display = mako notification)

## Goal

Clicking the Claude module in Waybar (robot icon + session percentage) shows when the
session (5h) and weekly (7d) limits reset: absolute local time plus live remaining
duration. The conky panel stays unchanged.

## Current state

The daemon already receives exact reset timestamps from
`https://api.anthropic.com/api/oauth/usage` (`five_hour.resets_at`,
`seven_day.resets_at`, ISO 8601) but only writes pre-formatted relative strings
(`session_reset=3h 33m`) to `conky.txt`. The Waybar module has no click handler.

## Changes

### 1. Daemon: pass through raw timestamps

`write_conky()` additionally emits, only when the API provided them:

```
session_resets_at=<ISO 8601 as returned by the API>
weekly_resets_at=<ISO 8601 as returned by the API>
```

Existing keys stay untouched, so conky and `claude-status.sh` are unaffected.
Values contain no `=`; the `IFS='='` readers keep working.

### 2. Click script (local config, not in this repo)

`~/.config/waybar-labwc/claude-reset-popup.sh`, POSIX sh:

- Reads `conky.txt` (path overridable via `CLAUDE_USAGE_FILE` for tests).
- Computes at click time, from the ISO timestamps via GNU `date`:
  - absolute local time: `heute um HH:MM`, `morgen um HH:MM`, otherwise
    `<Wochentag> DD.MM. um HH:MM` (system locale)
  - live remaining time in the daemon's style: `4d 10h`, `3h 34m`, `42m`;
    `jetzt` when already elapsed
- Notification via `notify-send -a "Claude Usage" -t 12000` (mako), body:

  ```
  Session: 81%, Reset heute um 15:30 (in 3h 34m)
  Woche: 56%, Reset Mi 19.08. um 22:00 (in 4d 10h)
  ```

- Error handling: missing file, `error=` line, or missing timestamp keys produce a
  notification explaining the state (daemon not running / daemon error text /
  no data yet) instead of silent failure.
- `--print` mode writes the same text to stdout instead of notifying (testing,
  terminal use).
- Deterministic tests: current time overridable via `CLAUDE_USAGE_NOW` (epoch
  seconds); tests pin `TZ`. Assertions avoid locale-dependent weekday names.

### 3. Waybar config

`custom/claude` gets `"on-click": "~/.config/waybar-labwc/claude-reset-popup.sh"`.

## Testing

`~/.config/waybar-labwc/test-claude-reset-popup.sh`: fixture-driven checks of
`--print` output (normal case, cross-day reset, elapsed reset, error line, missing
file, missing keys). Lives next to the script, not in /tmp, so it survives reboots
and acts as the durable QA gate.

Daemon change is a two-line passthrough: verified by syntax check plus live
inspection of `conky.txt` after restart.

## Deploy

1. Restart daemon (kill PID, relaunch detached via `setsid -f`, matching the labwc
   autostart line), force refresh with SIGUSR1, confirm new keys in `conky.txt`.
2. Reload Waybar so the click handler is active.
3. Update README (document the two new keys), commit, push.

## Out of scope

- No conky display changes (explicit user requirement).
- No reset display for extra usage (API exposes no reset timestamp there).
- No tooltip changes in `claude-status.sh`.
