# Trend Radar

ABD, Almanya, Türkiye, Güney Kore ve Çin'de insanların o gün neleri aramaya başladığını **saatlik** toplar, her gece bir rapor çıkarır ve (istersen) Claude'a bu trendlerden **uygulama fikirleri** ve **video içerik fikirleri** ürettirir. Sonuçlar GitHub Pages üzerinde basit bir dashboard'da görünür.

Sunucu kiralamana gerek yok: her şey ücretsiz GitHub Actions üzerinde çalışır. GitHub'ın sunucuları ABD'de olduğu için Google'a erişim sorunu da olmaz.

## Ne topluyor?

| Pazar | Kaynak | Ne verir |
|---|---|---|
| 🇺🇸 ABD, 🇩🇪 Almanya, 🇹🇷 Türkiye, 🇰🇷 Kore | Google Trends "Trending now" RSS | O an aramaları hızla artan sorgular, tahmini hacim (2000+, 500K+…), ilgili haber linkleri |
| 🇨🇳 Çin | Baidu 热搜 (gerçek zamanlı) | Baidu'nun sıcak arama listesi, sıralı (hacim sayısı yok) |

Bilmen gerekenler:

- Google "en çok aranan"ı değil, **normalden çok daha fazla aranmaya başlayanı** (trend) yayınlıyor. "YouTube", "hava durumu" gibi her gün aranan devler listede çıkmaz, çıkmaması da fikir bulmak için iyi.
- Google RSS her çağrıda ülke başına yaklaşık 10 trend döndürüyor ve geçmiş tutmuyor. Bu yüzden sistem saatte bir çekip **geçmişi kendisi biriktiriyor**. Günün sonunda ülke başına genelde 50-150 farklı trend birikir.
- Google Çin'de çalışmadığı için Google Trends'te Çin verisi yok; Çin için Baidu kullanılıyor. Baidu'nun en üstteki sabitlenmiş (resmî) maddesi varsayılan olarak atlanır.

## Kurulum (yaklaşık 10 dakika)

1. **GitHub'da yeni bir repo aç** (ör. `trend-radar`). **Public** olmasını öneririm: Actions dakikaları sınırsız olur ve Pages ücretsiz çalışır. (Private repoda saatlik toplama ayda ~750 dakika yer; ücretsiz kotan 2000 dakika. Ama private repoda Pages için ücretli plan gerekir.)
2. Bu klasörün içeriğini repoya yükle:
   ```bash
   cd trend-radar
   git init && git add . && git commit -m "ilk sürüm"
   git branch -M main
   git remote add origin https://github.com/<kullanıcı-adın>/trend-radar.git
   git push -u origin main
   ```
3. **AI fikirleri için anahtar ekle (isteğe bağlı ama tavsiye):** Repo → Settings → Secrets and variables → Actions → *New repository secret* → isim `ANTHROPIC_API_KEY`, değer: console.anthropic.com'dan aldığın API anahtarı. Eklemezsen rapor yine oluşur, sadece fikir bölümü boş kalır.
4. **Dashboard'u aç:** Settings → Pages → *Source: Deploy from a branch* → Branch `main`, klasör `/docs` → Save. Birkaç dakika sonra `https://<kullanıcı-adın>.github.io/trend-radar/` adresinde görünür.
5. **İlk çalıştırma:** Actions sekmesi → "Saatlik trend toplama" → *Run workflow*. Sonra "Günlük rapor + fikirler" → *Run workflow* → tarih alanına `today` yaz. Dashboard'da ilk raporu görürsün.

Bundan sonra kendiliğinden çalışır: saatte bir veri toplar, her gece 00:41'de (Şanghay saati) bir önceki günün raporunu ve fikirlerini üretir.

## Çıktılar

- `data/snapshots/YYYY-MM-DD.jsonl` saatlik ham veri (her satır bir trend)
- `reports/YYYY-MM-DD.md` günlük rapor; GitHub'da doğrudan okunabilir, telefondan da rahat
- `docs/` dashboard (sadece sistem fontları ve sıfır harici kütüphane; Çin'den açılırken engellenecek bir CDN yok)

Raporda her trendin yanında:
- 🆕 son 7 günde hiç görülmemiş
- 🔁 son 7 günün en az 3'ünde görülmüş (kalıcı ihtiyaç işareti, app fikri için en değerli sinyal)

## Ayarlar (`config.json`)

| Alan | Açıklama |
|---|---|
| `google_geos` | Takip edilen ülkeler. İki harfli kod ekleyip çıkarabilirsin: `JP`, `GB`, `HK`, `TW`… |
| `baidu_china` | Çin/Baidu açık mı |
| `timezone` | Günlerin hangi saat dilimine göre bölüneceği |
| `report_top_n` | Raporda ülke başına gösterilen trend sayısı |
| `ai_top_n_per_country` | Claude'a ülke başına gönderilen trend sayısı |
| `ai_model` | Kullanılan Claude modeli (varsayılan `claude-fable-5-1`; daha ucuz seçenekler `claude-sonnet-5-5`, `claude-haiku-5-5`) |
| `ai_focus` | Claude'a fikirlerin hangi yöne olmasını istediğini anlatan cümle. Kendi ilgi alanlarına göre değiştir |

Maliyet: günde tek çağrı yapılır; harcamayı Claude Console'daki kullanım sayfasından izleyebilirsin.

## Kendi bilgisayarında çalıştırmak

Python 3.9+ yeter, ek paket yok.

```bash
python collect.py --dry-run          # çekip ekrana yaz
python collect.py                    # çekip data/ içine kaydet
python report.py --date today --no-ai
ANTHROPIC_API_KEY=sk-... python report.py --date today
python report.py --date today --print-prompt --no-ai   # Claude'a ne gideceğini gör
python collect.py --fixtures         # internetsiz test (fixtures/ içindeki örnek veriyle)
```

Çin içinden çalıştırırsan Google kısmı VPN olmadan hata verir; Baidu kısmı çalışır. Bu yüzden asıl çalışma yeri GitHub Actions.

Dashboard'u yerelde görmek için: `cd docs && python -m http.server 8000` → `http://localhost:8000`

## Sorun giderme

- **Workflow push yapamıyor (403):** Settings → Actions → General → *Workflow permissions* → "Read and write permissions" seç.
- **Bir ülke hata veriyor:** Diğer kaynaklar etkilenmez; sadece o ülke o saat atlanır. Hepsi başarısız olursa workflow kırmızı görünür.
- **Zamanlanmış çalışmalar durdu:** GitHub, uzun süre hareketsiz kalan public repolarda zamanlanmış workflow'ları kapatabiliyor. Actions sekmesinde uyarı çıkarsa "Enable workflow" de.
