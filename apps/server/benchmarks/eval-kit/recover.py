#!/usr/bin/env python3
"""Araçtan `verified` işaretlenmeden çıkmış bir CSV'yi kurtar.

Neden var: işaretleme ayrı bir eylem olduğu için, şarkının tamamı dinlenip
düzeltmeler yapıldığı hâlde hiçbir satır "kontrol edildi" işaretlenmeden dışa
aktarılabiliyordu. Zamanlar dosyada duruyor, yalnız işaret eksik — dosya
sağlıklı görünüp ölçümde BOŞ sayılıyor. (Araçta artık dışa aktarım bunu
soruyor; bu script daha önce çıkmış dosyalar için.)

    ./recover.py <indirilen>.csv <packets/NN-...json>

`verified` sütununu 1 yapar, eşleşen `.words.txt`'i paketten üretir ve ikisini
annotations/ altına yazar. Satır sayısı paketle tutmuyorsa yazmaz — bu, yanlış
şarkının dosyasını kurtarmaya çalışmanın tek işareti.
"""

import csv
import json
import os
import sys
from pathlib import Path

# Veri (ses/paket/anotasyon) repo DISINDA durur: sesler ve sozler telifli,
# set yalnizca dahili degerlendirme icin. Scriptler repo'da versiyonlu.
KIT = Path(os.environ.get("KASHI_EVAL_DIR", "/home/cnr-intel/kashi-eval"))


def tokens_of(packet: dict) -> list[str]:
    """Aracın düzleştirmesiyle BİREBİR aynı sıra — yoksa sayılar tutmaz."""
    out = []
    for line in packet["lines"]:
        out.extend((line.get("text") or "").strip().split())
    return out


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    csv_path, packet_path = Path(sys.argv[1]), Path(sys.argv[2])
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    tokens = tokens_of(packet)
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if len(rows) != len(tokens):
        print(f"DUR: CSV {len(rows)} satır, paket {len(tokens)} kelime — eşleşmiyor.",
              file=sys.stderr)
        print("Yanlış şarkının dosyası olabilir; hiçbir şey yazılmadı.", file=sys.stderr)
        return 1

    already = sum(1 for r in rows if r.get("verified") == "1")
    stem = packet["stem"]
    ann = KIT / "annotations"
    ann.mkdir(exist_ok=True)

    with (ann / f"{stem}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["word_start", "word_end", "line_end", "verified"])
        w.writeheader()
        for r in rows:
            w.writerow({
                "word_start": r["word_start"], "word_end": r["word_end"],
                "line_end": r.get("line_end", "nan"), "verified": 1,
            })
    (ann / f"{stem}.words.txt").write_text(" ".join(tokens) + "\n", encoding="utf-8")

    print(f"kurtarıldı: {stem}")
    print(f"  {len(rows)} kelime · önceden işaretli {already} → hepsi 1")
    print(f"  yazıldı: annotations/{stem}.csv + .words.txt")
    print("  NOT: bu, şarkının TAMAMINI kontrol ettiğin varsayımıyla yazıldı.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
