# Lisans-temiz hizalama zinciri — araştırma sentezi (2026-08-09)

Faz 8.1'in yeniden tanımlanmış hedefi: **EN + TR çalışan, ücretli üründe sevk
edilebilir (tamamen permissive) hizalama zinciri** için ölçülmüş bir mimari
önerisi. Bu doküman üç paralel araştırma turunun sentezi; her iddianın kaynağı
ajan raporlarında URL'li olarak mevcut, kritik olanlar buraya taşındı.

Lisans çıtası: sevk edilen her ağırlık/kod permissive (Apache-2.0 / MIT / BSD /
CC0). NC her yerde yasak (kullanım kısıtı sunucu tarafında da işler); AGPL
yasak; eval-amaçlı dahili kullanımda NC serbest (hiçbir şey sevk edilmez).

## 1. Bugünkü zincirin lisans durumu

| Halka | Bugün | Lisans | Sevk edilebilir mi |
|---|---|---|---|
| Ayrıştırıcı | kim_ft_unwa (pcunwa/Kim-Mel-Band-Roformer-FT) | **HİÇ YOK** — repo'da LICENSE/README/kart yok → varsayılan "all rights reserved" | ❌ |
| Hizalayıcı | MahmoudAshraf/mms-300m-1130 (taban facebook/mms-300m) | CC-BY-NC-4.0 | ❌ |
| Romanizasyon | uroman | MIT | ✅ |

İki kırık halka var; ikisi için de doğrulanmış çıkış yolu bulundu.

## 2. Ayrıştırıcı: çıkış yolu net

**Kritik düzeltme:** htdemucs_ft "temiz MIT fallback" DEĞİLMİŞ. Maintainer
beyanı (adefossez, demucs issue #327, 2022-05-23, birebir alıntı): *"The model
weights are not covered by the MIT license, and are provided only for
scientific purposes."* → demucs ağırlıkları ticari rotada **kesin elenir**.
(Önceki faz notlarındaki "temiz fallback htdemucs_ft (MIT)" bilgisi yanlış.)

Doğrulanmış tablo (ajan C):

- **KimberleyJSN/melbandroformer — MIT** (HF etiketi bugün itibarıyla; GPL→MIT
  relicense Nisan 2026 ikincil kaynaklı). Üretimdeki kim_ft_unwa'nın **doğrudan
  ebeveyni**; vocals SDR 10.98. Zincirdeki tek tartışmasız temiz roformer.
- **BS PolarFormer (ZFTurbo, MSST release v1.0.20)** — zoo'nun en yüksek vocals
  SDR'ı (11.00); ağırlıklar MIT repo'nun kendi release'inde ama açık ağırlık
  beyanı yok → sevk öncesi tek satırlık GitHub issue ile teyit şart.
- unwa fine-tune'una lisans SORULABİLİR: HF discussion sekmesi açık
  (pcunwa/Kim-Mel-Band-Roformer-FT/discussions), yazılı+kalıcı kanal. Olumlu
  cevap tek adımda sorunu çözer (taban MIT olduğu için başka engel yok).
- Elenenler: htdemucs_ft (beyanla kapalı) · Open-Unmix/Bandit/Banquet (NC) ·
  Spleeter (lisans OK, kalite sınıf dışı) · bsr-revive-v2 (çifte lisanssız).

**ÖLÇÜLDÜ (2026-08-09, 79 şarkı, GPU, `pc-kim-base-j400`):** kim-base (MIT)
vs kim-ft-unwa (lisanssız): toplam PCO@0.3 **0.9090 vs 0.9150** (−0.6 puan);
dil bazında EN −0.05 / FR +0.20 (berabere), DE −1.33 / ES −1.17. **Medyan MAE
farkı +2 ms — tipik şarkı birebir aynı**; toplam MAE farkı (261 vs 191 ms)
14/79 şarkının kuyruğundan geliyor (en kötü 2'si ES, tek bölge kilit kaybı
deseni: MAE 1400+ ms'e çıkarken PCO 0.95'te kalıyor — hakem katmanının tam
hedefindeki desen). **Karar seçenekleri:** (a) kim-base'i kabul et (−0.6 puan,
MIT, bugün hazır), (b) unwa'ya HF discussion'dan lisans sor (olumluysa sıfır
bedel), (c) PolarFormer'ı ZFTurbo teyidi sonrası ölç (MSST formatı — tezgah
bağlama işi var). İkinci aday PolarFormer, ZFTurbo teyidi gelirse.

## 3. Hizalayıcı EN: çözülmüştü (Faz 8 ölçümü)

jg XLS-R 1B (jonatasgrosman, Apache-2.0) MMS'le 79 şarkıda BERABERE
(EN 0.8789 vs 0.8746; DE −1.2 / FR −0.7 / ES ±0). Dikiş hazır
(`settings.align_model`), geçiş bayrak işi. CPU maliyeti ölçülmedi (GPU'da
3,5-5,5× yavaş) — GPU entegrasyonu (Caner'ın 5070 Ti'lı kişisel makinesi, aynı
ağda; ilerde docker + otomatik/CF erişimli kurulum) planlandığı için hız
engel olmaktan çıktı.

## 4. Hizalayıcı TR: ✅ ÇÖZÜLDÜ (2026-08-11 ölçümü)

10 şarkı, 1966 doğrulanmış kelime, aynı ayrıştırıcı (kim-base), aynı pencere
ve jitter. Eşleştirilmiş karşılaştırma:

| model | PCO@0.3 | PCO@0.2 | PCO@0.1 | MAE | lisans zinciri |
|---|---|---|---|---|---|
| MMS-300m (bugünkü) | **0.938** | 0.869 | 0.733 | 158 ms | ❌ CC-BY-NC |
| **mpoyraz cv7** | **0.930** | 0.859 | 0.730 | 174 ms | ✅ **CC-BY-4.0** |
| mpoyraz cv8 | 0.888 | 0.822 | 0.662 | 311 ms | ✅ Apache + CC0 |

**cv7 ile MMS arasında istatistiksel fark YOK:** şarkı başına ortalama fark
−0.008, %95 güven aralığı **[−0.017, +0.001]** — sıfırı içeriyor. 10 şarkının
4'ünde birebir aynı, 1'inde cv7 daha iyi, 5'inde 0.4-3.7 puan geride.

cv8 (Apache + CC0, en temiz zincir) **5 puan geride** — kabul edilemez.
Aradaki fark eğitim verisi: cv7 CommonVoice 7 + MediaSpeech (CC-BY), cv8 yalnız
CommonVoice 8. Lisans bir kademe "daha az saf" ama CC-BY-4.0 **atıfla ticari
kullanıma açık** — bir künye satırı yeterli.

**Karar: mpoyraz cv7.** `--no-align-romanize` ile (Türkçe harfler modelin
sözlüğünde yerel).

Ölçüm sırasında çıkan tuzak: `romanize=True` uroman üzerinden noktalamayı da
temizliyormuş. Kapatınca ilk virgül/parantez/♪ şarkıyı düşürdü ve ilk koşuda
10 şarkının 5'i kayboldu. Artık metin **modelin sözlüğüne** indirgeniyor
(sözlük tokenizer'dan okunuyor; ç ğ ı ö ş ü korunur, â→a düşer, noktalama
atılır, token sayısı korunur).

## 4b. Hizalayıcı TR: aday taraması (arka plan)

Ajan B'nin doğrulanmış sıralaması:

1. **mpoyraz/wav2vec2-xls-r-300m-cv8-turkish** — her kutu işaretli: Apache
   ağırlık (API+README doğrulandı) + Apache taban (XLS-R-300m) + **yalnız
   CommonVoice 8 verisi (CC0)**. Native TR vocab (ç ğ ı ö ş ü) →
   `romanize=False` per-model bayrağı (küçük iş). `AutoModelForCTC` ile dikişe
   doğrudan takılır. Güçlü ekosistem kanıtı: **WhisperX'in TR hizalama
   varsayılanı bu ailenin cv7'si** (alignment.py'de sabit).
2. **cahya/wav2vec2-base-turkish** — alan dışı dayanıklılıkta en iyi (RSE dev
   28.0), 95M ile en ucuz; AMA eğitim verisinde MagicHub (CC BY-NC-ND) var →
   açık ara birinci çıkmazsa ticari rotada elenir.
3. **omniASR-CTC-1B (Meta, Kasım 2025, Apache-2.0)** — `tur_Latn` destekli,
   tek modelle EN+TR ihtimali (per-dil model yönetimini kaldırır). Resmi format
   fairseq2; tek transformers dönüşümü üçüncü-el ve doğrulanmamış
   (ilhanemirhan/omniASR_CTC_1B) → ciddiye alınırsa kendi dönüşümümüz. Erken
   duman testi önerilir.

- Whisper birincil hizalayıcı olarak RED (WhisperX makalesi Whisper'ın kendi
  kelime zamanlarının zayıflığını ölçüyor; saha projeleri kelime zamanı için
  Whisper'ı terk etmiş). TR ikinci-görüş rolü için faster-whisper kararı Faz 8
  notlarında duruyor, bu ayrı konu.
- Stratejik yedek: w2v-bert-2.0 (MIT taban doğrulandı) + CommonVoice TR (CC0)
  kendi fine-tune — reçete hazır (HF blog tutorial'ı), 3 aday da PCO hedefini
  tutturamazsa.

## 5. TR değerlendirme seti: hazır veri YOK, kendi setimiz şart

Ajan A'nın taraması (hepsi doğrulanmış):

- **lrclib**: yalnız satır düzeyi (canlı API doğrulaması). Enhanced-LRC servis
  etmiyor; LRCGET v2'nin "Lyricsfile" formatı izlemeye değer.
- **DALI**: TR pratikte yok. **Musixmatch richsync**: ücretli/kapalı kapı.
- **UltraStar toplulukları**: ~20-25 TR şarkı (Tarkan/Sezen Aksu dönemi;
  modern arşivle kesişim yok). Hece düzeyi, beat-kuantize, belirli video
  sürümüne GAP'li. Bağımsız çapraz-kontrol olarak değerli, ana set olamaz.
- **Akademik**: yalnız Türk makam müziği (georgid 17 şarkı kelime düzeyi,
  lisanssız; Zenodo 10 kayıt phrase düzeyi CC-BY-NC-SA). Pop'u temsil etmez;
  "zor durum" yan-testi olarak rafta.

**Rota: "JamendoLyrics-TR" klonu.** Arşivden 15-20 TR şarkı (tür/tempo
çeşitliliği), mevcut pipeline çıktısı ön-işaretleme, insan düzeltmesi, aynı CSV
şeması → mevcut eval koduna sıfır değişiklik, EN-TR karşılaştırması bedava.
Tahmin ~25 insan-saati (ilk 2 şarkıdan sonra yeniden kalibre edilecek).
Anchor-bias uyarısı: düzeltici kendi pipeline'ımızın çıktısını "kabul etme"
eğilimi gösterir — şarkıların bir kısmında ikinci geçiş / kasıtlı jitter.
Araç adayları: Enhanced LRC Maker, subtitletools Timed Lyrics Editor,
gerekirse wavesurfer.js ile ~1 günlük özel araç.

## 6. Hedef mimari (ölçüm sonrası hali)

**ÜÇ HALKA DA ÖLÇÜLDÜ.**

| Halka | Hedef | Lisans | Ölçüm |
|---|---|---|---|
| Ayrıştırıcı | KimberleyJSN melbandroformer (`kim-base`) | **MIT** | 0.909 vs 0.915 — tipik şarkıda birebir (medyan MAE farkı +2 ms) |
| Hizalayıcı EN | jg XLS-R 1B | **Apache-2.0** | 0.8789 vs 0.8746 — MMS'i geçti |
| Hizalayıcı TR | mpoyraz cv7 + `--no-align-romanize` | **CC-BY-4.0** | 0.930 vs 0.938 — fark istatistiksel olarak YOK |
| Romanizasyon | uroman (EN) / native vocab (TR) | MIT | — |
| Eski zincir | MMS + kim_ft | NC / lisanssız | **config'te yedek kalır, sevk edilmez** |

Sevk edilebilir bir zincir var ve kalite bedeli ölçülebilir düzeyde değil.
Hiçbiri "umuyoruz" değil — üçü de bağımsız ölçüldü.

Geçiş kod işi değil, üç ayar:

```
align_model                = jonatasgrosman/wav2vec2-xls-r-1b-english   # EN
                             mpoyraz/wav2vec2-xls-r-300m-cv7-turkish    # TR
align_romanize             = false          # TR yolunda
separation_model_filename  = vocals_mel_band_roformer.ckpt
```

**Açık kalan tek iş: dil başına model seçimi.** Bugün tek bir `align_model`
var; EN ve TR farklı checkpoint istiyor, pipeline'ın dile göre seçmesi gerek.
Küçük iş — `resolve_model_name` zaten tek karar noktası.

Donanım: GPU serbest (5070 Ti kişisel makinede, aynı ağ); ilerde sunucudan
erişilebilir docker GPU servisi planlanıyor — 1B sınıfı modellerin CPU
maliyeti bu yüzden birincil kısıt değil.

## 7. Sıralı iş listesi

1. TR eval seti: şarkı seçimi + protokol + araç → anotasyon (~25 saat, insan)
2. Ayrıştırıcı ölçümü: KimberleyJSN vs kim_ft (EN tezgahı, GPU'da ~30 dk)
3. TR hizalayıcı ölçümü: mpoyraz cv8 (+cv7 kontrol) + omniASR duman testi
   (TR seti hazır olunca)
4. unwa'ya HF discussion'dan lisans sorusu + ZFTurbo'ya PolarFormer teyidi
   (yazılı, kalıcı; Caner onayıyla)
5. Geçiş: `align_model` + `separation_model_filename` bayrakları, eski zincir
   config yedeği olarak kalır

Açık/doğrulanamayan noktalar: voidful vocab (gated 401) · ylacombe w2v-bert TR
lisans niyeti · ilhanemirhan dönüşümünün bütünlüğü · omniASR TR-özel WER ·
Kim relicense tarihçesinin birincil kaynağı.
