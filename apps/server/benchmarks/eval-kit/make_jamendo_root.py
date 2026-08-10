#!/usr/bin/env python3
"""Anotasyon çıktılarını ölçülebilir bir veri setine dönüştür.

Araçtan inen üçlüyü (`<stem>.csv`, `<stem>.words.txt`, `<stem>.review.json`)
JamendoLyrics dizin şemasına yerleştirir, böylece mevcut tezgah hiçbir
değişiklik olmadan okur:

    python -m benchmarks.run --dataset jamendo --jamendo-root jamendolyrics-tr \\
      --languages tur --separation kim-base --windowed --dump-words --label tr-mms

Hedef şema (datasets.py'nin beklediği birebir yapı):

    benchmarks/data/jamendolyrics-tr/
      JamendoLyrics.csv          Filepath,Artist,Title,Language,LicenseType
      lyrics/<stem>.words.txt    boşlukla ayrılmış token'lar
      annotations/words/<stem>.csv   word_start,word_end,line_end
      mp3/<stem>.mp3             ses (ffmpeg içerikten çözer, uzantı sadece isim)

Ses dosyaları .mp3 ADIYLA yerleştirilir çünkü datasets.py stem'i
`Filepath.removesuffix(".mp3")` ile çıkarıyor; içerik m4a kalır ve ffmpeg
bunu sorunsuz çözer — yeniden kodlama yok, kalite kaybı yok.

Kullanım:
    ./make_jamendo_root.py            # annotations/ altındaki her şeyi derle
    ./make_jamendo_root.py --check    # yalnız doğrula, yazma
"""

import argparse
import csv
import json
import os
import shutil
import sys
from pathlib import Path

# Veri (ses/paket/anotasyon) repo DISINDA durur: sesler ve sozler telifli,
# set yalnizca dahili degerlendirme icin. Scriptler repo'da versiyonlu.
KIT = Path(os.environ.get("KASHI_EVAL_DIR", "/home/cnr-intel/kashi-eval"))
REPO = Path(os.environ.get("KASHI_REPO", "/home/cnr-intel/Projects/kashi"))
DEST = REPO / "apps/server/benchmarks/data/jamendolyrics-tr"


def _packets() -> dict[str, dict]:
    """stem -> paket (başlık/sanatçı/source_id için)."""
    out = {}
    for p in sorted((KIT / "packets").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        d["_file"] = p.stem  # ses dosyası bu adı paylaşır
        out[d["stem"]] = d
    return out


# Ardışık iki kelimenin sırası bu kadar bozulabilir ve hâlâ zararsızdır: satır
# sınırında elle yapılan ince ayar birkaç on ms örtüşme bırakabiliyor ve her
# kelime metrikte bağımsız karşılaştırıldığı için sonucu değiştirmiyor. Bunun
# ÜSTÜ ise gerçek bir karışma işareti (yanlış kelimeye damga vurulmuş).
BACKSTEP_TOLERANCE_S = 0.25


def _validate(stem: str, tokens: list[str], rows: list[dict]) -> tuple[list[str], list[str]]:
    """(ölümcül, uyarı). Ölümcül olanlar şarkıyı elemeli; uyarılar yalnız
    söylenmeli — küçük bir kusur yüzünden 250 kelimelik emeği çöpe atmak,
    korumaya çalıştığı şeyden daha çok zarar verir."""
    fatal: list[str] = []
    warn: list[str] = []
    if len(tokens) != len(rows):
        fatal.append(f"{len(tokens)} token vs {len(rows)} CSV satırı — sayılar tutmuyor")
    prev = -1.0
    for i, r in enumerate(rows):
        try:
            s, e = float(r["word_start"]), float(r["word_end"])
        except (KeyError, ValueError):
            fatal.append(f"satır {i}: word_start/word_end okunamadı")
            break
        if e <= s:
            fatal.append(f"satır {i}: end ({e}) <= start ({s})")
        if r.get("line_end") is None:
            fatal.append(f"satır {i}: line_end kolonu yok")
        if s < prev:
            back = prev - s
            msg = f"satır {i}: zaman {back * 1000:.0f} ms geriye gitti ({s} < {prev})"
            (fatal if back > BACKSTEP_TOLERANCE_S else warn).append(msg)
        prev = s
    if rows and all(r.get("line_end") == "nan" for r in rows):
        fatal.append("hiçbir satırda line_end yok — satır sınırları kaybolmuş")
    if rows and not any(r.get("verified") == "1" for r in rows):
        fatal.append("hiçbir satır verified=1 değil — ölçümde bu şarkı BOŞ sayılır")
    return fatal, warn


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="yalnız doğrula, dosya yazma")
    args = ap.parse_args()

    ann = KIT / "annotations"
    csvs = sorted(ann.glob("*.csv"))
    if not csvs:
        print(f"{ann} boş — araçtan indirdiğin dosyaları buraya koy", file=sys.stderr)
        return 1

    packets = _packets()
    rows_out, problems, ready = [], [], []
    for c in csvs:
        stem = c.stem
        words_txt = ann / f"{stem}.words.txt"
        if not words_txt.exists():
            problems.append(f"{stem}: .words.txt eksik")
            continue
        tokens = words_txt.read_text(encoding="utf-8").split()
        with c.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        fatal, warn = _validate(stem, tokens, rows)
        for w in warn:
            problems.append(f"{stem}: {w} (zararsız, elenmedi)")

        review = ann / f"{stem}.review.json"
        rev = json.loads(review.read_text(encoding="utf-8")) if review.exists() else {}
        cal = rev.get("calibration") or {}
        caught = cal.get("caught_rate")
        if caught is not None and caught < 0.70:
            # Dikkat kalibrasyonu düştü: ön-işaretler kontrol edilmek yerine
            # onaylanmış olabilir. Uyarı, engel değil — kararı insan verir.
            problems.append(
                f"{stem}: kalibrasyon yakalama oranı %{caught * 100:.0f} (<%70) — "
                "bu şarkının zamanlamalarını tekrar geçmeyi düşün"
            )
        if fatal:
            problems.append(f"{stem}: ELENDİ — " + "; ".join(fatal))
            continue

        pkt = packets.get(stem)
        if not pkt:
            problems.append(f"{stem}: eşleşen paket yok (isim değişmiş olabilir)")
            continue
        ready.append((stem, tokens, rows, pkt, rev))
        rows_out.append({
            "Filepath": f"{stem}.mp3", "Artist": pkt["artist"], "Title": pkt["title"],
            "Language": "Turkish", "LicenseType": "internal-eval-only",
        })

    for p in problems:
        print(f"  DİKKAT  {p}")
    if not ready:
        print("derlenecek geçerli şarkı yok", file=sys.stderr)
        return 1
    total_words = sum(len(t) for _, t, _, _, _ in ready)
    print(f"\n{len(ready)} şarkı / {total_words} kelime hazır")
    if args.check:
        print("(--check: hiçbir şey yazılmadı)")
        return 0

    (DEST / "lyrics").mkdir(parents=True, exist_ok=True)
    (DEST / "annotations" / "words").mkdir(parents=True, exist_ok=True)
    (DEST / "mp3").mkdir(parents=True, exist_ok=True)
    for stem, tokens, _rows, pkt, _ in ready:
        (DEST / "lyrics" / f"{stem}.words.txt").write_text(
            " ".join(tokens) + "\n", encoding="utf-8"
        )
        shutil.copy(ann / f"{stem}.csv", DEST / "annotations" / "words" / f"{stem}.csv")
        # Ses, paketle AYNI dosya adını taşır (rename.py). Kimliğe göre arama
        # yedek: adlandırma öncesi indirilmiş dosyalar için.
        src = next((KIT / "audio").glob(f"{pkt['_file']}.*"), None) if pkt.get("_file") else None
        if src is None:
            src = next((KIT / "audio").glob(f"{pkt['source_id']}.*"), None)
        if src is None:
            print(f"  DİKKAT  {stem}: ses dosyası yok ({pkt['source_id']})")
            continue
        shutil.copy(src, DEST / "mp3" / f"{stem}.mp3")
    with (DEST / "JamendoLyrics.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Filepath", "Artist", "Title", "Language", "LicenseType"])
        w.writeheader()
        w.writerows(rows_out)

    print(f"yazıldı: {DEST}")
    print("\nölçmek için:")
    print("  cd /home/cnr-intel/Projects/kashi/apps/server")
    print("  python -m benchmarks.run --dataset jamendo --jamendo-root jamendolyrics-tr \\")
    print("    --languages tur --separation kim-base --windowed --dump-words --label tr-baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
