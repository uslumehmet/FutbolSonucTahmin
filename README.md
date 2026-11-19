# FutbolSonuçTahmin

Futbol maçlarının nasıl biteceğini istatistik ve makine öğrenmesiyle tahmin eden, kendi bilgisayarınızda çalışan kişisel bir web sitesi. Süper Lig, Avrupa'nın 5 büyük ligi ve milli maçlar için her maçın ihtimallerini hesaplar.

> **Önemli:** Buradaki ihtimaller ve tahminler istatistik ve makine öğrenmesi modellerinin hesaplamasıdır, gerçek sonuç değildir. Her tahmin yanlış çıkabilir. Site gerçek parayla hiçbir şey oynatmaz. Ayrıntı için [Sorumluluk reddi](#sorumluluk-reddi) bölümüne bakın.

## Neler yapar

- **Maçlar:** Her maç için 1-X-2 ihtimal çubuğu, toplam gol 1.5/2.5 Üst, karşılıklı gol ve iki takımın ayrı ayrı 1.5 Üst ihtimali. Önümüzdeki 7 günün maçları sezon fikstüründen gelir; oranlar ve eksik oyuncular maçtan bir gün önce eklenir.
- **Maç detayı:** 22 tahmin türü (maç sonucu, çifte şans, 1.5/2.5/3.5 alt-üst, karşılıklı gol, ev sahibi ve deplasman golleri). Ayrıca tahminin gerekçesi, iki takımın son maçları, aralarındaki maçlar ve sakat ya da cezalı oyuncular.
- **Kupon oluştur:** Hedef orana (2, 3, 5, 10) göre tutma ihtimali en yüksek seçimleri birleştirir. İki mod var:
  - **Güvenli:** ihtimali en yüksek seçim.
  - **Değerli:** ihtimali yüksek olup oranı modelin ihtimaline göre fazla olan seçim.
- **Sanal kasalar:** Sistem her gün iki modla, sanal parayla kendi kendine oynar. **Performans** sayfası hangi modun daha iyi gittiğini ve modelin ne kadar isabetli olduğunu gösterir.
- **Kendi kendine çalışır:** Veriyi günceller, biten maçları sonuçlandırır, modelleri her hafta yeniden eğitir. Bilgisayar kapalıyken kaçırdığı işleri açılınca tamamlar.

## Nasıl çalışır

| Katman | Ne yapar |
|---|---|
| Gol modeli | Kulüpler için Dixon-Coles, milli takımlar için Elo farkına dayalı gol modeli |
| Takım gücü | Elo puanları, son maçlar, aralarındaki maçlar |
| Eksik oyuncular | Sakat ve cezalı oyuncuların hücum ve savunmaya etkisi |
| Makine öğrenmesi | Lojistik regresyon ve gradient boosting, ardından kalibrasyon. Sadece ölçülerek daha iyi çıkarsa devreye girer |

**Geçmiş test (2023–2025, 6.052 maç):** Maç sonucunu doğru tahmin oranı %52,7. Modelin "%70 ve üzeri" dediği seçimler %74,2 oranında tuttu. Geçmişteki başarı gelecekte de aynı olacağı anlamına gelmez.

**Teknolojiler:** Python, FastAPI, Jinja2 + HTMX, SQLite (SQLAlchemy), scikit-learn, SciPy, pandas, APScheduler, Chart.js.

## Kurulum (Windows)

**Gerekenler**

- Windows 10 veya 11.
- [Python 3.14](https://www.python.org/downloads/). Kurulumda **"Add python.exe to PATH"** kutusunu işaretleyin. Proje bu sürümle test edildi.
- İnternet: ilk kurulumda Python paketleri için yaklaşık 100 MB, geçmiş maç verileri için yaklaşık 25 MB indirilir (openfootball, milli maçlar ve eksik sonuçlar için ESPN; hiçbiri kayıt ya da anahtar istemez). Sonrasında günlük kullanım çok azdır, her şey önbellekte tutulur.

**Adımlar**

1. Projeyi indirin: GitHub sayfasında **Code → Download ZIP** ile indirip bir klasöre çıkarın ya da `git clone` kullanın.
2. Ücretsiz API anahtarınızı alın (bir sonraki bölüm). Anahtar olmadan da kurulabilir, sonra eklenebilir.
3. `baslat.bat` dosyasına çift tıklayın. İlk seferde şunları yapar:
   - Python ortamını (`.venv`) kurar ve paketleri yükler.
   - Geçmiş maç verilerini indirir ve modelleri eğitir. Bu birkaç dakika sürebilir.
   - Tarayıcıda http://localhost:8765 adresini açar.
4. Sonraki seferlerde `baslat.bat` siteyi hemen açar.

## Ücretsiz API anahtarı

Yaklaşan maçlar, oranlar ve sakat ya da cezalı oyuncular [API-Football](https://www.api-football.com) üzerinden gelir. Anahtar olmadan da site açılır, ama sadece geçmiş veriler ve test raporu görünür.

1. https://dashboard.api-football.com adresinden ücretsiz hesap açın. Kredi kartı istenmez. E-postanızı doğrulayın.
2. Panelde görünen API anahtarını kopyalayın.
3. Anahtarı iki yoldan biriyle girin:
   - **Kolay yol:** Sitede **Ayarlar → API-Football** bölümüne yapıştırıp **Kaydet**'e basın. Anahtar sadece bilgisayarınızdaki veritabanında (`data/bahis.db`) saklanır.
   - **Dosyayla:** `.env.example` dosyasını kopyalayıp adını `.env` yapın. `API_FOOTBALL_KEY=` satırının sonuna anahtarınızı yazın ve siteyi yeniden başlatın (`durdur.bat`, sonra `baslat.bat`).
4. **Ayarlar**'da **"Ücretsiz planın kapsamını dene"** düğmesine basın. Bu 6–8 istek harcar. Bu sezonun maçları geliyorsa her şey hazırdır.

**Ücretsiz planın sınırları**

- Günde 100, dakikada 10 istek. Kota her gün yenilenir; ücretsiz plan süresizdir, ay sonunda bitmez. Site isteklerini kendisi yavaşlatır ve her yanıtı önbellekte tutar, böylece aynı veri iki kez istenmez. Sayfaları gezmek istek harcamaz; istekleri sadece günlük güncelleme ve Ayarlar'daki deneme düğmesi kullanır. Yoğun bir hafta sonu günü yaklaşık 40–60 istek gider. Kota dolarsa sistem durur ve ertesi gün kaldığı yerden devam eder.
- Kotayı artırmak için birden fazla ücretsiz hesap açmak API-Football [kurallarına](https://www.api-football.com/terms) aykırıdır; hesap kapatılabilir.
- Tarihe göre maç sorgusu sadece dün, bugün ve yarın için çalışır. Bu yüzden ileriki günlerin maçları openfootball'daki sezon fikstüründen alınır; oranları ve kesin saati maçtan bir gün önce gelir. Kupon da o zaman kurulabilir.
- Lig ve sezon sorguları sadece 2022–2024 sezonları için çalışır. Bu sezonun ve eski sezonların eksik kalan sonuçları ESPN'den tamamlanır (aşağıda).

> **Anahtarınız size özeldir.** Kimseyle paylaşmayın, ekran görüntüsünde göstermeyin. `.env` dosyası ve `data/` klasörü `.gitignore` içindedir, GitHub'a yüklenmez. Anahtarınız bir yere sızarsa API-Football panelinden yenisini alın.

## Kullanım

| Ne | Nasıl |
|---|---|
| Başlatmak | `baslat.bat`. Sunucu arka planda, pencere açmadan çalışır. |
| Durdurmak | `durdur.bat`. 8765 portunu dinleyen programı kapatır. O portu kullanan başka bir programınız varsa onu da kapatır. |
| Telefondan açmak | Her yerden ve bilgisayar kapalıyken: aşağıdaki **İnternete açma** bölümü. Sadece aynı Wi-Fi'de, kısa süre: `baslat_ag.bat` (**şifre yoktur**, işiniz bitince pencereyi kapatın). |
| Bilgisayar açılınca başlatmak (isteğe bağlı) | Win+R → `shell:startup` yazın, açılan klasöre `arka_plan.vbs` dosyasının kısayolunu koyun. |
| Sunucu kayıtları | `data\sunucu.log` |

**Sayfalar:** Maçlar, Kupon oluştur, Kuponlarım, Performans, Kasalar, Ayarlar. Ayrıntılı anlatım [KULLANIM.md](KULLANIM.md) dosyasında.

**Günlük takvim (Türkiye saati)**

| Saat | İş |
|---|---|
| 10:30 | Yaklaşan maçlar, oranlar, eksik oyuncular ve sonuçlar çekilir, tahminler yenilenir |
| 11:00 | Sistem kasaları sanal parayla otomatik oynar |
| 09:00, 15:00, 20:00, 00:30 | Biten maçlar sonuçlandırılır |
| Her 30 dakikada | Kaçırılan iş varsa tamamlanır |
| Pazartesi 03:00 | Modeller yeniden eğitilir |
| Salı ve Cuma 06:00 | openfootball ve milli maç verileri yenilenir |

**Komut satırı (isteğe bağlı):** `.venv\Scripts\python -m app.cli <komut>`

| Komut | Ne yapar |
|---|---|
| `init` | İlk kurulum: geçmiş veri ve model eğitimi |
| `catch-up` | Zamanı gelmiş bütün işleri yapar (bulutta GitHub Actions bunu çalıştırır) |
| `tasi` | Bilgisayardaki veritabanını `HEDEF_DB_URL` adresindeki bulut veritabanına kopyalar |
| `update` | Yaklaşan maçları, oranları ve eksikleri çeker, tahminleri yeniler |
| `autoplay` | Sistem kasalarıyla otomatik oyun |
| `settle` | Biten maçları sonuçlandırır |
| `history`, `intl` | Kulüp ve milli maç geçmişini yeniler |
| `probe` | Ücretsiz API planının neye izin verdiğini dener |
| `walkforward`, `train-layer2`, `backtest` | Model eğitimi ve geçmiş testi |

**Testler:** `.venv\Scripts\python -m pytest -q`

## İnternete açma (telefondan, bilgisayar kapalıyken)

Üç ücretsiz servis birlikte çalışır. Bilgisayarın açık olması gerekmez.

| Parça | Servis | Ne yapar |
|---|---|---|
| Site | [Render](https://render.com) (ücretsiz plan) | Sayfaları gösterir. Şifre sorar. 15 dakika kullanılmazsa uyur; uyurken ilk açılış 30–60 saniye sürer. |
| Veritabanı | [Neon](https://neon.tech) (ücretsiz PostgreSQL, 0,5 GB) | Maçlar, tahminler, kasalar, kuponlar |
| Günlük işler | GitHub Actions (public depoda ücretsiz) | Veri güncelleme, otomatik oyun, sonuçlandırma, haftalık eğitim (`.github/workflows/gorevler.yml`) |

Netlify ve Vercel bu proje için uygun değil: Netlify Python sunucusu çalıştırmaz, Vercel'de ise makine öğrenmesi paketleri boyut sınırını aşar ve günlük işler çalışmaz.

**1. GitHub'a yükleyin.** Depo public olabilir: `.env` ve `data/` `.gitignore` içinde, şifre ve anahtarlar depoya girmez.

**2. Neon'da veritabanı açın.** GitHub hesabınızla giriş yapın → *New project* → bölge **AWS Europe Central 1 (Frankfurt)**. *Connect* düğmesindeki bağlantı adresini (`postgresql://...`) kopyalayın. Bu adres bir şifre içerir, kimseyle paylaşmayın.

**3. Bilgisayardaki verileri taşıyın (bir kez).** `.env` dosyasına `HEDEF_DB_URL=<Neon adresi>` satırını ekleyin, sonra:

```
durdur.bat
.venv\Scripts\python -m app.cli tasi
```

Yaklaşık 30–40 MB yüklenir. Hedefte veri varsa komut durur; üzerine yazmak için sonuna `--zorla` ekleyin.

**4. Render'da siteyi açın.** GitHub hesabınızla giriş yapın → *New* → *Blueprint* → bu depoyu seçin. `render.yaml` okunur ve üç değer sorulur:

| Değer | Ne yazılır |
|---|---|
| `DATABASE_URL` | Neon adresi |
| `SITE_PASSWORD` | Siteye giriş şifreniz. Uzun olsun, başka yerde kullanmadığınız bir şifre. |
| `API_FOOTBALL_KEY` | API anahtarınız |

Kurulum bitince site `https://futbol-sonuc-tahmin-....onrender.com` gibi bir adreste açılır. Telefonda açın, şifreyi girin; o cihazda 180 gün boyunca giriş açık kalır. Tarayıcı menüsünden "Ana ekrana ekle" ile uygulama gibi açabilirsiniz. Beş yanlış denemeden sonra giriş 15 dakika kapanır.

**5. Günlük işleri açın.** GitHub'da depo → *Settings* → *Secrets and variables* → *Actions* → *New repository secret*: `DATABASE_URL` (Neon adresi) ve `API_FOOTBALL_KEY`. Sonra *Actions* sekmesi → *Gunluk gorevler* → *Run workflow* ile bir kez deneyin.

**6. Bilgisayardaki otomatik başlatmayı kapatın.** `shell:startup` klasöründeki `arka_plan.vbs` kısayolunu silin. İki sistem birden çalışırsa günlük 100 API isteğini paylaşırlar ve bilgisayardaki eski veritabanı ayrı kupon oynar.

**Buluttaki takvim (Türkiye saati).** GitHub zamanlanmış işleri bazen 5–30 dakika geciktirir; her çalışma zamanı gelmiş ne varsa yapar, kaçan bir iş sonrakinde tamamlanır.

| Saat | İş |
|---|---|
| 09:07 | Sonuçlandırma, günlük güncelleme (maçlar, oranlar, eksikler, tahminler) |
| 11:07 | Otomatik oyun (başlamasına en az 1 saat olan maçlarla) |
| 12:37 | Yedek: 11:07 gecikti ya da atlandıysa tamamlar |
| 15:07, 20:07, 00:37 | Sonuçlandırma |
| Haftada bir | Modeller yeniden eğitilir; Salı ve Cuma openfootball ve milli maç verileri yenilenir |

**Bilinmesi gerekenler**

- GitHub, public depolarda 60 gün commit olmazsa zamanlanmış işleri kapatır. İş akışı her gün kendini yeniden "açık" işaretler; yine de kapanırsa GitHub e-postayla haber verir, *Actions* sekmesinden tekrar açılır.
- Actions kayıtları public depoda herkese görünür. Kayıtlarda sadece maç ve kupon sayıları yazar; şifre ve anahtarlar GitHub tarafından gizlenir.
- Neon'un 0,5 GB sınırı için bulutta 10 günden eski API yanıtlarının içeriği silinir (istek sayaçları kalır).
- İndirme işleri bulutta yapılır; sizin internetinizden sadece açtığınız sayfalar harcanır.

## Klasör yapısı

```
app/
  data/       veri indirme ve içe aktarma (openfootball, martj42, API-Football, ESPN)
  model/      gol modelleri, Elo, makine öğrenmesi katmanı, tahmin türleri
  betting/    seçim, kupon kurma, sanal kasalar, sonuçlandırma
  backtest/   geçmiş testi
  web/        FastAPI uygulaması, sayfa şablonları, CSS ve JS
  jobs.py     zamanlanmış işler
  cli.py      komut satırı
  migrate.py  SQLite'tan bulut veritabanına taşıma
.github/     GitHub Actions: buluttaki günlük işler
render.yaml  Render site ayarı
tests/        otomatik testler (pytest)
docs/         tasarım belgesi ve uygulama planı
data/         sadece sizin bilgisayarınızda: veritabanı, indirilen veriler, modeller (GitHub'a yüklenmez)
```

## Veri kaynakları

- [openfootball](https://github.com/openfootball): kulüp maç geçmişi.
- [martj42/international_results](https://github.com/martj42/international_results): milli maç geçmişi.
- [API-Football](https://www.api-football.com): yaklaşan maçlar, oranlar, eksik oyuncular ve sonuçlar. Kendi kullanım koşulları geçerlidir; her kullanıcı kendi anahtarını kullanır.
- ESPN'in herkese açık maç sonuçları (`site.api.espn.com`, anahtar gerekmez): API-Football'un ücretsiz planının ve openfootball'un vermediği günlerin skorları. Resmi, belgelenmiş bir API değildir; adres değişirse sadece eksik doldurma durur. Elle doldurma: `python -m app.cli espn --sezon 2025 2026 --lig T1`.
- Üçüncü taraf JavaScript kütüphaneleri ve lisansları: [app/web/static/vendor/LISANSLAR.md](app/web/static/vendor/LISANSLAR.md).

## Sorumluluk reddi

- Bu projedeki ihtimaller ve tahminler, geçmiş maç verileri üzerinde çalışan istatistik ve makine öğrenmesi modellerinin hesaplamasıdır. **Gerçek sonuç değildir.** Hiçbir model bir maçın nasıl biteceğini önceden bilemez, her tahmin yanlış çıkabilir.
- Gösterilen oranlar API-Football üzerinden alınan piyasa oranlarıdır ve her an değişebilir.
- Site gerçek parayla oyun oynatmaz. Kasalar sanaldır, para yatırma ya da çekme yoktur. Hiçbir şirketle bağlantısı yoktur.
- Bu bilgilere dayanarak verdiğiniz kararların ve sonuçlarının **sorumluluğu tamamen size aittir.** Geliştirici, doğrudan ya da dolaylı hiçbir kayıptan sorumlu tutulamaz.
- Yazılım "olduğu gibi" sunulur. Doğru, eksiksiz ya da kesintisiz çalışacağı konusunda hiçbir söz verilmez.
- Proje 18 yaşından küçükler için hazırlanmamıştır. Bulunduğunuz ülkenin yasalarına uymak sizin sorumluluğunuzdadır.

## Lisans

Telif hakkı © 2026 Mehmet Uslu. Tüm hakları saklıdır. Kod, incelemek ve öğrenmek için herkese açıktır. Kişisel ve ticari olmayan kullanım için indirip çalıştırabilirsiniz. İzin alınmadan kopyalanıp dağıtılması, satılması ya da ticari amaçla kullanılması yasaktır. Ayrıntı: [LICENSE](LICENSE).
