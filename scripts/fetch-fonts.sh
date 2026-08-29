#!/usr/bin/env bash
# Vendors the webfonts the native bundle needs into static/vendor-fonts/.
#
# Run this once and commit the result. build-www.mjs copies the directory
# verbatim, so builds never touch the network.
#
# Noto Sans SC is deliberately NOT vendored: the full CJK face is 8-10MB, and
# the font stack in vocare-app.jsx falls through to PingFang SC on iOS and
# Noto Sans CJK on Android, both of which ship with the OS.

set -euo pipefail

cd "$(dirname "$0")/.."
OUT="static/vendor-fonts"
mkdir -p "$OUT"

# A modern browser UA is required, or Google serves legacy TTF instead of woff2.
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

# latin subset only — the UI chrome is English; CJK comes from system fonts.
CSS_URL='https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Space+Grotesk:wght@400;500;600;700&display=swap'

echo "Fetching font CSS…"
css=$(curl -sS -A "$UA" "$CSS_URL")

echo "$css" | grep -oE 'https://fonts\.gstatic\.com/[^)]+\.woff2' | sort -u | while read -r url; do
  name=$(basename "$url")
  echo "  $name"
  curl -sS -o "$OUT/$name" "$url"
done

# Rewrite the remote URLs to the local filenames, preserving unicode-range so
# the browser still only loads the subsets it needs.
echo "$css" | sed -E 's#https://fonts\.gstatic\.com/[^)]*/([^/)]+\.woff2)#./\1#g' > "$OUT/fonts.css"

echo
echo "Vendored $(find "$OUT" -name '*.woff2' | wc -l | tr -d ' ') font files into $OUT"
du -sh "$OUT"
