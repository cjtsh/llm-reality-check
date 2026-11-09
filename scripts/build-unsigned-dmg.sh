#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; OUT="$ROOT/dist"
rm -rf "$OUT" "$ROOT/LLM-Reality-Check-0.2.0-unsigned.dmg"; mkdir -p "$OUT"
cp -R "$ROOT/packaging/LLM Reality Check.app" "$OUT/LLM Reality Check.app"
chmod +x "$OUT/LLM Reality Check.app/Contents/MacOS/llm-reality-check"
mkdir -p "$OUT/LLM Reality Check.app/Contents/Resources/runtime"
mkdir -p "$OUT/LLM Reality Check.app/Contents/Resources/AppIcon.iconset"
for size in 16 32 128 256 512; do sips -z "$size" "$size" "$ROOT/packaging/AppIcon.png" --out "$OUT/LLM Reality Check.app/Contents/Resources/AppIcon.iconset/icon_${size}x${size}.png" >/dev/null; done
for size in 32 256 512; do half=$((size / 2)); sips -z "$size" "$size" "$ROOT/packaging/AppIcon.png" --out "$OUT/LLM Reality Check.app/Contents/Resources/AppIcon.iconset/icon_${half}x${half}@2x.png" >/dev/null; done
iconutil -c icns "$OUT/LLM Reality Check.app/Contents/Resources/AppIcon.iconset" -o "$OUT/LLM Reality Check.app/Contents/Resources/AppIcon.icns"
rm -rf "$OUT/LLM Reality Check.app/Contents/Resources/AppIcon.iconset"
cp -R "$ROOT/app" "$ROOT/requirements.txt" "$ROOT/Dockerfile" "$ROOT/docker-compose.yml" "$ROOT/README.md" "$ROOT/LICENSE" "$ROOT/DISCLAIMER.md" "$OUT/LLM Reality Check.app/Contents/Resources/runtime/"
cp "$ROOT/README.md" "$ROOT/LICENSE" "$ROOT/DISCLAIMER.md" "$OUT/"
ln -s /Applications "$OUT/Applications"
hdiutil create -volname "LLM Reality Check 0.2.0" -srcfolder "$OUT" -ov -format UDZO "$ROOT/LLM-Reality-Check-0.2.0-unsigned.dmg"
echo "Created $ROOT/LLM-Reality-Check-0.2.0-unsigned.dmg"
