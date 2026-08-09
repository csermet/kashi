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

## 4. Hizalayıcı TR: aday tarandı, ölçüm TR seti bekliyor

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

| Halka | Hedef | Lisans | Durum |
|---|---|---|---|
| Ayrıştırıcı | KimberleyJSN melbandroformer (veya PolarFormer) | MIT | ölçüm bekliyor (EN tezgahı hazır) |
| Hizalayıcı EN | jg XLS-R 1B | Apache-2.0 | ✅ ölçüldü, berabere |
| Hizalayıcı TR | mpoyraz cv8 (aday 1) | Apache-2.0 | TR seti bekliyor |
| (alternatif) | omniASR-CTC tek model EN+TR | Apache-2.0 | duman testi bekliyor |
| Romanizasyon | uroman / native vocab'da `romanize=False` | MIT | küçük iş |
| Eski zincir | MMS + kim_ft | NC / lisanssız | **yedek olarak kalır** (config'te, sevk edilmez) |

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
