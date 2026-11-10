#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist"
APP="$OUT/LLM Reality Check.app"
CONTENTS="$APP/Contents"
RUNTIME="$CONTENTS/Resources/runtime"
DMG="$ROOT/LLM-Reality-Check-0.3.3-unsigned.dmg"
DMG_ROOT="$ROOT/build/dmg"

command -v uv >/dev/null || { echo "uv is required to build the app runtime" >&2; exit 1; }
rm -rf "$OUT" "$DMG"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources" "$RUNTIME"

cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>llm-reality-check</string>
  <key>CFBundleIdentifier</key><string>com.cjtsh.llm-reality-check</string>
  <key>CFBundleName</key><string>LLM Reality Check</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.3.3</string>
  <key>CFBundleVersion</key><string>0.3.3</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

cat > "$CONTENTS/MacOS/llm-reality-check" <<'LAUNCHER'
#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.docker/bin:$PATH"
CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME="$CONTENTS/Resources/runtime"
export LLM_REALITY_CHECK_DATA_DIR="$HOME/Library/Application Support/LLM Reality Check"
mkdir -p "$LLM_REALITY_CHECK_DATA_DIR"
cd "$RUNTIME"
exec "$RUNTIME/python/bin/python3.12" -m app --host 127.0.0.1 --port 8765 --open-browser
LAUNCHER
chmod +x "$CONTENTS/MacOS/llm-reality-check"

ICONSET="$CONTENTS/Resources/AppIcon.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do sips -z "$size" "$size" "$ROOT/packaging/AppIcon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null; done
for size in 32 256 512; do half=$((size / 2)); sips -z "$size" "$size" "$ROOT/packaging/AppIcon.png" --out "$ICONSET/icon_${half}x${half}@2x.png" >/dev/null; done
iconutil -c icns "$ICONSET" -o "$CONTENTS/Resources/AppIcon.icns"
rm -rf "$ICONSET"

cp -R "$ROOT/app" "$ROOT/requirements.txt" "$ROOT/README.md" "$ROOT/LICENSE" "$ROOT/DISCLAIMER.md" "$RUNTIME/"
uv python install --no-bin --install-dir "$OUT/python-build" 3.12.12
PYTHON_DIR="$(find "$OUT/python-build" -mindepth 1 -maxdepth 1 -type d -name 'cpython-3.12.12-*')"
mv "$PYTHON_DIR" "$RUNTIME/python"
uv pip install --python "$RUNTIME/python/bin/python3.12" \
  --target "$RUNTIME/python/lib/python3.12/site-packages" -r "$RUNTIME/requirements.txt"
find "$RUNTIME" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$RUNTIME" -name '._*' -type f -delete
rm -rf "$DMG_ROOT"
mkdir -p "$DMG_ROOT"
cp -R "$APP" "$DMG_ROOT/"
ln -s /Applications "$DMG_ROOT/Applications"
cp "$ROOT/README.md" "$ROOT/LICENSE" "$ROOT/DISCLAIMER.md" "$DMG_ROOT/"
hdiutil create -volname "LLM Reality Check 0.3.3" -srcfolder "$DMG_ROOT" -ov -format UDZO "$DMG"
echo "Created $DMG"
