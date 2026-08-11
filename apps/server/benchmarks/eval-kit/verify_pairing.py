#!/usr/bin/env python3
"""Her paketin YANINDAKİ ses dosyası gerçekten o şarkı mı?

Yeniden adlandırmadan sonra paket-ses bağı yalnız dosya adına kalıyor; ad
yanlışsa sessizce başka şarkının sesi üzerinde anotasyon yapılır ve ölçüm
çöp olur. Bağımsız kanıt: ffprobe ile ölçülen GERÇEK süre, paketin süresiyle
tutuyor mu.

    ./verify_pairing.py
"""

import glob
import json
import os
import subprocess
import sys
from pathlib import Path

KIT = Path(os.environ.get("KASHI_EVAL_DIR", "/home/cnr-intel/kashi-eval"))
TOLERANCE_S = 2.0


def main() -> int:
    ok = problems = 0
    for pf in sorted((KIT / "packets").glob("*.json")):
        d = json.loads(pf.read_text(encoding="utf-8"))
        stem = pf.stem
        audio = glob.glob(str(KIT / "audio" / f"{stem}.*"))
        if not audio:
            print(f"  SES YOK      {stem}")
            problems += 1
            continue
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", audio[0]],
            capture_output=True, text=True, check=False)
        if not out.stdout.strip():
            print(f"  OKUNAMADI    {stem}")
            problems += 1
            continue
        real = float(out.stdout.strip())
        pkt = (d.get("duration_ms") or 0) / 1000
        if abs(real - pkt) <= TOLERANCE_S:
            ok += 1
        else:
            problems += 1
            # Bayat süre metadata'sı da buraya düşer; hizalama yine de doğru
            # olabilir (son kelime gerçek sesin içindeyse). Ayırt et.
            last = max((ln["end_ms"] for ln in d["lines"]), default=0) / 1000
            kind = "BAYAT METADATA" if last <= real else "YANLIŞ EŞLEŞME"
            print(f"  {kind:14} {stem[:44]:46} ses {real:7.1f} vs paket {pkt:7.1f} "
                  f"(son söz {last:.1f})")
    print(f"\n{ok}/{ok + problems} paket-ses eşleşmesi doğrulandı")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
