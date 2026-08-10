#!/bin/bash
# Anotasyon paketlerini processed_tracks'ten uret (her sarki icin bir JSON).
# Arsiv tazeleme dalgasi bittikce yeniden kosulabilir — taze on-isaretleme
# daha az elle duzeltme demektir.
set -euo pipefail
cd "${KASHI_EVAL_DIR:-/home/cnr-intel/kashi-eval}"
IDS=$(python3 -c "print(','.join(\"'\"+l.strip()+\"'\" for l in open('ids.txt') if l.strip()))")
kubectl exec -n devops postgresql-0 -c postgresql -i -- \
  env PGPASSWORD='e8IquL8uLL+NjvEJGu4VJFGk1KqUJxAN' \
  psql -U kashi_ro -d kashi -q -t -A -c "
SELECT jsonb_agg(jsonb_build_object(
  'source_id', source_id, 'title', title, 'artist', artist,
  'duration_ms', duration_ms, 'quality_score', quality_score,
  'pipeline_version', pipeline_version,
  'lines', (SELECT jsonb_agg(jsonb_build_object(
       'start_ms', l->'start_ms', 'end_ms', l->'end_ms',
       'text', l->'text', 'words', l->'words'))
     FROM jsonb_array_elements(document->'lines') l)))
FROM processed_tracks WHERE source_id IN ($IDS);" > /tmp/packets_raw.json

python3 - <<'PY'
import glob, json, os, re
raw = json.load(open('/tmp/packets_raw.json'))
os.makedirs('packets', exist_ok=True)
# Eski adlandirma kalintisi kalmasin: paketler her kosuda source_id adiyla
# yazilir, sonunda rename.py okunabilir adlara cevirir. Ikisi bir arada
# durursa ayni sarki iki kez listelenir.
for old in glob.glob('packets/*.json'):
    os.remove(old)
ready = stale = 0
for p in raw:
    wordless = sum(1 for l in p['lines'] if not l.get('words'))
    p['wordless_lines'] = wordless
    stem = re.sub(r'[^\w\-]+', '_', f"{p['artist']}_-_{p['title']}")[:80]
    p['stem'] = stem
    json.dump(p, open(f"packets/{p['source_id']}.json", 'w'), ensure_ascii=False, indent=1)
    if wordless: stale += 1
    else: ready += 1
    print(f"  {p['source_id']}  {p['pipeline_version']:>7}  kelimesiz {wordless:>3}  {p['title'][:40]}")
print(f"\n{len(raw)} paket yazildi — {ready} tam on-isaretli, {stale} eksik (tazeleme sonrasi tekrar kos)")
PY

./rename.py
