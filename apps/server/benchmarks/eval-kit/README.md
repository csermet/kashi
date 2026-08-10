# Kashi TR eval seti — anotasyon kiti

Türkçe için kelime düzeyi zamanlama hakikati **hiçbir yerde yok** (lrclib satır
düzeyi, DALI'de Türkçe yok, Musixmatch ücretli kapı). Ölçemediğimiz hiçbir şeyi
üretime sokmadığımız için bu seti kendimiz kuruyoruz.

Çıktı şeması JamendoLyrics'in aynısı → mevcut ölçüm tezgahı (`benchmarks/`)
sıfır değişiklikle okur, EN-TR karşılaştırması bedava gelir.

**Bu klasör repo'ya GİRMEZ.** Ses telifli, sözler telifli; set yalnız dahili
değerlendirme için. (Aynı sebeple `apps/server/benchmarks/data/` de gitignore'lu.)

## Nasıl kullanılır

1. Windows'tan SMB ile bu klasöre gel: `\\cnr-intel\OS-Nvme\kashi-eval`
2. `tool.html`'i tarayıcıda aç.
3. **paket** ve **ses** olarak AYNI adlı dosyayı seç:
   `packets/03-HAZIR-Tarkan-Geccek.json` ↔ `audio/03-HAZIR-Tarkan-Geccek.m4a`
4. Şarkıyı dinle, yanlış oturan kelimeleri düzelt, satırı <kbd>Enter</kbd> ile onayla.
5. **dışa aktar** → 3 dosya iner: `.csv` (zamanlar), `.words.txt` (kelimeler),
   `.review.json` (denetim istatistiği). Üçünü de `annotations/` altına koy.

Dosya adı ne diyor:

```
03-HAZIR-Tarkan-Geccek.json
^^  ^^^^^
sıra  durum
```

- **HAZIR** — ön-işaretleri tam, hemen yapılabilir.
- **BEKLE** — kelimesiz satırları var; arşiv tazeleme dalgası düzeltiyor.
  Dalga bitmeden yapma, boşuna elle iş çıkar.

Tam liste ve sıra: [SIRA.md](SIRA.md)

Çalışman **otomatik kaydedilir** (tarayıcı localStorage) — sekme kapansa bile
aynı paketi tekrar açtığında kaldığın yerden devam eder.

## Tuşlar

| tuş | iş |
|---|---|
| <kbd>space</kbd> | oynat / durdur |
| <kbd>←</kbd> <kbd>→</kbd> | önceki / sonraki kelime |
| <kbd>↑</kbd> <kbd>↓</kbd> | önceki / sonraki satır |
| <kbd>T</kbd> | kelimeyi şu ana damgala, sonrakine geç |
| <kbd>,</kbd> <kbd>.</kbd> | ±10 ms ince ayar |
| <kbd>;</kbd> <kbd>:</kbd> | ±50 ms ayar |
| <kbd>L</kbd> | kelimeyi döngüde dinle (başından 0,45 sn önce) |
| <kbd>Enter</kbd> | satırı onayla + sonraki satıra geç |
| <kbd>R</kbd> | kelimeyi orijinal haline döndür |

## Verimli akış

1. **Çal ve izle.** <kbd>space</kbd>. Söylenen kelime şeritte **yeşil** yanar.
   Duyduğun anla aynı anda yanıyorsa doğru — dokunma.
2. **Geç/erken yanan gördün** → <kbd>space</kbd> ile durdur.
3. **O kelimeyi seç.** Şeritte üstüne tıkla, ya da <kbd>←</kbd>/<kbd>→</kbd>.
   Seçili kelime **mavi** olur. Her kelimenin üstünde küçük sıra numarası var —
   cümlenin 2. kelimesi, üstünde **2** yazan.
4. **Dinle ve kaydır.** <kbd>L</kbd> o kelimeyi döngüye alır (0,45 sn öncesinden
   başlar, sürekli tekrarlar). Geç yanıyorsa <kbd>,</kbd> ile geri çek, erken
   yanıyorsa <kbd>.</kbd> ile ileri it (±10 ms). Büyük adım: <kbd>;</kbd> /
   <kbd>:</kbd> (±50 ms). Döngü çalarken kaydırma anında etki eder.
5. **Satır bitti** → <kbd>Enter</kbd>.

Dalga formunu **fareyle sürükleyerek** istediğin yere gidebilirsin (iki
şeritte de). Zor bir yer varsa hızı **0,75×** yap.

### "tap düzeltmesi" nedir?

Yalnız <kbd>T</kbd> tuşunu kullanırsan devreye girer. <kbd>T</kbd> = "şu anda
çalan yer bu kelimenin başlangıcıdır" demek; ama insan refleksi ~100 ms geç
kalır, bu sayı onu geri alır.

**Sen muhtemelen hiç kullanmayacaksın.** <kbd>,</kbd>/<kbd>.</kbd> ile kaydırma
daha hassas. Kelime tamamen alakasız bir yerdeyse (ön-işaret çok bozuk) <kbd>T</kbd>
kaba yerleştirme için işe yarar, sonra ince ayarı kaydırmayla yaparsın.

## Kalibrasyon modu (önemli)

Ön-işaretler **bizim kendi pipeline'ımızdan** geliyor. İnsan doğal olarak hazır
cevabı "onaylama" eğilimindedir (anchor bias) ve bu, kendi kendini doğrulayan
bir eval seti üretir — ölçmek istediğimiz şeyi tam da yok eder.

`kalibrasyon: AÇIK` düğmesi rastgele **%10 kelimeye ±100-200 ms sapma** ekler.
Gerçekten dinliyorsan bunları düzeltirsin; dışa aktarımdaki
`calibration.caught_rate` bunu rakama döker.

**En az 3 şarkıda açık tut.** Yakalama oranı düşükse (kabaca %70'in altı) o
şarkıların zamanlamalarına güvenmeyelim, tekrar geçelim.

## Ölçülmüş: neden sadece 30 kelime

İlk şarkıda tam anotasyon denendi — **137 kelime, 20 dakika**. Sonra
düzeltmeler orijinalle karşılaştırıldı:

| değişim | kelime |
|---|---|
| >50 ms | 61 (%45) |
| >150 ms | 25 (%18) |
| **>300 ms** | **6 (%4)** |

Ana metrik **PCO@0.3** = "kelime 300 ms içinde mi". Yani emeğin %96'sı metriğin
göremediği bir hassasiyete gitti. PCO bir oran olduğu için şarkının her yerine
yayılmış **30 kelime**, 250 kelime kadar iyi tahmin ediyor — onda bir maliyetle.

**Ölçülen yeni süre: şarkı başına 2-3 dakika.** 10 şarkı ≈ yarım saat.

**±100 ms'i kovalama.** Kulağa doğru geliyorsa <kbd>Enter</kbd> ile geç.

### Dokunma: "mod" ve "buraya kadar" düğmeleri

Araç zaten örneklem modunda açılır. **Mod düğmesine dokunma**, 30 kelime
yeterli.

Aynı şarkıya daha fazla kelime eklemek az şey katar — bir şarkının kelimeleri
birbirine benzer. **Bir şarkı daha yapmak** her zaman daha değerli: bağımsız
bilgi getirir. 10 şarkı × 30 kelime, 1 şarkı × 300 kelimeden çok daha iyi bir
ölçüm verir.

("✔ buraya kadar hepsini kontrol ettim" düğmesi, bir şarkıyı sistematik olarak
baştan sona geçtiysen o aralığı ölçüme sokar. Normal akışta gerekmez.)

## Nereden başlamalı

**`03-HAZIR-Tarkan-Geccek` ile bir tane yap.** Sadece bir tane.

Amaç önce süreyi ölçmek: literatür tahmini şarkı başına 20-40 dk ama bizim
ön-işaretlerimiz iyi olduğu için daha kısa sürebilir. Gerçek rakamı görmeden
kaç şarkı yapacağımıza karar vermenin anlamı yok.

Sonrasında muhtemelen **6-8 şarkı yeter**: ilk soru "lisans-temiz Türkçe model
çalışıyor mu?" ve bu büyük bir fark, az veriyle görülür. 20 şarkı ancak iki
model başa baş çıkarsa gerekir.

## Yeniden üretim

```bash
./dl.sh              # ses indir (idempotent, var olanı atlar)
./make_packets.sh    # paketleri tazele + yeniden adlandır
./make_jamendo_root.py --check   # anotasyonları doğrula, yazmadan
./make_jamendo_root.py           # ölçüm setini derle
```
