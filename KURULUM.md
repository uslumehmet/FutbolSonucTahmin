# Kurulum ve ayrıntılar

Kısa tanıtım için [README.md](README.md), sayfaların kullanımı için [KULLANIM.md](KULLANIM.md).

## Nasıl çalışır

| Katman | Ne yapar |
|---|---|
| Gol modeli | Kulüpler için Dixon-Coles, milli takımlar için Elo farkına dayalı gol modeli |
| Takım gücü | Elo puanları, son maçlar, aralarındaki maçlar |
| Eksik oyuncular | Sakat ve cezalı oyuncuların hücum ve savunmaya etkisi |
| Makine öğrenmesi | Lojistik regresyon ve gradient boosting, ardından kalibrasyon. Sadece ölçülerek daha iyi çıkarsa devreye girer |
| Piyasa ile birleştirme | Oran gelen maçlarda tahmin bahisçi oranıyla (Bet365; API-Football yanıt vermezse ESPN'deki DraftKings) birleştirilir; maç sonucunda piyasanın ağırlığı yaklaşık %90, 2.5 Alt/Üst'te %75. 2024–2026'da 1.819 maçlık ölçümde birleşim modelden daha isabetli çıktı. Ayarlar'dan kapatılabilir |

**Geçmiş test (2023–2025, 6.052 maç):** Maç sonucunu doğru tahmin oranı %52,7. Modelin "%70 ve üzeri" dediği seçimler %74,2 oranında tuttu. Geçmişteki başarı gelecekte de aynı olacağı anlamına gelmez. Bahisçinin oranları, tek başına modelden daha isabetli (maç sonucunda %54'e %52); geçmiş oranlarla yapılan testte sitenin seçim kuralları uzun vadede kâra geçmedi.

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

Bütün oranlar (Çifte Şans ve Karşılıklı Gol dahil) ve sakat ya da cezalı oyuncular [API-Football](https://www.api-football.com) üzerinden gelir.

**Anahtar olmadan ya da API yanıt vermezken** site boş kalmaz: önümüzdeki maçlar, saatleri, milli maçlar ve oranlar ESPN'den gelir. ESPN'de yalnızca Maç Sonucu ve Alt/Üst oranları var; Çifte Şans sadece birkaç büyük maçta, Karşılıklı Gol ve sakatlık bilgisi hiç yok. Bu durumda sitenin üstünde ve maçların yanında uyarı görünür, otomatik kupon kurulmaz (Ayarlar → "API verisi yokken de otomatik kupon kur" ile açılabilir).

1. https://dashboard.api-football.com adresinden ücretsiz hesap açın. Kredi kartı istenmez. E-postanızı doğrulayın.
2. Panelde görünen API anahtarını kopyalayın.
3. Anahtarı iki yoldan biriyle girin:
   - **Kolay yol:** Sitede **Ayarlar → API-Football** bölümüne yapıştırıp **Kaydet**'e basın. Anahtar sadece bilgisayarınızdaki veritabanında (`data/bahis.db`) saklanır.
   - **Dosyayla:** `.env.example` dosyasını kopyalayıp adını `.env` yapın. `API_FOOTBALL_KEY=` satırının sonuna anahtarınızı yazın ve siteyi yeniden başlatın (`durdur.bat`, sonra `baslat.bat`).
4. **Ayarlar**'da **"Ücretsiz planın kapsamını dene"** düğmesine basın. Bu 6–8 istek harcar. Bu sezonun maçları geliyorsa her şey hazırdır.

**Ücretsiz planın sınırları**

- Günde 100, dakikada 10 istek. Kota her gün yenilenir; ücretsiz plan süresizdir, ay sonunda bitmez. Site isteklerini kendisi yavaşlatır ve her yanıtı önbellekte tutar, böylece aynı veri iki kez istenmez. Sayfaları gezmek istek harcamaz; istekleri sadece günlük güncelleme ve Ayarlar'daki deneme düğmesi kullanır. Yoğun bir hafta sonu günü yaklaşık 40–60 istek gider. Kota dolarsa sistem durur ve ertesi gün kaldığı yerden devam eder.
- Kotayı artırmak için birden fazla ücretsiz hesap açmak API-Football [kurallarına](https://www.api-football.com/terms) aykırıdır; hesap kapatılabilir.
- **Paylaşılan IP:** API-Football, aynı IP adresinde birden fazla ücretsiz hesap görünce hesabı otomatik askıya alıyor; tek hesabınız olsa bile. Bulut sunucuları (GitHub Actions, Render), VPN, mobil veri ve Superbox gibi mobil şebekeden çıkan internetler IP'yi başka kullanıcılarla paylaşır. Bu yüzden API'ye yalnızca ev internetine bağlı bilgisayardan istek atılır; GitHub ve Render'dan atılmaz (aşağıda: Bilgisayardaki görev).
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
| 10:30 (ve site açılınca) | Yaklaşan maçlar, oranlar, eksik oyuncular ve sonuçlar çekilir, tahminler yenilenir |
| Güncellemenin hemen ardından | Sistem kasaları sanal parayla otomatik oynar (bir saat sonrasından 24 saat sonrasına kadarki maçlar; günde bir kez) |
| 09:00, 15:00, 20:00, 00:30 | Biten maçlar sonuçlandırılır |
| Her 30 dakikada | Kaçırılan iş varsa tamamlanır |
| Pazartesi 03:00 | Modeller yeniden eğitilir |
| Salı ve Cuma 06:00 | openfootball ve milli maç verileri yenilenir |

**Komut satırı (isteğe bağlı):** `.venv\Scripts\python -m app.cli <komut>`

| Komut | Ne yapar |
|---|---|
| `init` | İlk kurulum: geçmiş veri ve model eğitimi |
| `catch-up` | Zamanı gelmiş bütün işleri yapar (bulutta GitHub Actions bunu çalıştırır) |
| `bulut-guncelle` | Bilgisayardaki görev: `HEDEF_DB_URL`'deki bulut veritabanına bağlanıp zamanı gelen işleri yapar (API-Football buradan çağrılır) |
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
| Günlük işler | GitHub Actions (public depoda ücretsiz) | ESPN'den maçlar, saatler ve oranlar; sonuçlandırma, haftalık eğitim (`.github/workflows/gorevler.yml`). API-Football'a istek atmaz |
| API-Football ve otomatik kupon | Sizin bilgisayarınız | Windows açılınca bir kez çalışır (aşağıda: Bilgisayardaki görev) |

Sadece API-Football verisi ve otomatik kupon için bilgisayarın açılması gerekir; site, maçlar, ESPN oranları ve sonuçlandırma bilgisayar kapalıyken de çalışır.

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
| `API_FOOTBALL_KEY` | API anahtarınız (Render API'ye istek atmaz; anahtar Ayarlar'da da girilebilir, buluttaki veritabanında saklanır) |

Kurulum bitince site `https://futbol-sonuc-tahmin-....onrender.com` gibi bir adreste açılır. Telefonda açın, şifreyi girin; o cihazda 180 gün boyunca giriş açık kalır. Tarayıcı menüsünden "Ana ekrana ekle" ile uygulama gibi açabilirsiniz. Beş yanlış denemeden sonra giriş 15 dakika kapanır.

**5. Günlük işleri açın.** GitHub'da depo → *Settings* → *Secrets and variables* → *Actions* → *New repository secret*: `DATABASE_URL` (Neon adresi). API anahtarı gerekmez; GitHub API'ye istek atmaz. Sonra *Actions* sekmesi → *Gunluk gorevler* → *Run workflow* ile bir kez deneyin.

**6. Bilgisayardaki otomatik başlatmayı kapatın.** `shell:startup` klasöründeki `arka_plan.vbs` kısayolunu silin. İki sistem birden çalışırsa günlük 100 API isteğini paylaşırlar ve bilgisayardaki eski veritabanı ayrı kupon oynar.

**7. Bilgisayardaki görevi kurun** (API-Football verisi ve otomatik kupon için): aşağıdaki bölüm.

**Buluttaki takvim (Türkiye saati).** GitHub'ın kendi zamanlaması saatlerce gecikebiliyor. Bu yüzden iş akışı dışarıdan, ücretsiz [cron-job.org](https://cron-job.org) ile saatinde başlatılır. Kurulum:
- GitHub'da *Settings* → *Developer settings* → *Fine-grained tokens* altında yeni anahtar açın: sadece bu depo, *Actions: Read and write*.
- cron-job.org'da yeni iş açın: adres `https://api.github.com/repos/<kullanıcı>/<depo>/actions/workflows/gorevler.yml/dispatches`, yöntem POST, gövde `{"ref":"main"}`.
- Başlıklar: `Accept: application/vnd.github+json`, `Content-Type: application/json`, `X-GitHub-Api-Version: 2022-11-28`, `Authorization: Bearer <anahtar>`.

İş akışındaki zamanlamalar yedektir. Her çalışma zamanı gelmiş ne varsa yapar; kaçan bir iş sonrakinde tamamlanır.

| Saat | İş |
|---|---|
| 09:10 | Sonuçlandırma, günlük güncelleme (ESPN: maçlar, saatler, oranlar, milli maç sonuçları; tahminler) |
| 11:10 | Yedek güncelleme. Otomatik kupon burada kurulmaz, bilgisayar açılınca kurulur (Ayarlar'daki "API verisi yokken de otomatik kupon kur" açıksa GitHub da ESPN oranlarıyla kurar) |
| 15:10, 20:10, 23:10 | Sonuçlandırma |
| Haftada bir | Modeller yeniden eğitilir; Salı ve Cuma openfootball ve milli maç verileri yenilenir |

**Bilinmesi gerekenler**

- GitHub, public depolarda 60 gün commit olmazsa zamanlanmış işleri kapatır. İş akışı her gün kendini yeniden "açık" işaretler; yine de kapanırsa GitHub e-postayla haber verir, *Actions* sekmesinden tekrar açılır.
- Actions kayıtları public depoda herkese görünür. Kayıtlarda sadece maç ve kupon sayıları yazar; şifre ve anahtarlar GitHub tarafından gizlenir.
- Neon'un 0,5 GB sınırı için bulutta 10 günden eski API yanıtlarının içeriği silinir (istek sayaçları kalır).
- İndirme işleri bulutta yapılır; sizin internetinizden sadece açtığınız sayfalar harcanır.

## Bilgisayardaki görev

API-Football'a istek yalnızca ev internetine bağlı bilgisayarınızdan gider (yukarıda: Paylaşılan IP). Görev Windows her açıldığında (oturum açılınca) yaklaşık 1,5 dakika sonra bir kez, pencere açmadan çalışır:

1. Buluttaki veritabanına bağlanır (`.env`'deki `HEDEF_DB_URL`).
2. Biten maçları sonuçlandırır; API-Football'dan maçları, bütün oranları, sakat ve cezalı oyuncuları alır, tahminleri yeniler.
3. O günün otomatik kuponunu kurar: bir saat sonrasından 24 saat sonrasına kadarki maçlarla, günde bir kez. Önceki kupona giren maçlar tekrar oynanmaz.
4. Kapanır. Render'daki site aynı veritabanını okuduğu için her şey birkaç dakikada sitede görünür.

**Kurulum:** `.env` dosyasında `HEDEF_DB_URL=<Neon adresi>` olsun (anahtar `.env`'de `API_FOOTBALL_KEY=` olarak ya da sitede Ayarlar'da). Sonra `gorev_kur.bat` dosyasına çift tıklayın. Kaldırmak için `gorev_kaldir.bat`. Kayıtlar: `data\gorev.log`. Elle çalıştırmak: `.venv\Scripts\python -m app.cli bulut-guncelle`.

**Bilinmesi gerekenler**

- Görev bilgisayar uykudan uyanınca değil, Windows açılıp oturum açılınca çalışır.
- Bilgisayarı açmadığınız gün API verisi gelmez ve otomatik kupon kurulmaz; site ESPN verisiyle çalışmaya devam eder.
- Bilgisayar telefondan internet paylaşımına, mobil veriye ya da VPN'e bağlıyken görevi çalıştırmayın: IP başkalarıyla paylaşıldığı için hesap askıya alınabilir.
- API hesap askısı ya da geçersiz anahtar hatası verirse o çalıştırmada başka istek atılmaz; Ayarlar'da "API durumu" satırı nedeni gösterir.

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
data/         sadece sizin bilgisayarınızda: veritabanı, indirilen veriler, modeller (GitHub'a yüklenmez)
```

## Veri kaynakları

- [openfootball](https://github.com/openfootball): kulüp maç geçmişi.
- [martj42/international_results](https://github.com/martj42/international_results): milli maç geçmişi.
- [API-Football](https://www.api-football.com): yaklaşan maçlar, oranlar, eksik oyuncular ve sonuçlar. Kendi kullanım koşulları geçerlidir; her kullanıcı kendi anahtarını kullanır.
- ESPN'in herkese açık maç verisi (`site.api.espn.com`, anahtar gerekmez): API-Football'un ücretsiz planının ve openfootball'un vermediği günlerin skorları; önümüzdeki 3 günün kulüp ve milli maçları, saatleri, Maç Sonucu ve Alt/Üst oranları (DraftKings; büyük maçlarda Bet365, Çifte Şans dahil), milli maç sonuçları. Resmi, belgelenmiş bir API değildir; adres değişirse sadece eksik doldurma durur. Elle doldurma: `python -m app.cli espn --sezon 2025 2026 --lig T1`.
- Üçüncü taraf JavaScript kütüphaneleri ve lisansları: [app/web/static/vendor/LISANSLAR.md](app/web/static/vendor/LISANSLAR.md).
