# Türkçe taban çizgisi — ilk ölçüm (2026-08-11)

10 şarkı, **1968 doğrulanmış kelime**. Projenin ilk Türkçe hakikat verisi ve
ilk Türkçe rakamı.

Set: `benchmarks/data/jamendolyrics-tr/` · kit: `benchmarks/eval-kit/` ·
kuruluş ve yöntem: [tr-eval-seti-bulgular-2026-08.md](tr-eval-seti-bulgular-2026-08.md)

## Sonuç

Mevcut zincir (MMS-300m + kim-melband, pipeline 2.19.1):

| dil | PCO@0.3 | en iyi / en kötü | n |
|---|---|---|---|
| Almanca | 0.955 | 1.000 / 0.891 | 20 |
| İspanyolca | 0.926 | 0.986 / 0.773 | 20 |
| **Türkçe** | **0.923** | **0.995 / 0.752** | **10** |
| Fransızca | 0.904 | 0.988 / 0.717 | 19 |
| İngilizce | 0.875 | 0.988 / 0.614 | 20 |

**Türkçe İngilizce'den kötü değil — daha iyi.** Beklenmedik bir sonuç: MMS çok
dilli bir model ve Türkçe onun için "küçük" bir dil; performansın düşmesi
beklenirdi. Düşmemiş.

Bunun pratik anlamı: **Türkçe için ayrı bir model şart değil.** Lisans-temiz
geçişte aranan şey "Türkçe'yi kurtaracak model" değil, "mevcut kaliteyi
koruyan permissive model".

## Şarkı bazında

| şarkı | doğrulanmış | >300 ms hata | PCO@0.3 |
|---|---|---|---|
| Hadise — Şampiyon | 192 | 1 | 0.995 |
| Sibel Can ve Eypio — KIYAMAM | 186 | 1 | 0.995 |
| Tarkan — Öp | 272 | 2 | 0.993 |
| Gülşen — Yatcaz Kalkcaz Ordayım | 244 | 6 | 0.975 |
| manifest ve Pango — Zehir | 147 | 8 | 0.946 |
| Ahmet Kaya — Hani Benim Gençliğim | 144 | 8 | 0.944 |
| INJI — BELLYDANCING | 160 | 10 | 0.938 |
| Berkcan Güven — DISS | 277 | 27 | 0.903 |
| manifest — Toz Pembe | 213 | 44 | 0.793 |
| Volga Tamöz — Sebastian | 133 | 33 | 0.752 |

**Dağılım 0.75-0.99 arası.** Yalnız temiz şarkılarla ölçseydik ~0.99 çıkardı ve
hiçbir model diğerinden ayrılmazdı; asıl ayırt edici güç alttaki üç şarkıda.

## ⚠️ Bu setin ölçemedikleri — sınırları net olsun

**PCO@0.1 ve MAE bu setten ÇIKARILAMAZ.** Yöntem gereği: dokunulmayan kelimenin
"hakikati" ön-işaretin kendisidir, yani hatası tanım gereği sıfır görünür.
Naif hesap PCO@0.1'i 0.88 gösteriyor — bu **uydurma**; gerçek değer çok daha
düşük. İngilizce'de aynı zincirin PCO@0.1'i 0.481.

Caner'in Öp'teki gözlemi bunu doğruluyor: *"kelimelerin çoğu ileride, hepsi
300 ms içinde kalıyor ama 100 ms'e baksaydık belki yarısı hatalı çıkardı."*

Yani bu set **tam olarak PCO@0.3 için** kurulmuş bir alettir. Algısal kalite
(Faz 9) ayrı ve çok daha pahalı bir set ister.

İkinci sınır: insan yalnız **bariz** yanlışı düzeltti. 320 ms sapmış bir kelime
gözden kaçmış olabilir. Bu, rakamı bir miktar **iyimser** yapar — ama iki modeli
aynı sette karşılaştırırken bu yanlılık ikisine de eşit uygulanır, dolayısıyla
**karşılaştırma geçerli kalır.**

## Ölçülen hata modları

| mod | nerede | ne oluyor |
|---|---|---|
| **Kilit kaybı** | BELLYDANCING (88 sn sonrası) | Kayma büyüyerek ilerliyor: −2,5 sn → −7 sn. Pencereli hizalamanın önlemesi gereken desen. |
| **Tekrarlı söz karışması** | Berkcan Güven nakaratı | Aynı cümle üst üste geçince hangi tekrarın hangisi olduğu şaşıyor: +1,9 sn'ye kadar ileri, sonra −5,6 sn geri. |
| **Şarkıya özel kayma** | Öp (−250 ms) vs Ahmet Kaya (+400 ms) | Yön şarkıdan şarkıya değişiyor → **sabit ofset düzeltmesi işe yaramaz.** |
| **Monoton hizalayıcının bulamayacağı kelime** | Şampiyon, Toz Pembe | Söz sırasından 3,8-17,6 sn sonra söylenen nara. CTC monoton olduğu için hiçbir model yerleştiremez; ölçüm dışı bırakıldı. |
| **Söz metni hatalı** | Sebastian ("Dakıyormuş" ← "yakıyormuş") | lrclib metni yanlış. Hizalayıcıdan söylenmemiş bir kelimeyi yerleştirmesi isteniyor. Ölçülmedi ama en az bir örnek var. |

## Sıradaki

1. **mpoyraz cv8** ölçümü (Apache-2.0 ağırlık + Apache taban + CC0 veri;
   WhisperX'in TR hizalama varsayılanı bu ailenin cv7'si). Aynı 10 şarkı, aynı
   eşik. Soru: **0.923'ü koruyor mu?**
2. Korursa TR için lisans-temiz zincir tamamlanır. Korumazsa cahya (NC-veri
   lekeli) ve omniASR-CTC sıradaki adaylar.
3. Ayrıştırıcı tarafı zaten hazır: kim-base (MIT) ölçüldü, tipik şarkıda
   üretimdekiyle birebir.
