#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${OUT_DIR:-.}"
cd "$OUT_DIR"

# Ремультиплекс всех .webm без перекодирования — дописывает Duration/Cues
# Требуется установленный ffmpeg
find . -type f -name '*.webm' -print0 | while IFS= read -r -d '' f; do
  tmp="${f%.webm}.fixed.webm"
  ffmpeg -y -loglevel error -i "$f" -map 0 -c copy -f webm "$tmp"
  mv -f "$tmp" "$f"
done
