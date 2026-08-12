# Faz 8.1 değerlendirmesi + Faz 9 planı (2026-08-11)

Caner'ın sorusu: *"300 ms kabul edilebilir bir doğruluk değil, 100-150 ms'e
inmek lazım."* — **Haklı, ve bu fazın kapanışı o işin başlangıcı olmalı.**

## Bu faz ne yaptı, ne YAPMADI

**Yaptı: lisans.** Zincirdeki üç halkanın üçü de permissive karşılığıyla
ölçüldü (kim-base MIT · jg-1b Apache · mpoyraz cv7 CC-BY). Kalite bedeli
ölçülebilir düzeyde değil. Ücretli ürünün önündeki hukuki engel kalktı.

**Yaptı: ölçebilirlik.** Türkçe için projenin ilk hakikat verisi kuruldu
(10 şarkı / 1966 kelime). Öncesinde Türkçe hakkında hiçbir ölçülmüş iddiada
bulunamıyorduk.

**Yaptı: dürüstlük altyapısı.** Dört ölçüm tuzağı kapatıldı — en tehlikelisi
kontrol edilmemiş bölgenin modele tam puan kazandırmasıydı.

**YAPMADI: doğruluğu iyileştirmedi.** Bu fazın amacı zaten "aynı kaliteyi
lisanssız taşı" idi ve tam olarak o oldu. Hizalama bugün dünkü kadar iyi —
ne daha iyi ne daha kötü. **Faz 9'un konusu bu.**

## Artılar / eksiler

| ✅ | ❌ |
|---|---|
| Lisans zinciri temiz, üçü de ölçüldü | Doğruluk hiç artmadı (amaç değildi) |
| TR ölçülebilir hale geldi | TR seti **yalnız 300 ms'de** geçerli |
| 5 hata modu isimlendirildi + kanıtlandı | Beşi de **düzeltilmedi** |
| Qwen ölçümle elendi (yol kapandı, boşuna uğraşılmayacak) | İkinci-görüş yolu tümden kapandı |
| overlay `uncertain` sahada | Etkisi 0.5 kalite kapısıyla sınırlı |
| Arşiv tazelendi (409 satır kelime senkronu kazandı) | 0.5 kapısı hâlâ ölçülmüş-kötü, dokunulmadı |

## 🔑 YENİ BULGU: sistematik gecikme — ve bedava düzeltmesi

İngilizce JamendoLyrics (**tam hassasiyetli** insan anotasyonu, 5693
doğrulanmış kelime / 20 şarkı, MMS + kim-melband):

- **Kelimelerin %76.4'ü GEÇ işaretleniyor.** Rastgele olsa %50 beklenirdi.
  Üstelik 20 şarkının **20'si birden** geç — yanlılık birkaç bozuk şarkının
  taşıdığı bir şey değil, modelin kendisinin.
- İşaretli medyan hata: **+81 ms** (şarkı medyanlarının medyanı +72 ms)
- Sadece sabit bir kaydırma uygulayınca:

| düzeltme | PCO@0.1 | PCO@0.2 |
|---|---|---|
| yok (bugün) | 0.4802 | 0.7407 |
| **−80 ms** | **0.5595** | **0.8146** |
| −100 ms | 0.5401 | 0.8175 |

**Dürüst rakam +0.079** (leave-one-song-out: ofset 19 şarkıda seçilip 20.'de
ölçülüyor; 20 fold'un 20'si de −80 ms seçiyor — yanlılık genelleniyor).

> Bu tablo `benchmarks/lateness.py` ile yeniden üretilir; ilk yazımındaki
> rakamlar (0.492 → 0.588) elle hesaplanmıştı ve **şarkı başına değil kelime
> havuzunda** toplanıyordu. Aracın 0-ofset satırı artık sonuç dosyasının kendi
> `aggregate.pco`'suyla dört haneye kadar aynı — yani rapor edilebilir.

### ✅ P1 KAPANDI: yanlılık ölçüldü, düzeltildi, doğrulandı (2026-08-12)

Yanlılık **modele değil ŞARKIYA ait** — bu iddiayı iki bağımsız zincir
doğruladı: MMS + kim-melband %76.4 / +81 ms, sevk edilen jg-1b + kim-base
%76.3 / +78 ms. İki farklı model, iki farklı ayrıştırıcı, 40 fold'un 40'ı da
aynı **−80 ms**'i seçiyor. (Faz 8'in bunun için önerdiği MEKANİZMA ise bir
sonraki bölümde çürütüldü — yanlılığın varlığı ölçüm, açıklaması değildi.)

`align_offset_ms` (pipeline 2.21.0) uygulandıktan sonra ölçülen:

| | düzeltmesiz | **−80 ms ile** |
|---|---|---|
| geç kelime oranı | %76.3 | **%49.1** |
| medyan işaretli hata | +78 ms | **−2 ms** |
| geç şarkı | 20/20 | 6/20 |
| **kalan** en iyi ofset | −80 ms | **0 ms** (20/20 fold) |
| PCO@0.1 | 0.4892 | **0.5847** |
| PCO@0.2 | 0.7649 | **0.8358** |
| PCO@0.3 | 0.8931 | **0.9101** |
| medyan kelime MAE | 150 ms | **128 ms** |
| aşırı-uzatma | 0.153 | **0.076** |

Kalan ofsetin sıfıra inmesi asıl kanıt: ortada tüketilmemiş başka bir sabit
gecikme yok. **Türkçe'ye ofset KONULMADI** — TR eval seti yalnız 300 ms'de
geçerli, 80 ms ölçeğinde bir yanlılığı göremez (Faz 9 madde 5'in işi).

Kanıt dosyaları: `2026-08-12-wd-jg1b-shipped.json` (düzeltmesiz) ve
`2026-08-12-wd-jg1b-offset80.json` (düzeltmeli).

### ✅ P2 KAPANDI: ilk-ses sınıfı — ve HİPOTEZİN ÇÜRÜMESİ (2026-08-12)

**Faz 8'in mekanizma tahmini YANLIŞ çıktı.** Tahmin: ünsüzle başlayan kelime,
ünsüzün süresi kadar geç işaretlenir; ünlüyle başlayan kelime yansız olur.
Düzeltmesiz zincirde ölçülen medyan işaretli hata:

| ilk ses | medyan | n |
|---|---|---|
| **ünlü** | **+112 ms** | 1376 |
| sürtünmeli | +87 ms | 828 |
| patlamalı | +62 ms | 1813 |
| sürekli (nazal/likit) | +59 ms | 1676 |

Ünlüyle başlayanlar en GEÇ sınıf, tahminin tam tersi. Düzeltme ölçüldüğü için
duruyor; açıklama durmuyor — `pipeline/phonetics.py` bunu açıkça yazıyor.
Sıralamaya uyan yeni hipotez (kanıt DEĞİL): **akustik işaretin keskinliği** —
patlamalı ünsüzün patlaması nettir, sürtünmenin kenarı yumuşaktır, önceki
kelimenin sesinden kesintisiz akan ünlünün sinyalde sınırı yoktur.

Sevk edilen tablo (mutlak ofsetler): ünlü −110, sürtünmeli −100, patlamalı
−80, sürekli −60. Ölçülen sonuç:

| | sabit −80 | + sınıf tablosu |
|---|---|---|
| PCO@0.1 | 0.5847 | **0.5971** |
| PCO@0.2 | 0.8358 | 0.8385 |
| sınıf başına kalan yanlılık | +32 / +7 / −18 / −21 | **+2 / −13 / −18 / −1** |

Çapraz doğrulanmış katkı **+0.0130** (%95 GA [+0.0062, +0.0198], 20 şarkının
14'ünde kazanıyor); ölçülen gerçek katkı +0.0124. **Küçük olduğunu açıkça
söylemek gerek:** 0.70 hedefine kalan yol 100-300 ms bandında (kelimelerin
%33'ü) ve kuyrukta (868 satırın 22'si toplam hatanın %38'ini taşıyor).
Kanıt: `2026-08-12-wd-jg1b-byinitial.json`.

**Tek satırlık bir kaydırma, algısal toleransta ~10 puan.** Yanlılığın kendisi
sağlam: iki checkpoint, iki ayrıştırıcı, 40 fold'un 40'ı. Ama *neden* olduğu
konusundaki Faz 8 açıklaması (ünsüz süresi) P2'de çürüdü — sistematik ama
mekanizması HENÜZ bilinmeyen bir yanlılık.

## Faz 9 planı: algısal doğruluk (300 ms → 100-150 ms)

**Kritik avantaj: yeni veri toplamaya gerek YOK.** İngilizce JamendoLyrics
zaten tam hassasiyetli; PCO@0.1'i bugün ölçebiliyoruz. Geliştirme orada
yapılır, Türkçe'de küçük bir yüksek-hassasiyet altkümesiyle doğrulanır.

### Sıra (ucuzdan pahalıya, her adım ölçülür)

1. ✅ **KAPANDI (2026-08-12, canlıda)** — sabit ofset −80 ms, pipeline 2.21.0.
   Ölçülen: PCO@0.1 0.4892 → 0.5847 (+0.096).
2. ✅ **KAPANDI (2026-08-12)** — ilk-ses sınıfına göre düzeltme,
   pipeline 2.22.0. Ölçülen: 0.5847 → **0.5971** (+0.012). Beklentinin tersi
   yönde çıktı: ünlüyle başlayan kelime daha çok geri gitmeli, ünsüzle
   başlayan daha az.
3. ⏸️ **ÖNCELİĞİ DÜŞTÜ (2026-08-12 ölçümü)** — 22 bozuk satırın 15'i ad-lib
   veya birebir tekrar, yani metinden çözülemeyecek belirsizlik; gerçek hata
   7/868 = %0.8. "Toplam hatanın %38'i" rakamı belirsizlikten şişmişti.
4. ✅ **KAPANDI (2026-08-12)** — kalite kapısı 0.5 → **0.2** (overlay 0.25.0).
   Yeniden ölçüldü: Spearman +0.244, kapı 1 kötüden 0'ını yakalayıp gerçek
   PCO@0.3'ü 0.979 olan dokümanı öldürüyordu.
5. **Yüksek hassasiyetli TR altkümesi** (~4-6 saat insan) — yalnız 1-4 adımın
   Türkçe'de de tuttuğunu doğrulamak için 3-5 şarkı, ±30 ms hedefiyle.

### ❌ ÖLÇÜMLE ELENEN: onset'e hizalama + şarkı başına ofset (2026-08-12)

Faz 8'in "ünlü-farkındalıklı onset snap" maddesi **ölçümle kapandı.** Sinyal
var gibi görünüyor: gerçek kelime başlangıçlarının %74'ü bir vokal onset'ine
≤100 ms mesafede ve "±150 ms içindeki EN İYİ onset'i seç" oracle'ı 0.5971 →
0.6577 veriyor. Ama uygulanabilir hiçbir politika bunu yakalamıyor:

| politika | PCO@0.1 |
|---|---|
| dokunma (taban) | **0.5971** |
| en yakın onset ±60 / ±100 / ±150 ms | 0.5862 / 0.5798 / 0.5772 |
| yalnız tek aday varsa ±150 ms | 0.5916 |
| yalnız güçlü onset (>%90) ±150 ms | 0.5822 |
| penceredeki en güçlü onset ±150 ms | 0.5419 |

**Hepsi tabanın ALTINDA.** Sebebi tek teşhisle anlaşıldı: onset yakınlığı,
modelin doğru bulduğu kelimeyle yanlış bulduğu kelimeyi **ayırt etmiyor** —
gerçek başlangıcın en yakın onset'e uzaklığı isabetlerde 40 ms, 100-300 ms
bandında 48 ms, >300 ms hatalarda 55 ms. Üç grup da aynı. Oracle'ın 0.6577'si
cevaba bakarak elde ediliyor, yani gerçek bir tavan değil.

Onset **kanıt katmanı olarak hakemde kalır** (Spearman +0.399 ile satırın
yerini sorgulamakta iyi); **düzeltici olarak ölü**.

Aynı turda ölçülen ikinci ölen fikir: **şarkı başına ofset**. Oracle tavanı
+0.0162 ve 20 şarkının yalnız 3'ünde |ofset| ≥ 40 ms. Uygulanabilir bir
kestirici (lrclib çapalarından) bunun altını yakalar.

### ✅ P4 KAPANDI: 0.5 kalite kapısı düşürüldü (2026-08-12)

Kapı yeni zincirle yeniden ölçüldü ve **savunulamaz** çıktı:

- `quality_score` ↔ gerçek PCO@0.3: **Spearman +0.244**
- Kapının altında kalan 2 şarkıdan biri **gerçek PCO@0.3 = 0.979** (neredeyse
  kusursuz, sahada kelime senkronu atılıyordu), diğeri 0.788
- Gerçekten kötü olan tek şarkı (PCO@0.3 < 0.75) **q=0.81 ile kapıdan geçti**

Yani kapı 1 kötüden 0'ını yakalıyor, 1 mükemmeli öldürüyordu. Hata kalibrasyonu
okumamaktan geliyor: skorun kendi kalibrasyonu **yanlış-söz bölgesini ~0.2**,
doğru hizalanmış tam miksi ~0.7 diyor — 0.5 tam da DOĞRU aralığın ortasına
düşüyordu.

**Karar: `QUALITY_GATE` 0.5 → 0.2** (overlay 0.25.0). Kalibrasyonun gerçekten
desteklediği kısım korunuyor (yanlış sözle hizalanan doküman hâlâ elenir),
gerisi hakem katmanına bırakılıyor: satır düzeyinde bağımsız kanıtla (onset,
sessizlik kapsamı) karar veren ve kurtardığını `uncertain` işaretleyen katman
zaten canlı, overlay de onu soluk render ediyor. **Şüphe satırın, dokümanın
değil.**

Sema açıklaması ve sunucudaki üç bayat "0.5" referansı da güncellendi. Test
artık DEĞERİ sabitliyor (eski testler sabite göreliydi, o yüzden kapı aylarca
ölçülmeden yerinde kaldı).

### Hedef (v2 — 2026-08-12'de Caner yeniden çerçeveledi)

İlk bar PCO@0.1 ≥ 0.70'ti. Caner hedefi ürün diliyle yeniden koydu: **"300
değil 200 altı — 200 üstü kaçak olabildiğince az; tipik kelime 100 altı,
olabiliyorsa 80 altı."** Metrik çifti buna göre:

- **Birincil: PCO@0.2** — şu an **0.8385** (0.4892 tabanından iki düzeltmeyle
  0.7649 → 0.8358 → 0.8385 geldi). Sabit eşik konmadı; yön yukarı, 200 ms
  üstü kaçak şu an kelimelerin %16'sı.
- **Medyan kelime hatası <100 ms** — şu an **84 ms** (esneme hedefi <80).
- **PCO@0.1 ≥ 0.65 (yumuşak hedef)** — şu an 0.5971. 0.70'ten geri çekildi:
  Caner "olmaya devam etsin ama aşırı emek vermeye gerek yok" dedi; kalan
  ~0.05'lik yol için orantısız maliyet ödenmez.
- Ortalama (192 ms) hedef metriği DEĞİL: ad-lib/tekrar belirsizliği şişiriyor,
  ortalamayı kovalamak çözümü olmayan satırları kovalamak olur.

Dil kapsamı da netleşti: **bu faz İngilizce'de biter** (sistem burada
kurulur); Türkçe F9.5'te elle kalibrasyonla taşınır (mini set ±30 ms),
diğer diller sonra (Japonca hafif önde). Ucuz kazançlar bitti; PCO@0.2'yi
kayda değer kıpırdatacak kalan yol model düzeyinde (fine-tune bahsi, ayrı
karar).

## Karar: fazı kapatalım mı?

**Öneri: evet, ama "tamam budur" diye değil.** Faz 8.1'in hedefi lisanstı ve
o hedef ölçülerek tutuldu. Doğruluk ayrı bir problem sınıfı, ayrı bir bütçe
ve ayrı bir eval hassasiyeti istiyor — aynı fazın içine sıkıştırmak ikisini de
yarım bırakır.

Kapanış şartı: **dil başına model seçimi** (~1 saat, tek kalan iş). Sonra
Faz 9 doğrudan yukarıdaki 1. maddeyle açılır — elimizde ölçülmüş, mekanizması
bilinen ve ucuz bir kazanç var.
