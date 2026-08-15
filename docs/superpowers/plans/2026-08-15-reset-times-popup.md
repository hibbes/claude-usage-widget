# Reset Times Popup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clicking the Waybar custom/claude module shows a mako notification with absolute local reset times plus live remaining duration for the session (5h) and weekly (7d) limits.

**Architecture:** The daemon passes the API's raw ISO `resets_at` timestamps through into `conky.txt` (two new keys). A new local POSIX-sh click handler reads them, formats absolute + relative times at click time with GNU `date`, and calls `notify-send`. Waybar wires the handler via `on-click`. Conky is untouched.

**Tech Stack:** Python 3 (stdlib only, existing daemon), POSIX sh, GNU date, notify-send/mako, Waybar JSON config.

**Spec:** `docs/superpowers/specs/2026-08-15-reset-times-popup-design.md`

## Global Constraints

- Existing `conky.txt` keys must not change (conky and `claude-status.sh` parse them).
- New keys: exactly `session_resets_at` and `weekly_resets_at`, raw ISO 8601 as returned by the API, written only when present.
- Click script is local config (`~/.config/waybar-labwc/`), NOT part of this repo; the repo only documents the keys.
- User-facing strings in the notification are German; no em or en dashes anywhere.
- Notification: `notify-send -a "Claude Usage" -t 12000 "Claude-Limits" <body>`.
- Tests must be deterministic: pin `TZ=Europe/Berlin`, inject "now" via `CLAUDE_USAGE_NOW` (epoch seconds), never assert locale-dependent weekday names.
- Process restarts follow the soft-restart pattern: `setsid -f ... </dev/null >/dev/null 2>&1`, never touch labwc. Avoid pkill self-match with a bracket in the pattern (`[.]`), see feedback_pkill_self_match.

---

### Task 1: Click script with test (TDD)

**Files:**
- Create: `~/.config/waybar-labwc/test-claude-reset-popup.sh`
- Create: `~/.config/waybar-labwc/claude-reset-popup.sh`

**Interfaces:**
- Consumes: `~/.config/claude-usage-widget/conky.txt` key=value lines; keys `session_pct`, `weekly_pct`, `session_resets_at`, `weekly_resets_at`, `error`.
- Produces: executable `claude-reset-popup.sh [--print]`; env overrides `CLAUDE_USAGE_FILE`, `CLAUDE_USAGE_NOW`. Task 3 wires this path into Waybar.

- [ ] **Step 1: Write the failing test**

Write `~/.config/waybar-labwc/test-claude-reset-popup.sh`:

```sh
#!/bin/sh
# Tests for claude-reset-popup.sh (--print mode). Run directly; exit 0 = green.
# Clock pinned to 2026-08-15T10:00:00Z (12:00 CEST), TZ pinned to Europe/Berlin.

DIR=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$DIR/claude-reset-popup.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
NOW_EPOCH=$(date -u -d '2026-08-15T10:00:00Z' +%s)
pass=0 fail=0

run() {
    TZ=Europe/Berlin CLAUDE_USAGE_NOW="$NOW_EPOCH" CLAUDE_USAGE_FILE="$1" \
        sh "$SCRIPT" --print
}

expect() {  # expect <label> <needle> <haystack>
    case "$3" in
        *"$2"*) pass=$((pass + 1)) ;;
        *) fail=$((fail + 1))
           printf 'FAIL: %s\n  wanted substring: %s\n  got: %s\n' "$1" "$2" "$3" ;;
    esac
}

# 1. Normal case: session resets today, weekly on another day
cat > "$TMP/normal.txt" << 'EOF'
session_pct=81
session_reset=3h 33m
weekly_pct=56
weekly_reset=4d 10h
session_resets_at=2026-08-15T13:33:00+00:00
weekly_resets_at=2026-08-19T20:00:00+00:00
EOF
out=$(run "$TMP/normal.txt")
expect "session absolute local time" "Session: 81%, Reset heute um 15:33" "$out"
expect "session remaining"           "(in 3h 33m)" "$out"
expect "weekly absolute local time"  "19.08. um 22:00" "$out"
expect "weekly remaining"            "(in 4d 10h)" "$out"

# 2. Reset on the next calendar day says "morgen"
cat > "$TMP/morgen.txt" << 'EOF'
session_pct=12
session_resets_at=2026-08-16T05:00:00+00:00
weekly_resets_at=2026-08-19T20:00:00+00:00
EOF
out=$(run "$TMP/morgen.txt")
expect "next-day reset says morgen" "Reset morgen um 07:00 (in 19h 0m)" "$out"

# 3. Elapsed timestamp is marked stale instead of a negative duration
cat > "$TMP/stale.txt" << 'EOF'
session_pct=99
session_resets_at=2026-08-15T09:00:00+00:00
weekly_resets_at=2026-08-19T20:00:00+00:00
EOF
out=$(run "$TMP/stale.txt")
expect "elapsed reset marked stale" "abgelaufen" "$out"

# 4. Daemon error line, no reset keys: error is surfaced
cat > "$TMP/error.txt" << 'EOF'
error=Auth expired, run `claude` to refresh login
EOF
out=$(run "$TMP/error.txt")
expect "daemon error shown" "Auth expired" "$out"

# 5. Missing file
out=$(run "$TMP/does-not-exist.txt")
expect "missing file reported" "läuft nicht" "$out"

# 6. Old daemon without the new keys and without error line
cat > "$TMP/oldkeys.txt" << 'EOF'
session_pct=81
weekly_pct=56
EOF
out=$(run "$TMP/oldkeys.txt")
expect "missing keys reported" "Noch keine Reset-Daten" "$out"

# 7. Missing percentage falls back to "?"
cat > "$TMP/nopct.txt" << 'EOF'
session_resets_at=2026-08-15T13:33:00+00:00
weekly_resets_at=2026-08-19T20:00:00+00:00
EOF
out=$(run "$TMP/nopct.txt")
expect "missing pct shows ?" "Session: ?%" "$out"

printf '%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
```

Then `chmod +x` it.

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.config/waybar-labwc/test-claude-reset-popup.sh`
Expected: FAILs (script missing, `sh` cannot open it), summary line shows `0 passed, 10 failed`, exit code 1.

- [ ] **Step 3: Write the script**

Write `~/.config/waybar-labwc/claude-reset-popup.sh`:

```sh
#!/bin/sh
# Waybar on-click handler for the custom/claude module.
# Shows when the Claude session (5h) and weekly (7d) limits reset:
# absolute local time plus remaining duration, as a mako notification.
#
# Usage: claude-reset-popup.sh [--print]
#   --print   write the text to stdout instead of notify-send
#
# Test overrides:
#   CLAUDE_USAGE_FILE  conky.txt path (default: ~/.config/claude-usage-widget/conky.txt)
#   CLAUDE_USAGE_NOW   "now" as epoch seconds (default: date +%s)

FILE="${CLAUDE_USAGE_FILE:-$HOME/.config/claude-usage-widget/conky.txt}"
NOW="${CLAUDE_USAGE_NOW:-$(date +%s)}"
PRINT=0
[ "${1:-}" = "--print" ] && PRINT=1

show() {
    if [ "$PRINT" = 1 ]; then
        printf '%s\n' "$1"
    else
        notify-send -a "Claude Usage" -t 12000 "Claude-Limits" "$1"
    fi
}

if [ ! -f "$FILE" ]; then
    show "claude-usage-widget läuft nicht (fehlt: $FILE)"
    exit 0
fi

session_pct= weekly_pct= session_resets_at= weekly_resets_at= error=
while IFS='=' read -r key val; do
    case "$key" in
        session_pct)       session_pct=$val ;;
        weekly_pct)        weekly_pct=$val ;;
        session_resets_at) session_resets_at=$val ;;
        weekly_resets_at)  weekly_resets_at=$val ;;
        error)             error=$val ;;
    esac
done < "$FILE"

if [ -z "$session_resets_at" ] && [ -z "$weekly_resets_at" ]; then
    show "${error:-Noch keine Reset-Daten vom Daemon}"
    exit 0
fi

# fmt_line <Name> <pct> <iso> -> "Name: 81%, Reset heute um 15:33 (in 3h 33m)"
fmt_line() {
    name=$1 pct=${2:-?} iso=$3
    if [ -z "$iso" ]; then
        printf '%s: keine Daten' "$name"
        return
    fi
    epoch=$(date -d "$iso" +%s 2>/dev/null)
    if [ -z "$epoch" ]; then
        printf '%s: Zeitstempel unlesbar (%s)' "$name" "$iso"
        return
    fi

    t=$(date -d "$iso" +%H:%M)
    day=$(date -d "$iso" +%Y-%m-%d)
    if [ "$day" = "$(date -d "@$NOW" +%Y-%m-%d)" ]; then
        abs="heute um $t"
    elif [ "$day" = "$(date -d "@$((NOW + 86400))" +%Y-%m-%d)" ]; then
        abs="morgen um $t"
    else
        abs="$(date -d "$iso" +'%a %d.%m.') um $t"
    fi

    diff=$((epoch - NOW))
    if [ "$diff" -le 0 ]; then
        printf '%s: %s%%, Reset %s (abgelaufen, Daten evtl. veraltet)' \
            "$name" "$pct" "$abs"
        return
    fi
    h=$((diff / 3600))
    m=$(( (diff % 3600) / 60 ))
    if [ "$h" -ge 24 ]; then
        rel="$((h / 24))d $((h % 24))h"
    elif [ "$h" -gt 0 ]; then
        rel="${h}h ${m}m"
    else
        rel="${m}m"
    fi
    printf '%s: %s%%, Reset %s (in %s)' "$name" "$pct" "$abs" "$rel"
}

body="$(fmt_line "Session" "$session_pct" "$session_resets_at")
$(fmt_line "Woche" "$weekly_pct" "$weekly_resets_at")"
show "$body"
```

Then `chmod +x` it.

- [ ] **Step 4: Run test to verify it passes**

Run: `~/.config/waybar-labwc/test-claude-reset-popup.sh`
Expected: `10 passed, 0 failed`, exit code 0. Also run `sh -n` on both files as a syntax gate.

(No git commit: both files live in `~/.config/waybar-labwc/`, which is not a git repository. The test file itself is the durable QA gate.)

---

### Task 2: Daemon passes raw reset timestamps through

**Files:**
- Modify: `/home/neo/claude-usage-widget/claude-usage-widget.py` (in `write_conky`, the `five`/`seven` blocks around lines 237-244)
- Modify: `/home/neo/claude-usage-widget/README.md` (key documentation)

**Interfaces:**
- Consumes: `five_hour.resets_at` / `seven_day.resets_at` from the OAuth usage API response (ISO 8601 strings).
- Produces: `session_resets_at=` and `weekly_resets_at=` lines in `conky.txt`, exactly the keys Task 1's script reads.

- [ ] **Step 1: Extend write_conky**

In `write_conky`, change the two blocks to:

```python
            five = d.get("five_hour")
            if five:
                lines["session_pct"] = f"{five['utilization']:.0f}"
                lines["session_reset"] = format_reset(five.get("resets_at"))
                if five.get("resets_at"):
                    lines["session_resets_at"] = five["resets_at"]
            seven = d.get("seven_day")
            if seven:
                lines["weekly_pct"] = f"{seven['utilization']:.0f}"
                lines["weekly_reset"] = format_reset(seven.get("resets_at"))
                if seven.get("resets_at"):
                    lines["weekly_resets_at"] = seven["resets_at"]
```

- [ ] **Step 2: Syntax gate**

Run: `python3 -m py_compile /home/neo/claude-usage-widget/claude-usage-widget.py && echo OK`
Expected: `OK`.

- [ ] **Step 3: Document the keys in README**

Find the section of `README.md` that lists the `conky.txt` keys (it mentions `session_reset`, `weekly_reset` etc.) and add, matching the surrounding format:

```
session_resets_at / weekly_resets_at: raw ISO 8601 reset timestamps as
returned by the API (only present after a successful fetch). Lets consumers
compute absolute local times, e.g. a Waybar on-click reset popup.
```

- [ ] **Step 4: Deploy the daemon (soft restart)**

```bash
pkill -f 'claude-usage-widget[.]py'
setsid -f /home/neo/claude-usage-widget/claude-usage-widget.py </dev/null >/dev/null 2>&1
sleep 3
pgrep -af 'claude-usage-widget[.]py'
```

Expected: exactly one new PID running `/home/neo/claude-usage-widget/claude-usage-widget.py`.

- [ ] **Step 5: Verify live data**

```bash
sleep 8
grep resets_at ~/.config/claude-usage-widget/conky.txt
```

Expected: both `session_resets_at=` and `weekly_resets_at=` with ISO timestamps. (Fresh daemon has no cached data, so the first loop iteration calls the API immediately.) If the first poll hit a transient API error, send `pkill -USR1 -f 'claude-usage-widget[.]py'` and re-check.

Also confirm the old consumers still parse: `~/.config/waybar-labwc/claude-status.sh` outputs JSON with the same percentages as `conky.txt`.

- [ ] **Step 6: Commit and push**

```bash
cd /home/neo/claude-usage-widget
git add claude-usage-widget.py README.md
git commit -m "Expose raw reset timestamps in conky.txt

session_resets_at / weekly_resets_at carry the API's ISO resets_at
through unmodified, so display consumers can show absolute local reset
times (used by a Waybar on-click popup). The pre-formatted relative
keys stay unchanged for existing conky/waybar parsers."
git push
```

(Commit footer per repo convention: Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>)

---

### Task 3: Wire Waybar on-click, reload, end-to-end check

**Files:**
- Modify: `~/.config/waybar-labwc/config` (custom/claude block, lines 48-53)

**Interfaces:**
- Consumes: `~/.config/waybar-labwc/claude-reset-popup.sh` from Task 1.
- Produces: user-visible click behavior; nothing downstream.

- [ ] **Step 1: Add the click handler**

Insert ONLY the `on-click` line into the existing `custom/claude` block. All
other lines stay byte-identical; in the real config the robot glyph is the
ASCII escape sequence backslash-uf544, not a literal glyph, so do not touch the `format` line.
Result (glyph shown literally here):

```json
    "custom/claude": {
        "exec": "~/.config/waybar-labwc/claude-status.sh",
        "return-type": "json",
        "interval": 30,
        "format": " {}",
        "on-click": "~/.config/waybar-labwc/claude-reset-popup.sh"
    }
```

- [ ] **Step 2: Validate JSON**

Run: `python3 -m json.tool ~/.config/waybar-labwc/config >/dev/null && echo OK`
Expected: `OK`.

- [ ] **Step 3: Restart Waybar (soft restart, config has no live reload wired)**

```bash
pkill -x waybar
setsid -f waybar -c /home/neo/.config/waybar-labwc/config \
    -s /home/neo/.config/waybar-labwc/style.css </dev/null >/dev/null 2>&1
sleep 3
pgrep -x waybar
```

Expected: one waybar PID, still alive after the sleep (no crash loop). If `WAYLAND_DISPLAY`/`XDG_RUNTIME_DIR` are missing in the shell, export them first (`WAYLAND_DISPLAY=wayland-0`, `XDG_RUNTIME_DIR=/run/user/1000`).

- [ ] **Step 4: End-to-end notification check**

```bash
~/.config/waybar-labwc/claude-reset-popup.sh --print
~/.config/waybar-labwc/claude-reset-popup.sh
makoctl list | grep -A2 'Claude-Limits'
```

Expected: `--print` shows two lines with real current data (absolute time + remaining); the real invocation pops a mako notification; `makoctl list` shows it. A `grim` screenshot of the bar area may additionally confirm waybar is rendering.

- [ ] **Step 5: Tick plan checkboxes, commit plan updates**

```bash
cd /home/neo/claude-usage-widget
git add docs/superpowers/plans/2026-08-15-reset-times-popup.md
git commit -m "Tick implementation plan for reset-times popup"
git push
```

(The physical mouse click cannot be simulated on this box, wtype/ydotool are not installed. Config validation + script E2E + live waybar cover everything up to the click; the user performs the first real click.)
