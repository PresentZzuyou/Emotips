#!/bin/bash
cd "$(dirname "$0")"
export PATH="/Users/zzuyou/Library/Python/3.9/bin:/opt/homebrew/bin:$PATH"

echo "========================================"
echo "  이모팁스 영상 편집기 시작 중..."
echo "========================================"

python3 scripts/server.py
