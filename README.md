# FutbolSonuçTahmin

Süper Lig, Avrupa'nın 5 büyük ligi ve milli maçlar için maç sonuçlarını istatistik ve makine öğrenmesiyle tahmin eden kişisel bir web sitesi.

> Tahminler bir hesaplamadır, gerçek sonuç değildir; her tahmin yanlış çıkabilir. Site gerçek parayla oyun oynatmaz, kasalar sanaldır.

## Neler yapar

- Her maç için maç sonucu, toplam gol, karşılıklı gol ve takım golleri ihtimallerini gösterir.
- Seçtiğiniz hedef orana göre tutma ihtimali en yüksek kuponu kurar; kupondaki bahisleri değiştirebilirsiniz.
- Her gün sanal parayla kendi kendine oynar; Performans sayfası nasıl gittiğini gösterir.
- Verileri kendisi günceller, biten maçları sonuçlandırır, modelleri her hafta yeniden eğitir.

Tahmin; gol modeli (Dixon-Coles), Elo puanları, eksik oyuncular ve makine öğrenmesiyle yapılır. Oranı gelen maçlarda bahisçi oranıyla birleştirilir. Geçmiş testte (6.052 maç) maç sonucunu %52,7 doğru bildi.

## Kurulum (Windows)

1. [Python 3.14](https://www.python.org/downloads/) kurun. Kurulumda "Add python.exe to PATH" kutusunu işaretleyin.
2. Projeyi indirin (**Code → Download ZIP**) ve `baslat.bat` dosyasına çift tıklayın. İlk açılış birkaç dakika sürer, site http://localhost:8765 adresinde açılır.
3. [API-Football](https://dashboard.api-football.com)'dan ücretsiz anahtar alın ve sitede **Ayarlar** sayfasına yapıştırın.

Telefondan ve bilgisayar kapalıyken kullanmak, ücretsiz planın sınırları ve komutlar: [KURULUM.md](KURULUM.md). Sayfaların kullanımı: [KULLANIM.md](KULLANIM.md).

## Sorumluluk reddi

İhtimaller geçmiş maç verileriyle çalışan modellerin hesaplamasıdır; hiçbir model bir maçın nasıl biteceğini önceden bilemez. Oranlar piyasadan alınır ve değişebilir. Site hiçbir şirketle bağlantılı değildir; para yatırma ya da çekme yoktur. Bu bilgilere dayanarak verdiğiniz kararların sorumluluğu size aittir. Yazılım olduğu gibi sunulur ve 18 yaşından küçükler için hazırlanmamıştır.

## Lisans

© 2026 Mehmet Uslu. Tüm hakları saklıdır. Kod incelemek ve kişisel kullanım için açıktır; izin alınmadan kopyalanıp dağıtılamaz, satılamaz. Ayrıntı: [LICENSE](LICENSE).
