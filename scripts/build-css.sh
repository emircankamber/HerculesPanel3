#!/usr/bin/env sh
# Derlenmiş Tailwind CSS'i (tailwind.css) yeniden üretir. index.html ya da app.js'e yeni bir
# Tailwind sınıfı eklediğinde çalıştır ve çıktıyı commit'le. Sürüm sabit (CDN'deki v3 ile aynı seri).
set -e
cd "$(dirname "$0")/.."
npx --yes tailwindcss@3.4.17 -c tailwind/tailwind.config.js -i tailwind/input.css -o tailwind.css --minify
