#!/usr/bin/env bash
# 惨劇RoopeR Skill パッケージング（macOS / Linux / Git Bash）
# engine/ と rules/ を正（リポジトリ直下）とし、dist にコピーして zip を作る。
#
#   bash skill/build.sh
#
# 生成物: skill/dist/sangeki-rooper-rules/  と  skill/dist/sangeki-rooper-rules.zip
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SKILL_DIR")"
NAME="sangeki-rooper-rules"
DIST="$SKILL_DIR/dist"
PKG="$DIST/$NAME"

rm -rf "$PKG"
mkdir -p "$PKG"

cp "$SKILL_DIR/SKILL.md"      "$PKG/"
cp "$SKILL_DIR/run_engine.py" "$PKG/"
cp -R "$REPO_ROOT/engine" "$PKG/engine"
cp -R "$REPO_ROOT/rules"  "$PKG/rules"

# スキルに不要なものを除去
rm -f "$PKG/engine/demo.py" "$PKG/engine/README.md"
find "$PKG" -name "__pycache__" -type d -prune -exec rm -rf {} +

rm -f "$DIST/$NAME.zip"
if command -v zip >/dev/null 2>&1; then
    ( cd "$DIST" && zip -qr "$NAME.zip" "$NAME" )
else
    # Git Bash on Windows usually has no `zip`. Bash→PowerShell へ日本語パスを渡すと
    # 文字化けするため、zip 化は PowerShell 版 build.ps1 に任せる（案内のみ）。
    echo "note: 'zip' が無いためフォルダのみ生成しました。zip が必要なら build.ps1 を使ってください:" >&2
    echo "      powershell -File skill/build.ps1" >&2
fi

echo "built: $PKG"
echo "zip:   $DIST/$NAME.zip"
