#!/usr/bin/env python3
"""Paket + ses dosyalarını okunabilir isimlere çevir.

YouTube kimliği (R6gSMSYKhKU) insan için anlamsız: hangi şarkı olduğu
görünmüyor, paketle sesi eşleştirmek imkânsız. Yeni isim şunu söyler:

    01-HAZIR-Tarkan-Geccek.json / .m4a
    11-BEKLE-Hadise-Sampiyon.json / .m4a
       ^^      ^^^^^
       sıra    ön-işaret durumu

HAZIR = ön-işaretleri tam, hemen anotasyon yapılabilir.
BEKLE = kelimesiz satırları var; arşiv tazeleme dalgası bunları düzeltiyor,
        dalga bitmeden yapmak boşuna elle iş çıkarır.

Idempotent: tekrar koşulabilir, isimler source_id'den türetilir.
"""

import json
import os
import shutil
import unicodedata
from pathlib import Path

# Veri (ses/paket/anotasyon) repo DISINDA durur: sesler ve sozler telifli,
# set yalnizca dahili degerlendirme icin. Scriptler repo'da versiyonlu.
KIT = Path(os.environ.get("KASHI_EVAL_DIR", "/home/cnr-intel/kashi-eval"))
TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def slug(s: str) -> str:
    s = s.translate(TR_MAP)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    out = "".join(c if c.isalnum() else "-" for c in s)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")[:38]


def main() -> int:
    packets = []
    for p in (KIT / "packets").glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        d["_path"] = p
        packets.append(d)

    # HAZIR olanlar önce (ön-işareti tam), sonra BEKLE — sıra numarası
    # doğrudan "hangisinden başlayayım" sorusunu cevaplasın.
    packets.sort(key=lambda d: (d["wordless_lines"] > 0, -len(d["lines"])))

    index = []
    for i, d in enumerate(packets, 1):
        state = "BEKLE" if d["wordless_lines"] else "HAZIR"
        name = f"{i:02d}-{state}-{slug(d['artist'])}-{slug(d['title'])}"
        # paket
        dst = KIT / "packets" / f"{name}.json"
        if d["_path"] != dst:
            src_path = d["_path"]
            d.pop("_path")
            dst.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            src_path.unlink()
        else:
            d.pop("_path", None)
        # ses — paketle AYNI adı alsın, eşleştirme göz kararı olsun
        audio = next((KIT / "audio").glob(f"{d['source_id']}.*"), None)
        if audio is None:
            audio = next((KIT / "audio").glob(f"{name}.*"), None)
        ext = audio.suffix if audio else "?"
        if audio and audio.stem != name:
            shutil.move(str(audio), str(KIT / "audio" / f"{name}{ext}"))
        index.append((i, state, d, name, ext))

    lines = ["# Anotasyon sırası", "",
             "| # | durum | şarkı | satır | kelimesiz | q |",
             "|---|---|---|---|---|---|"]
    for i, state, d, _name, _ext in index:
        lines.append(f"| {i:02d} | {state} | {d['artist']} — {d['title']} | "
                     f"{len(d['lines'])} | {d['wordless_lines']} | {d['quality_score']:.2f} |")
    lines += ["", "HAZIR = hemen yapılabilir · BEKLE = tazeleme dalgası bitince",
              "", "Paket ve ses AYNI adı taşır: `packets/01-...json` ↔ `audio/01-...m4a`"]
    (KIT / "SIRA.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    ready = sum(1 for _, s, *_ in index if s == "HAZIR")
    print(f"{len(index)} dosya adlandırıldı — {ready} HAZIR, {len(index) - ready} BEKLE")
    print(f"liste: {KIT / 'SIRA.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
