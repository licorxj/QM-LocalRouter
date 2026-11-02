#!/usr/bin/env bash
# Build the LocalRouter macOS installer (per-user .pkg).
#
# Pipeline (mirrors scripts\build-windows-installer.ps1):
#   1. Build the frontend (npm ci + npm run build)               [--skip-frontend]
#   2. Write version.json (git tag/commit) for the frozen exe
#   3. PyInstaller one-file build (dist/apirouter + dist/LocalRouter)
#                                                                [--skip-backend]
#   4. Generate LocalRouter.icns from frontend/public/logo.png (optional)
#   5. Assemble the LocalRouter.app bundle
#   6. pkgbuild (component) + productbuild (distribution) -> per-user pkg
#
# Output: dist/installer/LocalRouter-Setup-<version>-macos.pkg
# The pkg installs per-user (no admin) as ~/Applications/LocalRouter.app:
# the frozen exe keeps all runtime state (sqlite db, logs, .env) next to
# itself, so the app must live in a user-writable location - hence the
# distribution restricts destinations to the current user's home.
#
# The build is NOT signed/notarized (needs an Apple Developer ID): on first
# launch the user must approve the app once via System Settings > Privacy &
# Security, or clear the quarantine flag with
#   xattr -dr com.apple.quarantine ~/Applications/LocalRouter.app
#
# Usage: scripts/build-macos-installer.sh [--skip-frontend] [--skip-backend] [--version x.y.z]
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo"

SKIP_FRONTEND=0
SKIP_BACKEND=0
VERSION=""

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-frontend) SKIP_FRONTEND=1 ;;
    --skip-backend)  SKIP_BACKEND=1 ;;
    --version)       VERSION="${2:?--version requires a value}"; shift ;;
    *) echo "[ERROR] unknown argument: $1" >&2; exit 1 ;;
  esac
  shift
done

info() { echo "==> $*"; }
fail() { echo "[ERROR] $*" >&2; exit 1; }

# ---------------------------------------------------------------- 1. Version
tag_raw=""
if [ -z "$VERSION" ]; then
  tag_raw="$(git describe --tags --always 2>/dev/null || true)"
  if [ -z "$tag_raw" ]; then tag_raw="0.0.0-unknown"; fi
  case "$tag_raw" in v*) VERSION="${tag_raw#v}" ;; *) VERSION="$tag_raw" ;; esac
else
  tag_raw="v$VERSION"
fi
info "Repo: $repo"
info "Installer version: $VERSION"

# ---------------------------------------------------------------- 2. Python
py=""
for cand in backend/venv/bin/python backend/.venv/bin/python; do
  if [ -x "$cand" ]; then py="$cand"; break; fi
done
if [ -z "$py" ]; then py="python3"; fi
info "Python: $py"

# ---------------------------------------------------------------- 3. Frontend
if [ "$SKIP_FRONTEND" -ne 1 ]; then
  info "Building frontend (npm run build)..."
  (
    cd frontend
    if [ ! -d node_modules ]; then
      npm ci
    fi
    npm run build
  )
fi
if [ ! -f frontend/dist/index.html ]; then
  fail "frontend/dist/index.html not found. Build the frontend first (or drop --skip-frontend)."
fi

# ---------------------------------------------------------------- 4. version.json
git_commit="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then dirty=true; else dirty=false; fi
# UTF-8 without BOM: the frozen exe parses this with plain json.
printf '{\n  "git_tag": "%s",\n  "git_commit": "%s",\n  "git_dirty": %s\n}\n' \
  "$tag_raw" "$git_commit" "$dirty" > version.json

# ---------------------------------------------------------------- 5. PyInstaller
if [ "$SKIP_BACKEND" -ne 1 ]; then
  if ! "$py" -c "import litellm, webview" >/dev/null 2>&1; then
    info "Backend deps missing in selected Python; installing backend/requirements.txt..."
    "$py" -m pip install -r backend/requirements.txt
  fi
  if ! "$py" -m PyInstaller --version >/dev/null 2>&1; then
    info "Installing build requirements (pyinstaller)..."
    "$py" -m pip install -r requirements-build.txt
  fi
  info "PyInstaller one-file build..."
  "$py" -m PyInstaller build_pyinstaller.spec --noconfirm --clean
fi
if [ ! -f dist/apirouter ]; then
  fail "dist/apirouter not found. Run PyInstaller first (or drop --skip-backend)."
fi
if [ ! -f dist/LocalRouter ]; then
  fail "dist/LocalRouter not found - the desktop shell binary is built by build_pyinstaller.spec."
fi

# ---------------------------------------------------------------- 6. Icon
# frontend/public/logo.png -> build/macos/LocalRouter.icns via sips+iconutil;
# skipped silently when either tool is unavailable (default icon then).
mkdir -p build/macos
app_icon=""
if [ -f frontend/public/logo.png ] && command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
  iconset="build/macos/LocalRouter.iconset"
  rm -rf "$iconset"
  mkdir -p "$iconset"
  png_ok=1
  for spec in "16 icon_16x16" "32 icon_16x16@2x" "32 icon_32x32" "64 icon_32x32@2x" \
              "128 icon_128x128" "256 icon_128x128@2x" "256 icon_256x256" "512 icon_256x256@2x" \
              "512 icon_512x512" "1024 icon_512x512@2x"; do
    size="${spec%% *}"; name="${spec##* }"
    if ! sips -z "$size" "$size" frontend/public/logo.png --out "$iconset/$name.png" >/dev/null 2>&1; then
      png_ok=0
      break
    fi
  done
  if [ "$png_ok" -eq 1 ] && iconutil -c icns "$iconset" -o build/macos/LocalRouter.icns 2>/dev/null; then
    app_icon="build/macos/LocalRouter.icns"
    info "App icon generated from frontend/public/logo.png"
  else
    info "(icon generation failed - shipping the app with the default icon)"
  fi
fi

# ---------------------------------------------------------------- 7. App bundle
stage="release-stage"
app="$stage/LocalRouter.app"
rm -rf "$stage"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"

info "Assembling $app"
cp dist/LocalRouter "$app/Contents/MacOS/LocalRouter"
cp dist/apirouter "$app/Contents/MacOS/apirouter"
cp release/run.sh "$app/Contents/MacOS/run.sh"
chmod +x "$app/Contents/MacOS/LocalRouter" "$app/Contents/MacOS/apirouter" "$app/Contents/MacOS/run.sh"
mkdir -p "$app/Contents/MacOS/frontend"
cp -R frontend/dist "$app/Contents/MacOS/frontend/dist"
if [ -f .env.example ]; then
  cp .env.example "$app/Contents/MacOS/.env.example"
fi
if [ -n "$app_icon" ]; then
  cp "$app_icon" "$app/Contents/Resources/LocalRouter.icns"
fi

cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>                  <string>LocalRouter</string>
    <key>CFBundleDisplayName</key>           <string>LocalRouter</string>
    <key>CFBundleIdentifier</key>            <string>com.localrouter.localrouter</string>
    <key>CFBundleVersion</key>               <string>$VERSION</string>
    <key>CFBundleShortVersionString</key>    <string>$VERSION</string>
    <key>CFBundleExecutable</key>            <string>LocalRouter</string>
    <key>CFBundlePackageType</key>           <string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key> <string>6.0</string>
    <key>CFBundleIconFile</key>              <string>LocalRouter</string>
    <key>LSMinimumSystemVersion</key>        <string>11.0</string>
    <key>NSHighResolutionCapable</key>       <true/>
    <key>NSPrincipalClass</key>              <string>NSApplication</string>
</dict>
</plist>
PLIST

# ---------------------------------------------------------------- 8. Installer
# Component pkg from the app bundle, then a distribution pkg whose <domains>
# restrict destinations to the current user's home: double-clicking the pkg
# then installs ~/Applications/LocalRouter.app without an admin prompt.
mkdir -p dist/installer
info "Building component package (pkgbuild)"
pkgbuild --root "$stage" \
         --identifier com.localrouter.localrouter \
         --version "$VERSION" \
         --install-location "Applications" \
         dist/installer/LocalRouter-component.pkg

info "Building per-user installer (productbuild)"
cat > build/macos/distribution.xml <<XML
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
    <title>LocalRouter $VERSION</title>
    <options customize="never" require-scripts="false"/>
    <domains enable_anywhere="false" enable_currentUserHome="true" enable_localSystem="false"/>
    <pkg-ref id="com.localrouter.localrouter"/>
    <choices-outline>
        <line choice="default"/>
    </choices-outline>
    <choice id="default" title="LocalRouter">
        <pkg-ref id="com.localrouter.localrouter"/>
    </choice>
    <pkg-ref id="com.localrouter.localrouter" version="$VERSION" onConclusion="none">LocalRouter-component.pkg</pkg-ref>
</installer-gui-script>
XML
productbuild --distribution build/macos/distribution.xml \
             --package-path dist/installer \
             "dist/installer/LocalRouter-Setup-$VERSION-macos.pkg"

installer="dist/installer/LocalRouter-Setup-$VERSION-macos.pkg"
if [ ! -f "$installer" ]; then
  fail "Installer not produced: $installer"
fi
rm -f dist/installer/LocalRouter-component.pkg
info "Done: $installer"
