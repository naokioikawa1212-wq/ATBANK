#!/bin/bash
# Build the desktop app:  ./build_desktop.sh
# Output: dist/BikeRestoreShorts/
set -e

echo "=== 1/3  フロントエンドをビルド ==="
cd frontend
npm run build
cd ..

echo "=== 2/3  PyInstaller でパッケージ化 ==="
pip install pywebview pyinstaller --quiet
pyinstaller desktop.spec --noconfirm

echo "=== 3/3  完了 ==="
echo "実行ファイル: dist/BikeRestoreShorts/BikeRestoreShorts"
echo ""
echo "注意: ffmpeg がシステムにインストールされている必要があります。"
