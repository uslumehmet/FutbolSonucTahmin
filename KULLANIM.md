# FutbolSonuçTahmin — Kullanım

Kurulum ve ücretsiz API anahtarı için önce [README.md](README.md) dosyasına bakın.

## Başlatma
Sunucu **pencere açmadan arka planda** çalışır.
- Siteyi açmak için: `baslat.bat` (sistem kapalıysa önce başlatır, sonra tarayıcıda http://localhost:8765 açar) ya da tarayıcıda doğrudan http://localhost:8765
- Durdurmak için: `durdur.bat` (8765 portunu dinleyen programı kapatır; o portu başka bir program kullanıyorsa onu da kapatır)
- Bilgisayar açılınca kendiliğinden başlasın isterseniz: Win+R → `shell:startup` → açılan klasöre `arka_plan.vbs` dosyasının kısayolunu koyun. Kaldırmak için o kısayolu silin.
- Telefondan, her yerden ve bilgisayar kapalıyken açmak için siteyi internete açın: README'deki "İnternete açma" bölümü (Render + Neon + GitHub Actions, şifreli giriş).
- Sadece aynı Wi-Fi'de kısa süre için `baslat_ag.bat` (şifre yok; pencere kapanınca durur).
- Sunucu kayıtları: `data\sunucu.log`

Bilgisayar kapalıyken sistem çalışmaz. Açtığınızda kaçırdığı işleri yapar, ama başlamış maçlara kupon yazamaz. Uykudan uyanınca 30 dakika içinde telafi eder.

## API anahtarı (bir kez)
1. https://dashboard.api-football.com adresinden ücretsiz kayıt olun (kredi kartı istemez).
2. Anahtarı **Ayarlar → API-Football** bölümüne yapıştırıp kaydedin.
3. **"Ücretsiz planın kapsamını dene"** düğmesine basın. Bu sezonun maçları geliyorsa her şey hazır.

Anahtar olmadan yaklaşan maçlar, oranlar ve sakat/cezalı listeleri gelmez; geçmiş veriler ve test raporu yine görünür.

## Sistem ne zaman ne yapar (Türkiye saati)
| Saat | İş |
|---|---|
| 10:30 | Yaklaşan maçlar, oranlar, eksik oyuncular ve tahminler güncellenir |
| 11:00 | Sistem iki moddan (Güvenli, Değerli) 2, 3 ve 5 oranlı kuponları sanal kasalarla oynar |
| 09:00, 15:00, 20:00, 00:30 | Biten maçlar sonuçlandırılır |
| Pazartesi 03:00 | Makine öğrenmesi katmanı yeniden eğitilir |

## Sayfalar
- **Maçlar:** Her maçın 1-X-2 ihtimal çubuğu, 2.5 Üst ve KG Var ihtimali, Güvenli/Değerli seçimi. "7 gün" sekmesi haftanın bütün maçlarını gösterir; "oran 1 gün önce gelir" etiketli maçlar sezon fikstüründen gelmiştir, saatleri değişebilir.
- **Maç detayı:** Bütün bahis seçenekleri (ihtimal, oran, değer), gerekçe, iki takımın son maçları, aralarındaki maçlar, eksik oyuncular. Eksik oyuncuyu elle ekleyebilirsiniz; tahmin hemen güncellenir.
- **Kupon oluştur:** Hedef oran + mod seçin, sistem en yüksek tutma ihtimalli kuponu kurar; sanal kasanızla oynarsınız.
- **Kuponlarım:** Her kasanın kuponları, tuttu/tutmadı durumu. Üstte sistemin bugünkü otomatik oyununun ne zaman yapıldığı, kaç kupon oynandığı, oynanmadıysa nedeni ve sıradaki maç günü yazar. Bilgisayar 11:00'den sonra açılırsa otomatik oyun açılışta, henüz başlamamış maçlarla (başlamasına en az 1 saat olan) yapılır.
- **Kasalar:** 100 ₺ ve 1.000 ₺ gibi farklı başlangıçlı kasalar; istediğiniz kadar yenisini açabilirsiniz.
- **Performans:** Hangi mod kazandırıyor, model söylediği ihtimallerde ne kadar isabetli, geçmiş test raporu.

## Dürüst not
Bu sitedeki ihtimaller istatistik ve makine öğrenmesi modellerinin tahminidir, gerçek sonuç değildir; verilen kararların sorumluluğu kullanıcıya aittir (ayrıntı: README'deki "Sorumluluk reddi"). Hiçbir tahmin kesin değildir. Geçmiş testte (2023–2025, 6.052 maç) maç sonucunu bilme oranı %53; model "%70 ve üstü" dediğinde %74 tuttu. Sistemin para kazandırıp kazandırmadığını ancak sanal kasaların birkaç haftalık sonucu gösterir.

## Komut satırı (isteğe bağlı)
`.venv\Scripts\python -m app.cli <komut>`: `update`, `autoplay`, `settle`, `probe`, `backtest`, `train-layer2`.
