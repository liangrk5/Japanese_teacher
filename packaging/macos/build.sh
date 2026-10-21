#!/bin/bash
# 在 macOS 上打包 日语学习助手.app，输出到 dist/。
set -euo pipefail
cd "$(dirname "$0")/../.."

uv sync --frozen --group build

# 用 icon.png 生成 .icns
ICONSET=packaging/macos/build/icon.iconset
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size packaging/macos/icon.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z $double $double packaging/macos/icon.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o packaging/macos/build/icon.icns

uv run pyinstaller --noconfirm --clean \
  --workpath packaging/macos/build/pyinstaller \
  --distpath dist \
  packaging/macos/nihongo.spec

# 未签名的应用：做一个临时签名，避免 Apple Silicon 拒绝运行
codesign --force --deep --sign - "dist/日语学习助手.app"

(cd dist && ditto -c -k --keepParent "日语学习助手.app" "日语学习助手-macOS.zip")
echo "完成：dist/日语学习助手.app"
