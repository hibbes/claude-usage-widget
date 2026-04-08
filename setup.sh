#!/bin/bash
# Install claude-usage-widget

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/claude-usage-widget"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DESKTOP_FILE="$AUTOSTART_DIR/claude-usage-widget.desktop"

echo "Installing claude-usage-widget..."

# Install script
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/claude-usage-widget.py" "$INSTALL_DIR/claude-usage-widget"
chmod +x "$INSTALL_DIR/claude-usage-widget"

# Create config dir
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"

# XDG autostart
mkdir -p "$AUTOSTART_DIR"
cat > "$DESKTOP_FILE" << 'EOF'
[Desktop Entry]
Type=Application
Name=Claude Usage Widget
Comment=System tray widget showing Claude AI usage limits
Exec=claude-usage-widget
Icon=utilities-system-monitor
Terminal=false
Categories=Utility;
X-GNOME-Autostart-enabled=true
EOF

echo "Installed to: $INSTALL_DIR/claude-usage-widget"
echo "Autostart:    $DESKTOP_FILE"
echo ""

# Cookie setup
if [ ! -f "$CONFIG_DIR/cookie" ]; then
    echo "Next step: set up your cookie."
    echo ""
    echo "  1. Open https://claude.ai/settings/usage in your browser"
    echo "  2. DevTools (F12) → Network → reload → click 'usage' request"
    echo "  3. Copy the Cookie header value, then run:"
    echo ""
    echo "     echo 'YOUR_COOKIE' > $CONFIG_DIR/cookie"
    echo "     chmod 600 $CONFIG_DIR/cookie"
    echo ""
else
    echo "Cookie already configured."
    echo "Run 'claude-usage-widget' or log out/in for autostart."
fi
