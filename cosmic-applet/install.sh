#!/bin/sh
# Build and install the COSMIC panel applet for the current user.
# Afterwards add it via COSMIC Settings > Desktop > Panel > Configure panel applets ("Claude Usage").
set -e
cd "$(dirname "$0")"
cargo build --release
ID=io.github.hibbes.CosmicAppletClaudeUsage
install -Dm755 target/release/cosmic-applet-claude-usage "$HOME/.local/bin/cosmic-applet-claude-usage"
install -Dm644 "data/$ID.desktop" "$HOME/.local/share/applications/$ID.desktop"
install -Dm644 data/robot-symbolic.svg "$HOME/.local/share/icons/hicolor/scalable/apps/$ID-symbolic.svg"
echo "Installed. Add \"Claude Usage\" to the panel in COSMIC Settings > Desktop > Panel > Configure panel applets."
echo "After an update, restart it with: pkill -x cosmic-applet-claude-usage (the panel relaunches it)."
