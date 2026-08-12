# 2026-08-12 denetim turu + strateji sentezi (8 paralel inceleme)

Kulak testi öncesi tam sağlık taraması: 5 kod denetçisi + 3 araştırmacı,
bulgular çapraz doğrulandı. Düzeltmeler `afd7f03`'te (server 0.26.0 /
pipeline 2.23.0, henüz sevk edilmedi).

## Doğrulanan ve DÜZELTİLEN kusurlar

1. **Dil yönlendirme regresyonu** — haritadışı tespit edilen dil (zh, ar, pl…)
   "eng" oluyor → İngilizce-vocab model + İngilizce ofsetler alıyordu.
   Artık ham kod geçer, tabloyu ıskalar → MMS + ofset yok.
2. **Hakem saat kayması** (iki denetçi bağımsız buldu) — onset toleransı ham
   saatte kalibreydi; kaydırma 60-110 ms'ini yiyordu. `AlignedWord.shift_ms`
   ile hakem artık akustik saatte yargılıyor; eşikler DEĞİŞMEDİ, kalibrasyon
   geri geldi.
3. **Config sessiz-yutma** — `offsetms`/`romanize_` typo'ları sessizce
   kayboluyordu (TR'ye uroman metni felaketi dahil). extra=forbid + ±500 ms
   sınır + sınıf anahtarları Literal.
4. **Aksanlı harf sınıfsızlığı** — â/î/û/é artık ünlü (F9.5 TR hazırlığı).
5. **Kayıt düzeltmesi** — sınıf tablosu kazancı dürüst LOSO ile **+0.010**
   (önceki "+0.013 cross-validated" in-sample fit'ti). Manşetin dürüst hali
   0.5971 → **0.594**. Sabit −80 temiz: 20 fold'un 20'si onu seçti.

## Doğrulanan ama BEKLETİLEN bulgular (bilinçli)

- **OOM aritmetiği** (karışık dilli gün + uzun parça: ~10.7 GB / 12 Gi):
  model önbelleğine LRU tahliye — ayrı iş, ölçüsüz dokunulmayacak.
- **Isıtılmayan dilin ilk işinde indirme hatası = kalıcı fail + 7 gün blok**
  ("other" sınıfı TRANSIENT değil) — hata sınıflandırma işi, backlog.
- **Overlay self-heal skor kıyası** çürük öncüle dayanıyor (Spearman +0.24)
  ama etkisi tek şarkılık pinlenme; kapı-altı dokümanın lrclib'i gölgelemesi
  tasarım kararı → Caner'a.
- **lrclib publish artık düzeltilmiş saati yayınlıyor** — politika notu.
- Bench tekrarlanabilirlik: plosive −80 (fit −60, fark ~0.002, muhafazakâr),
  "j" sınıf kayması (42 kelime) — bir sonraki benchmark koşusunda düzelt.

## Araştırma sentezi (3 ajan, kaynaklı raporlar ajan çıktılarında)

**Model alanı: permissive tavana dayandık.** Tek "belki daha iyi" aday
omniASR-CTC-1B (Apache, karakter-vokab, 20 ms kare) — şarkıda kanıtsız, 2
saatlik logit-uyum deneyi değer. NFA/parakeet + Kyutai 80 ms kare (yanlış
granülarite), MMS_FA/CrisperWhisper/STARS lisans-ölü. Alanın yayınladığı en
iyi medyan 41 ms ama MAE 200 ms+ — 100-300 bandı herkesin ortak sorunu.

**Algısal eşik (ISMIR 2021, Deezer):** fark etme eşiği asimetrik —
geç +220 ms, erken −330 ms. **Algısal PCO'muz [−330,+220] = 0.893**; fark
edilir kaçak yalnız %9.4 (geç %5.3). Medyan 84 ms eşiğin güvenle altında.
Pratik: kuyruk avı GEÇ + beat-hizalı kelimelere odaklanmalı; erken taraf
büyük ölçüde görünmez.

**Fine-tune bahsi: şarkı-verisiyle NO-GO** (yasal veri yok — DALI/DAMP/SVS
zinciri NC; pseudo-label öğretmenin bias'ını miras alır — Vietnam ekibi 1500
saat FT sonrası bile elle −120 ms kaydırmak zorunda kaldı). Şartlı GO:
**label-prior CTC + LoRA, LibriSpeech CC-BY** (torchaudio recipe,
huangruizhe/audio) — beklenti +2-3 puan PCO@0.2, 35-60 GPU-saat, kapı:
PCO@0.3'te sıfır regresyon. İnference-only label-prior ÇALIŞMAZ (ölçülmüş
sıfır, arXiv 2406.02560 §3.4.3).

**Diğer teknikler:** MAPS alt-kare interpolasyonu (yarım gün, birkaç ms) ·
BEACON tarzı çok-model konsensus (dikkat: kendi P3 ölçümümüz zayıf ikinci
hizalayıcının şüphe sinyali veremediğini gösterdi — aynı kapıdan geçmek
zorunda) · blank-penalty/decode-prior sınıfı ÖLÜ.

## Sonuç

Sistem sağlam: çekirdek 5 probdan 5'ini geçti, benchmark rakamları yeniden
üretilebilir, canlı 8 saattir temiz. Doğruluk cephesinde ucuz iş bitti;
kalan seçenekler (omniASR deneyi, label-prior LoRA, MAPS) hepsi ölçüm kapılı
ve F9 hedef v2'ye (PCO@0.2 + medyan<100/80) karşı değerlendirilecek.
