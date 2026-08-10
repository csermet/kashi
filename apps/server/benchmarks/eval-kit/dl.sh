#!/bin/bash
# Eval seti sesi indir — anotasyon icin (dahili kullanim, repo'ya girmez)
cd "${KASHI_EVAL_DIR:-/home/cnr-intel/kashi-eval}"
ok=0; fail=0
while read -r id; do
  [ -f "audio/$id.m4a" ] && { echo "ATLA $id (var)"; ok=$((ok+1)); continue; }
  if uvx yt-dlp -q --no-warnings -f "bestaudio[ext=m4a]/bestaudio" \
       -o "audio/$id.%(ext)s" "https://www.youtube.com/watch?v=$id" 2>&1 | tail -2; then
    echo "OK $id"; ok=$((ok+1))
  else
    echo "HATA $id"; fail=$((fail+1))
  fi
done < ids.txt
echo "BITTI: $ok basarili, $fail hata"
ls -la audio/ | tail -3
