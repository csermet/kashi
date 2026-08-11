#!/usr/bin/env python3
"""Paket + ses dosyalarını okunabilir isimlere çevir.

YouTube kimliği (R6gSMSYKhKU) insan için anlamsız: hangi şarkı olduğu
görünmüyor, paketle sesi eşleştirmek imkânsız. Yeni isim şunu söyler:

    01-HAZIR-Tarkan-Geccek.json / .m4a
    11-ZOR-manifest-Toz-Pembe.json / .m4a
       ^^      ^^^^^
       sıra    ön-işaret durumu

HAZIR = ön-işaretleri tam, hemen anotasyon yapılabilir.
ZOR   = tazeleme sonrası hâlâ kelimesiz satırı var; hakem kurtaramamış.
        Anotasyonu daha yavaş ama ölçümde en değerlisi.

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

    # Numaralar SABİT. İlk koşuda bir sıra kararlaştırılıp diske yazılır ve bir
    # daha değişmez: arşiv tazelendikçe şarkılar BEKLE'den HAZIR'a geçiyor ve
    # sıralamayı duruma göre yapmak, insan "13'ü yapıyorum" derken 13'ün başka
    # bir şarkı olmasına yol açıyordu.
    order_file = KIT / "order.json"
    if order_file.exists():
        order = json.loads(order_file.read_text(encoding="utf-8"))
    else:
        order = [d["source_id"] for d in
                 sorted(packets, key=lambda d: (d["wordless_lines"] > 0, -len(d["lines"])))]
        order_file.write_text(json.dumps(order, indent=1), encoding="utf-8")
    rank = {sid: i for i, sid in enumerate(order)}
    packets.sort(key=lambda d: rank.get(d["source_id"], 999))

    done = {p.stem for p in (KIT / "annotations").glob("*.csv")}
    index = []
    for i, d in enumerate(packets, 1):
        # BITTI = anotasyonu elde. Listenin ilk işi "sırada ne var" sorusunu
        # cevaplamak; biteni göstermezse aynı şarkı iki kez yapılır.
        # ZOR = tazeleme dalgasindan SONRA hala kelimesiz satiri var: hakem
        # kurtaramadi, yani gercekten zor bir vaka. Bu sarkilar anotasyonda
        # daha yavas ama olcumde en degerlisi — modellerin ayristigi yer.
        state = ("BITTI" if d["stem"] in done
                 else "ZOR" if d["wordless_lines"] else "HAZIR")
        # Sanatçı-şarkı kısmı SABİT; değişen yalnız baştaki numara ve durum.
        # Ses dosyasını bulmak için kullanılan tek dayanak bu, çünkü numara ve
        # durum her koşuda değişebiliyor.
        base = f"{slug(d['artist'])}-{slug(d['title'])}"
        name = f"{i:02d}-{state}-{base}"
        # paket
        dst = KIT / "packets" / f"{name}.json"
        if d["_path"] != dst:
            src_path = d["_path"]
            d.pop("_path")
            dst.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            src_path.unlink()
        else:
            d.pop("_path", None)
        # Ses, paketle AYNI adı taşımalı — eşleştirme göz kararı olsun. Arama
        # SLUG kuyruğuna göre: numara/durum önekine göre aramak, ikinci kez
        # numaralandırdığımızda 18 dosyayı bulunamaz hâle getirmişti (paketler
        # yeni numaraya geçti, sesler eskisinde kaldı, ad artık yalan söyledi).
        matches = [p for p in (KIT / "audio").iterdir()
                   if p.is_file() and (p.stem == d["source_id"] or p.stem.endswith(base))]
        if len(matches) > 1:
            print(f"  DİKKAT {base}: {len(matches)} ses dosyası eşleşti, ilki alındı")
        audio = matches[0] if matches else None
        ext = audio.suffix if audio else "?"
        if audio and audio.stem != name:
            shutil.move(str(audio), str(KIT / "audio" / f"{name}{ext}"))
        if audio is None:
            print(f"  DİKKAT {name}: ses dosyası bulunamadı — ./dl.sh koş")
        index.append((i, state, d, name, ext))

    kalan = sum(1 for _, st, *_ in index if st == "HAZIR")
    lines = ["# Anotasyon sırası", "",
             f"**{sum(1 for _, st, *_ in index if st == 'BITTI')} bitti · {kalan} hazır · "
             f"{sum(1 for _, st, *_ in index if st == 'ZOR')} zor**", "",
             "| # | durum | şarkı | satır | kelimesiz | q |",
             "|---|---|---|---|---|---|"]
    for i, state, d, _name, _ext in index:
        lines.append(f"| {i:02d} | {state} | {d['artist']} — {d['title']} | "
                     f"{len(d['lines'])} | {d['wordless_lines']} | {d['quality_score']:.2f} |")
    lines += [
        "", "BITTI = anotasyonu var · HAZIR = temiz ön-işaret · ZOR = kelimesiz satır içeriyor",
        "", "Paket ve ses AYNI adı taşır: `packets/01-...json` ↔ `audio/01-...m4a`",
    ]
    (KIT / "SIRA.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    ready = sum(1 for _, s, *_ in index if s == "HAZIR")
    print(f"{len(index)} dosya adlandırıldı — {ready} HAZIR, "
          f"{sum(1 for _, s2, *_ in index if s2 == 'ZOR')} ZOR, "
          f"{sum(1 for _, s2, *_ in index if s2 == 'BITTI')} BITTI")
    print(f"liste: {KIT / 'SIRA.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
