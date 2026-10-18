#!/usr/bin/env bash
# Trend Radar — Anthropic API anahtarını GitHub secret olarak kaydeder.
#
# Anahtar hiçbir dosyaya, ekrana veya sohbete yazılmaz: gizli olarak okunur
# ve doğrudan GitHub'a (şifreli secret olarak) gönderilir.
#
# Kullanım (macOS Terminal):
#   bash <(curl -fsSL https://raw.githubusercontent.com/KULLANICI/trend-radar/main/api-key-ekle.sh)
#   ya da repo klasöründeyken:  bash api-key-ekle.sh
#   farklı repo için:           bash api-key-ekle.sh kullanici/repo-adi
set -euo pipefail

say() { printf "\n\033[1m%s\033[0m\n" "$*"; }

# 1) GitHub CLI var mı?
if ! command -v gh >/dev/null 2>&1; then
  say "GitHub CLI (gh) bulunamadı, Homebrew ile kuruluyor…"
  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew yok. Önce https://brew.sh adresindeki komutla Homebrew'u kur, sonra bu script'i tekrar çalıştır."
    exit 1
  fi
  brew install gh
fi

# 2) GitHub'a giriş yapılmış mı?
if ! gh auth status >/dev/null 2>&1; then
  say "GitHub'a giriş yapman gerekiyor (tarayıcı açılacak)…"
  gh auth login --hostname github.com --git-protocol https --web
fi

# 3) Hangi repo?
REPO="${1:-}"
if [ -z "$REPO" ]; then
  REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
fi
if [ -z "$REPO" ]; then
  REPO="$(gh api user -q .login)/trend-radar"
fi
say "Repo: $REPO"

# 4) Anahtarı gizli olarak oku
echo "Claude Console'dan aldığın API anahtarını yapıştır ve Enter'a bas."
echo "(Yazdığın görünmeyecek, bu normal.)"
read -r -s -p "API anahtarı: " KEY
echo
KEY="$(printf %s "$KEY" | tr -d '[:space:]')"

if [ -z "$KEY" ]; then
  echo "Boş anahtar girildi, iptal."; exit 1
fi
LEN=${#KEY}
echo "Yapıştırılan anahtar: ${KEY:0:13}…${KEY: -4}  (${LEN} karakter)"
case "$KEY" in
  *…*|*...*) echo "✗ Bu kısaltılmış (gizlenmiş) bir anahtar gibi görünüyor. Console'da YENİ anahtar oluşturup ilk gösterildiğinde kopyala."; exit 1 ;;
esac
if [ "$LEN" -lt 80 ]; then
  echo "✗ Anahtar çok kısa (normalde ~100+ karakter). Tamamını kopyaladığından emin ol."; exit 1
fi
case "$KEY" in
  sk-ant-*) ;;
  *) echo "Uyarı: anahtar genelde 'sk-ant-' ile başlar. Yine de devam ediliyor." ;;
esac

# 4b) Workspace ID (sadece workspace'e bağlı olmayan anahtarlar için gerekli)
echo
echo "Anahtarın bir workspace'e bağlı değilse Workspace ID gerekiyor (Console > Settings > Workspaces, 'wrkspc_' ile başlar)."
read -r -p "Workspace ID (yoksa boş bırakıp Enter): " WS
WS="$(printf %s "$WS" | tr -d '[:space:]')"
WS_HDR=()
[ -n "$WS" ] && WS_HDR=(-H "anthropic-workspace-id: $WS")

# 5) (İsteğe bağlı) anahtar çalışıyor mu? Çin içinden bu kontrol başarısız olabilir, sorun değil.
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
  -H "x-api-key: $KEY" -H "anthropic-version: 2023-06-01" ${WS_HDR[@]+"${WS_HDR[@]}"} \
  https://api.anthropic.com/v1/models || true)"
case "$CODE" in
  200) echo "✓ Anahtar geçerli." ;;
  401) echo "✗ Anthropic anahtarı reddetti (401). Doğru kopyaladığından emin ol."; exit 1 ;;
  400) echo "✗ İstek reddedildi (400). Anahtar workspace'e bağlı değilse Workspace ID'yi doğru girdiğinden emin ol."; exit 1 ;;
  *)   echo "• Anahtar buradan kontrol edilemedi (kod: ${CODE:-yok}). GitHub üzerinden test edilecek." ;;
esac

# 6) GitHub secret olarak kaydet (stdin üzerinden; komut satırında görünmez)
printf %s "$KEY" | gh secret set ANTHROPIC_API_KEY --repo "$REPO"
unset KEY
say "✓ ANTHROPIC_API_KEY, $REPO reposuna kaydedildi."
if [ -n "$WS" ]; then
  printf %s "$WS" | gh secret set ANTHROPIC_WORKSPACE_ID --repo "$REPO"
  echo "✓ ANTHROPIC_WORKSPACE_ID kaydedildi."
fi

# 7) Bugünün raporunu hemen üret
read -r -p "Bugünün raporunu fikirlerle şimdi üreteyim mi? [E/h] " ANS
if [ -z "$ANS" ] || [[ "$ANS" =~ ^[EeYy] ]]; then
  gh workflow run daily-report.yml --repo "$REPO" -f date=today
  echo "Başlatıldı. 1-2 dakika sonra dashboard'da görünür:"
  echo "  https://$(echo "$REPO" | cut -d/ -f1).github.io/$(echo "$REPO" | cut -d/ -f2)/"
  echo "İlerlemeyi görmek için:  gh run watch --repo $REPO"
fi
