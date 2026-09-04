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

## Kapanmış olanlar (dokunma)

Faz 9 boyunca sevk edilen ve sahada doğrulanan işler: satır lead-in,
yanlış-EDIT doğrulama kapısı, bayat süre/playhead penceresi (ext 0.1.14),
videoId→süre defteri, çoklu-sanatçı rung'ı, merdiven-sürümlü negatif
önbellek, kulakla söz bulma rung'ı (ASR), tekrar-jesti + efekt rengi
düzeltmeleri.
