# Türkçe eval seti — kurulum ve ilk bulgular (2026-08-11)

Türkçe için kelime düzeyi zamanlama hakikati hiçbir yerde yok (lrclib satır
düzeyi — canlı doğrulandı, DALI'de TR yok, Musixmatch ücretli kapı, akademik
setler yalnız makam). Bu yüzden set elle kuruldu: Caner dinliyor, bariz yanlış
kelimeleri düzeltiyor, gerisi "baktım, doğruydu" sayılıyor.

Şema JamendoLyrics'in aynısı → mevcut tezgah `--jamendo-root jamendolyrics-tr`
ile hiç değişmeden okuyor. Kit: `benchmarks/eval-kit/` (veri repo dışında).

## Durum

8 şarkı · **1556 doğrulanmış kelime** (1691 kelimenin içinden).

Seçim: yoğunluk yelpazesi × kalitenin iki ucu × bilinen hata modları, sanatçı
başına en fazla 2. Kalan 2 aday: Berkcan Güven (rap), Sebastian.

## Anotasyon ekonomisi — ölçüldü, plan değişti

İlk şarkı tam anotasyonla denendi: **137 kelime, 20 dakika**. Sonra düzeltmeler
orijinalle karşılaştırıldı:

| değişim | kelime |
|---|---|
| >50 ms | 61 (%45) |
| >150 ms | 25 (%18) |
| **>300 ms** | **6 (%4)** |

Ana metrik PCO@0.3 olduğu için **emeğin %96'sı metriğin göremediği bir
hassasiyete** gitti. Kural değişti: "±100 ms'i kovalama, yalnız bariz yanlışı
düzelt". Yeni hız: **şarkı başına 4-5 dakika**, tam kapsamla.

## Hata oranı şarkıdan şarkıya 8 kat değişiyor

| şarkı | dokunulan | >300 ms gerçek hata |
|---|---|---|
| Yatcaz Kalkcaz (temiz) | 11/244 (%5) | 6 (~%2,5) |
| Toz Pembe (arşivin en kötüsü) | 60/213 (%28) | 44 (~%21) |

Yalnız temiz şarkılarla ölçseydik iki model de ~%97 alır, hiçbir şey
öğrenemezdik. **Bozuk şarkılar setin ayırt edici gücü.**

## ⚠️ Bulgu 1: 300 ms toleransı gerçek hatayı gizliyor

Caner'in Öp gözlemi: *"kelimelerin çoğu ileride, benim gördüğüm tam olması
gereken yerde değil ama hepsi 300 ms içinde kalıyor; 100 ms'e baksaydık belki
yarısı hatalı çıkardı."*

Bu, İngilizce ölçümünün zaten söylediğiyle birebir uyuşuyor:

| tolerans | tüm diller | İngilizce |
|---|---|---|
| ±100 ms (algısal) | 0.567 | **0.481** |
| ±300 ms (manşet) | 0.915 | 0.875 |

Manşet %91,5; algısal olarak doğru sayılabilecek kelime oranı %57. **Faz 9'un
(algı/efekt) asıl konusu bu.**

Setin kendi sınırı da buradan çıkıyor: elle düzeltmeler ±100 ms hassasiyetinde
yapıldı, dolayısıyla bu set **300 ms'de sağlam, 200 ms'de iyi, 100 ms'de
zayıf**. PCO@0.1 iddiaları bu setle yapılmamalı; gerekirse ayrı ve çok daha
pahalı bir set gerekir.

## ⚠️ Bulgu 2: kayma şarkıya özel, küresel değil

Öp'te 12 düzeltmenin 12'si de negatifti (hizalayıcı geç işaretlemiş) — rastgele
olma olasılığı 1/4096. Ama sete yayılınca tek yön kalmıyor:

| şarkı | medyan (>150 ms hatalar) | yön |
|---|---|---|
| Ahmet Kaya — Hani Benim Gençliğim | +400 ms | erken |
| Gülşen — Yatcaz Kalkcaz | +350 ms | erken |
| manifest — Toz Pembe | +550 ms | erken |
| Tarkan — Öp | −250 ms | **geç** |
| INJI — BELLYDANCING | −2625 ms | **geç (kilit kaybı)** |

**Sabit bir ofset düzeltmesi işe yaramaz** — her şarkı kendi yönünde kayıyor.
Şarkı-içi kayma (drift) ise gerçek: bkz. bulgu 3.

## ⚠️ Bulgu 3: kilit kaybı — BELLYDANCING

88. saniyeye kadar doğru, sonra kayma **büyüyerek** ilerliyor: ilk düzeltilen
kelimede −2,5 sn, onuncuda −7,0 sn. Pencereli hizalamanın önlemesi beklenen
desen tam da bu; demek ki pencere planı bu şarkıda tutmamış.

Ölçüm tuzağı olarak da öğreticiydi: kuyruk elle düzeltilmediği için
`verified=1` gitseydi **hakikat = pipeline'ın kendi yanlış çıktısı** olacaktı ve
her model o 29 kelimeyi tam tutturmuş sayılacaktı. Hizalayıcı, başarısız olduğu
yerde ödüllendirilecekti. Araç artık kısmi kapsamı açıkça soruyor.

## ⚠️ Bulgu 4: monoton hizalayıcının bulamayacağı kelimeler

Şampiyon'da bir "Şampiyon" narası sözlerdeki sırasından 17,6 sn sonra
söyleniyor; Toz Pembe'de bir "(Ooh)" 3,8 sn. CTC hizalama monotondur — böyle
bir kelimeyi hiçbir model yerleştiremez. Puanlamak, mimarinin yasakladığını
ölçmek olurdu; derleyici bu kelimeleri (asgari kümeyle, en uzun azalmayan alt
dizi üzerinden) ölçüm dışı bırakıyor.

## Sıradaki

1. Kalan 2 şarkı → 10 şarkı / ~1900 kelime
2. **İlk Türkçe ölçümü**: mevcut zincir (MMS + kim-base) taban çizgisi
3. **mpoyraz cv8** (Apache + CC0 veri, WhisperX'in TR varsayılanı) karşılaştırması
4. Sonuç lisans kararını verir: TR için permissive zincir mümkün mü
