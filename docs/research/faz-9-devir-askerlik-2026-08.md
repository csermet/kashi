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

### Neden SEVK EDİLMEDİ

**n=1.** Doğrulanmış tek örnek var, 8'in 2'sinde kural hiç çalışmıyor.
"İşe yarıyor" denemez, "ilk kez ölmedi" denebilir. Bir aylık gözetimsiz
döneme doğrulanmamış bir zamanlama değişikliği sokmak yanlış risk.

### Dönünce ilk adım (~3 dk Caner + ~1 saat ölçüm)

Uptown Funk'ı anotasyon aracında aç, **kalan 7 `(hot damn)` cevabının** ilk
kelimesini yerine oturt (satır 18, 20, 22, 47, 49, 51, 53). n=1 → n=8 olur,
kural gerçekten doğrulanabilir. Çıkarsa yaz; çıkmazsa bu sınıfı kapat ve
"anotasyon şart" de.

Probe betikleri: `scratchpad/onset_probe.py`, `/tmp/gap_probe.py` deseni —
ayrıştırma ~14 dk (worker pod, CPU), sonra librosa RMS + −25 dB eşiği.

## Kapanmış olanlar (dokunma)

Faz 9 boyunca sevk edilen ve sahada doğrulanan işler: satır lead-in,
yanlış-EDIT doğrulama kapısı, bayat süre/playhead penceresi (ext 0.1.14),
videoId→süre defteri, çoklu-sanatçı rung'ı, merdiven-sürümlü negatif
önbellek, kulakla söz bulma rung'ı (ASR), tekrar-jesti + efekt rengi
düzeltmeleri.
