# Faz 9 devir notu — askerlik arası (2026-08-31 → ~Ekim)

Bir aylık kesintiden önce yazıldı. Amaç tek: **dönünce hiçbir ölçümü
yeniden türetmek zorunda kalmamak.** Buradaki her sayı gerçekten ölçüldü;
tahmin olan yerler açıkça öyle işaretli.

## Canlı durum (dokunma, çalışıyor)

| bileşen | sürüm |
|---|---|
| server / pipeline | 0.29.2 / 2.26.0 |
| overlay | 0.28.4 |
| eklenti | 0.1.14 |

Repolar temiz ve push'lu, açık MR yok, kuyrukta iş yok, Velero tam yedeği
alındı (`askerlik-oncesi-full-20260831`).

## Elimizdeki en değerli şey: ölçebiliyoruz

Faz 9 boyunca tekrar eden sorun şuydu — Caner "burası hızlı" diyor, ölçüm
"iyi" diyor. Sebebi ölçüm setiydi: JamendoLyrics **CC indie**, ve şikâyetin
geldiği yoğun pop kancalarından **tek örnek bile içermiyor** (868 satırın
hiçbirinde parantez yok).

Artık pop için yer gerçeğimiz var: **5 şarkı, 414 doğrulanmış kelime**.

- Yer: `Projects/kashi/eval-pop/` (git-ignore'lu — telifli ses + söz)
- Yedek: `/mnt/storage/arsiv/kashi-eval-2026-08/` (farklı fiziksel disk)
- Derleme: `eval-pop/make_jamendo_root.py` → `benchmarks/data/jamendolyrics-pop`

### ⚠️ KAPSAM TUZAĞI — bu nota bakmadan ölçme

Anotasyon aracı dışa aktarırken **her satıra `verified=1`** yazıyor, ama
Caner yalnızca kendisine söylenen satırlara baktı. Olduğu gibi ölçersen
2411 kelimenin çoğunda **kendi çıktını kendinle** karşılaştırırsın ve
sahte-mükemmel sonuç alırsın. (İlk hesabım PCO 0.931 çıktı ve GEÇERSİZDİ.)

Geçerli bölge:
- **Uptown / Shape of You / Rock It** → yalnızca düzeltme içeren SATIRLAR
- **Stressed Out / Safari** → baştan son düzeltmeye kadar olan bölge

Sınırı bulma yolu: paketteki bizim zamanlarımızla CSV'nin **farklı olduğu**
son kelime.

## Ölçülen: hata üç sınıfa ayrılıyor

**1. Kanca sınıfı (Caner'ın şikâyeti) — dar ama gerçek**

```
(hot damn)                    850 ms ERKEN   ← Uptown Funk
"Oh, I'm in love..." → I'm    350 ms erken   ← Shape of You (2 kez)
```

Hepsi ya **parantezli cevap** ya **satır başı**, ve neredeyse hepsi **erken**
— JamendoLyrics'teki *geç* eğilimin tersi (oradaki −80 ms düzeltmesi o sete
fit edilmişti).

**2. Kaba arıza — iki ayrı şekil**

```
Stressed Out : YALNIZ satır 0, −6.15 sn, sonrası kusursuz
Safari       : ilerledikçe kilidi kaybediyor (0 → +400 → +3400 → +20.7 sn)
```

**3. İki körlük birbirini tamamlıyor** (kullanılmıyor, kullanılabilir)

```
Stressed Out satır 0 : score=1.0    KÖR      | uncertain=TRUE  YAKALADI
Safari       satır 0 : score=0.052  YAKALADI | uncertain=yok   KÖR
```

İkisi **birlikte** 30 kaba hatalı satırın **26'sını** yakalıyor. Şu an
hiçbiri kullanıcıyı korumuyor: hizalayıcının "sıfır fikrim var" dediği satır
ekrana tam güvenle basılıyor.

## ÖLÇÜMLE ÖLEN 5 FİKİR — tekrar denemeyin

1. **Satır-başı "şişik aralık" kelepçesi** — in-sample cazip (%17→%9), LOSO'da
   KÖTÜLEŞİYOR (%29.5→%23.6). Erken hatayı geç hataya çeviriyor.
2. **Konuma göre ek ofset** — kalan en iyi +10 ms, +0.005 PCO = gürültü.
3. **Beat ızgarası** — 87.063 kelime, 3 çözünürlük (beat/8'lik/16'lık):
   medyan 0.235-0.255, rastgele 0.25. Kelimeler ızgaraya HİÇ oturmuyor,
   kontrol grubu dahil.
4. **Tekrar tutarlılığı** — Uptown'da aynı satır 8 kez: aralar 120/160/100/
   1660/80/100/260/280 ms, medyan 140. GERÇEK ~970 ms. **7/8 örnek aynı
   şekilde yanlış** — medyan uygulasan yanlış cevabı ONAYLARSIN.
5. **Onset** — 786 onset (~344 ms'de bir). Bizim yer +49 ms, gerçek yer
   −128 ms: ikisinin de dibinde onset var. Yoğunluk bilgi taşımıyor.

Ayrıca: "boş satır + uzun kuyruk" imzası ve belge düzeyi şüphe oranı da
ayırt etmedi (Rock It %56 / Safari %79).

## AYAKTA KALAN TEK FİKİR: vokal enerji çukuru

Onset başarısız çünkü yoğun; **enerji çukuru seyrek ve anlamlı.**
Ayrıştırılmış vokalde, `(hot damn)` figüründe:

```
33500-33900 ms   −2.7 dB   ← BİZİM koyduğumuz yer (çağrının kuyruğu)
34200-34300 ms  −44.5 dB   ← SESSİZLİK
34400-34600 ms   −7.1 dB   ← GERÇEK yer (cevabın girişi)
```

Kural: satır içindeki ilk sessizlik çukurunu bul, cevabı çukurdan **sonra**
başlat. Doğrulanan örnekte:

```
bizim     33620 ms  → hata −850 ms
kural     34389 ms  → hata  −81 ms      (10 kat iyileşme)
```

8 tekrarda önerilen kaydırmalar: +769, +788, —, +216, +657, +677, —, +504.

### n=8 DOĞRULAMASI — 2026-09-04

Caner askerliğe gitmeden kalan 7 cevabı da anotladı (`edited: 3 → 37`).
Kural artık 1 değil **8 örnekte** ölçüldü. Ama ilk okuduğum rakam yanlıştı ve
bunu bilerek yazıyorum:

**Korumasız kural 9 satırı hareket ettiriyor ve 3'ünde blok sonraki satırın
üzerine taşıyor** — `(aaaaaow!)` +511 ms, iki `(say what?)` +391 ve +748 ms.
Yani "bir sonraki satırın başlangıcını aşma" koruması opsiyonel bir incelik
değil, kuralın taşıyıcı parçası. Overlay tarafında satır N+1 ekrana gelirken
satır N'in kelimeleri hâlâ akıyor olurdu.

| | korumasız | **korumalı (geçerli olan)** |
|---|---|---|
| mutlak hata medyanı | 750 → 64 ms | 750 → **148 ms** |
| 200 ms içinde | 5/8 | **4/8** |
| kötüleşen | 0 | **0** |
| sonraki satıra taşan | **3 satır** | **0** |

Örnek örnek (bizim → gerçek, hata önce → sonra):

```
satır 16   33620 → 34470   −850 → −81     (ileri, çukur 255 ms)
satır 18   37780 → 38580   −800 → −12     (ileri)
satır 20   41940 → 42790   −850 → −850    çukur bulunamadı, dokunulmadı
satır 22   47780 → 46980   +800 → +215    GERİ (bizim yer sessizlikteydi, −34.8 dB)
satır 47  108860 → 109560  −700 → −43     (ileri)
satır 49  113020 → 113720  −700 → −23     (ileri)
satır 51  117340 → 117890  −550 → −550    çukur bulunamadı, dokunulmadı
satır 53  121540 → 122090  −550 → −550    SIĞMADI (oda −50 ms), dokunulmadı
```

**Tasarım kararı veriye bağlandı:** "sığmıyorsa hiç dokunma" (SKIP) ile
"sığdığı kadar kaydır" (CLAMP) bu veride **aynı sonucu** veriyor — satır 53'ün
odası zaten negatif. O halde daha basit ve asla yarı-tahmin yapmayan SKIP.

### Dürüstlük kaydı

8 örnek ama **1 şarkı, 1 figür**. Genelleme kanıtlanmadı: `(say what?)` ve
`(aaaaaow!)` satırlarında kural bir şey öneriyor ama doğruluğunu ölçecek yer
gerçeği yok. İkinci bir şarkıda parantezli satırları anotlamak bunu kapatır.

Fixture'lar (git-ignore'lu, `/mnt/storage/arsiv/kashi-eval-2026-08/` altında
yedekli): `eval-pop/uptown_vocal_energy.json` (vokal RMS dB, hop 11.6 ms,
ref=max — kaynağı worker pod'undaki geçici `vocals.wav`, o pod silinince
yeniden üretmek 14 dk ayrıştırma demek) ve `eval-pop/uptown_response_truth.json`.

## 🚨 DÖNÜNCE İLK İŞ: yt-dlp 403 — hiçbir şarkı işlenemiyor

**2026-09-04'te bulundu, düzeltilmedi.** Tek şarkılık doğrulama denemesi
(Uptown Funk) üç denemede de düştü:

```
ERROR: unable to download video data: HTTP Error 403: Forbidden
```

Sebep bizim kodumuz değil, **sabitlenmiş yt-dlp sürümü**:

```
worker imajı : yt-dlp 2026.07.04   (pyproject.toml:14, `yt-dlp==2026.7.4`)
güncel       : yt-dlp 2026.08.19   ← aynı iki videoyu yerelde sorunsuz indiriyor
```

YouTube iki ay içinde bir şey değiştirdi ve pinlediğimiz sürüm ses indiremiyor.

**Kapsamı geniş:** bu yalnız arşiv taramasını değil, **yeni dinlenen her
şarkıyı** da vuruyor. Sunucu bugün hiçbir belge üretemez.

**Arşiv zarar GÖRMEDİ:** indirme başarısız olunca belge hiç yazılmıyor; 277
belge olduğu gibi duruyor.

### Neden kanarya uyarmadı (ve bu bir tasarım açığı)

`.github/workflows/ytdlp-canary.yml` bilinçli olarak **yalnız metadata**
çekiyor — GitHub runner IP'leri bot kontrolüne takıldığı için tam indirme
"kurt geldi" alarmı üretiyordu. Ama 403 tam da **veri indirmede** oluyor,
metadata'da değil. Yani kanarya bu sınıfı yapısal olarak göremez.

Dosyanın kendi yorumu çözümü zaten yazmış: *"If even metadata-only proves
flaky here, move this to a homelab CronJob + ntfy instead."* Ev sunucusundan
koşan, kısa bir gerçek indirme yapan bir kanarya bu boşluğu kapatır.

### Status 2026-10-03 — server 0.30.1 (pipeline unchanged, 2.27.0)

The pin bump alone did **not** fix it. With `yt-dlp==2026.8.19` (still the
latest stable on 2026-10-03; only nightlies since) our own download path
failed differently: `Requested format is not available`. Cause: the
`player_client` cascade ported from VDL was dead on every client. Measured
per client on Uptown Funk, cookie-less, no PO Token provider:

```
tv          UNPLAYABLE ("The page needs to be reloaded")
mweb        skipped: needs a GVS PO Token
web         skipped: SABR forced, formats carry no URL
android_vr  skipped: needs a GVS PO Token
visionos    5 audio formats with URLs   (new in 2026.8.19)
default     5 audio formats with URLs   (= visionos + web in 2026.8.19)
```

The "08.19 downloads fine locally" check above was most likely run with
yt-dlp's default clients, not ours. Fix: `player_client = ["default"]`
(`vdl_kit/ytdlp_opts.py` explains why; yt-dlp queries every listed client,
so dead fallbacks cost requests, and `default` moves with the monthly bump).

Verified before release: real downloads through `download_audio` —
Uptown Funk opus/251 126 kbps, probed 269.681 s (= the eval packet's
269 681 ms, same audio); a nightcore upload (r≈1.33) opus/251, probed
duration equal to its archived document, title intact for detection.
Server gate green (642 tests with a throwaway Postgres; the policy test
pins `["default"]` and fails with the old cascade). Also fixed a pyright
error that had been on `main` since the by-ear rung (`pick_by_transcript`
is now generic).

A metadata-only canary would not have seen this failure either — the
format list is non-empty (storyboards), only the audio formats are gone.

### 2.27.0 verified in the field — 2026-10-03 (step 2 done)

Uptown Funk re-processed alone on server 0.30.1 (download opus/251, the
same bytes as the local check). The base alignment reproduced August's
exactly: all 8 response words sat on the same pre-shift positions as the
2.26.0 document. The rule then moved 6 lines (`qa.response_shifted` = 6,
all six are ground-truth lines; no `(say what?)` / `(aaaaaow!)` line moved).

```
line   2.26.0   lab 2.27.0   field 2.27.0
 16     −850       −81          −81
 18     −800       −12          −12
 20     −850      −850          −54    ← lab found no dip; the field did (+796 ms)
 22     +800      +215         +215
 47     −700       −43          −31
 49     −700       −23          −23
 51     −550      −550         −550    (no dip, untouched — as in the lab)
 53     −550      −550         −550    (no room, refused — as in the lab)
```

6/8 identical to the lab within ±5 ms; the two that differ are both better.
Median |error| 750 → **67.5 ms** (lab: 148), within 200 ms 0/8 → **5/8**
(lab: 4/8), worsened 0, spilled into the next line 0.

Why 20 and 47 differ is not proven. The difference is in the loudness
contour, not the aligner (base positions are identical). The lab contour
came from a separately produced vocals file (23 229 frames vs the
pipeline's 23 228 — a slightly different decode); the field contour is
computed on the pipeline's own separated `align.wav`, which is deleted
with the job. The acceptance fixture stays the lab contour.

Still true: 1 song, 1 figure — generalisation needs a second annotated song.
(Stressed Out already has one: three `(oh)` words inside its valid region,
current errors 0 / −200 / −350 ms — queued for re-processing on 2.27.0.)

### Corrections and findings — 2026-10-04

- **The outage started on 2026-08-13, not 09-04.** The last successful job
  before the fix finished 2026-08-13 22:37 UTC; every job after it failed
  with 403. 09-04 is the day it was noticed. 53 songs first played between
  08-13 and 09-01 never got a document at all.
- **`main`'s CI was red for seven weeks** (from the by-ear rung, 2026-08-13,
  to `bc232ad`): pyright failed, so the `pytest` step after it never ran in
  CI. Nobody looked. Rule added to `CLAUDE.md`: confirm the CI run on `main`
  is green before tagging.
- **Step 3 started:** a quarter of the archive (62 of the 249 documents older
  than 2.26.0, most recently added first) is being re-processed.
- **Step 4 prepared:** a real-download canary (scheduled job on the
  self-hosted deployment, alert on two consecutive failures) is waiting for
  review.

### "Word-duration collapse" — measured, not decided

The roadmap's cheap candidate is a render-side minimum display time per
word. Measured on the pop set's valid region (4 songs, 197 word-to-word
intervals, start to start):

- No collapse in aggregate: our intervals < 120 ms 9.6 %, human 8.1 %;
  p5 97 vs 104 ms.
- Real collapses (human ≥ 120 ms, ours under half of it) are 15/197 = 7.6 %
  and mostly *placement* errors — Uptown's stapled `(hot damn)` (80 vs
  770 ms). On the same 34 Uptown intervals 2.27.0 cut them 9 → 4.
- 13 of our 19 intervals under 120 ms are under 120 ms for the human too:
  a short interval is usually genuinely fast singing.

So a display-time floor would not close the real collapses (260 vs 740 ms)
and would distort genuinely fast words. Weak candidate on this data; small
sample, Uptown-heavy — re-measure once the pop set has 2.27.0 documents.

### Dönünce sıra

1. `apps/server/pyproject.toml` içindeki pini güncelle (07.04 → o günün
   sürümü), `uv lock`, imaj, rollout. **Sürüm atlaması davranış
   değiştirebilir** — nightcore/`--extractor-args` yollarını da test et.
2. Uptown Funk'ı yeniden işlet ve **2.27.0 kuralını sahada doğrula**:
   `eval-pop/uptown_response_truth.json` ile karşılaştır, beklenen
   hata −81/−12/−850/+215/−43/−23/−550/−550 (±5 ms).
3. Ancak ondan sonra arşiv taraması (249 belge eski hatta, ~41 saat).
4. Kanaryayı ev sunucusuna taşı.

### 2.27.0'ın durumu

Kod **canlıda** (`server 0.30.0 / pipeline 2.27.0`, worker pod'unda teyit
edildi: kural bağlı, eşik −25 dB, tavan 1500 ms). Testler geçiyor (534
sunucu testi, 6/6 mutasyon), ama **sahada tek bir belge üretilmedi** — 403
yüzünden. Yani laboratuvarda doğru, sahada doğrulanmamış.

## Kapanmış olanlar (dokunma)

Faz 9 boyunca sevk edilen ve sahada doğrulanan işler: satır lead-in,
yanlış-EDIT doğrulama kapısı, bayat süre/playhead penceresi (ext 0.1.14),
videoId→süre defteri, çoklu-sanatçı rung'ı, merdiven-sürümlü negatif
önbellek, kulakla söz bulma rung'ı (ASR), tekrar-jesti + efekt rengi
düzeltmeleri.
