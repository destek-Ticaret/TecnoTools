# Production Deploy Rehberi

Bu rehber `tecnotools.com` (veya başka bir alan adı) ile canlıya almak için adım adım talimatları içerir. Hedef: tek bir Linux sunucu (Ubuntu 22.04 LTS önerilen), Docker tabanlı, HTTPS aktif.

> Tahmini süre: 30-45 dakika (DNS propagation hariç).

---

## 0. Sunucu gereksinimleri

| Kaynak | Minimum | Önerilen |
|---|---|---|
| CPU | 1 vCore | 2 vCore |
| RAM | 1 GB | 2 GB |
| Disk | 20 GB SSD | 40 GB SSD |
| OS | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| Bant genişliği | 1 TB/ay | — |

DigitalOcean / Hetzner / Vultr / AWS Lightsail'de aylık 5-10 USD seviyesinde uygun.

---

## 1. DNS

Domain sağlayıcında (GoDaddy, Namecheap, Cloudflare vb.) iki A kaydı:

```
tecnotools.com          A    <SUNUCU_IP>
www.tecnotools.com      A    <SUNUCU_IP>
```

Doğrulama: `dig tecnotools.com +short` → sunucu IP'sini döndürmeli.

---

## 2. Sunucu ilk kurulumu

```bash
# SSH ile bağlan
ssh root@<SUNUCU_IP>

# Yeni kullanıcı (root yerine)
adduser deploy
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy
su - deploy

# Firewall
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable

# Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit  # SSH'tan çık ve tekrar bağlan ki docker grubu aktif olsun
ssh deploy@<SUNUCU_IP>
docker --version  # doğrulama
docker compose version
```

---

## 3. Projeyi kopyala

```bash
cd ~
git clone <REPO_URL> tecnotools
cd tecnotools/backend

# Üretim env dosyasını hazırla
cp .env.production.example .env
nano .env   # Tüm __REPLACE__ değerlerini doldur
```

**Kritik değerler:**
- `APP_SECRET_KEY`: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` ile üret
- `POSTGRES_PASSWORD`: en az 24 karakter, rastgele
- `INITIAL_ADMIN_PASSWORD`: ilk girişten sonra hemen değiştir
- `PAYTR_*`: PayTR mağaza panelinden al, **TEST_MODE=0**

Frontend statiklerini de aynı yere kopyala:

```bash
mkdir -p static/js
cp ../index.html static/
cp ../admin.html static/
cp ../js/api.js static/js/

# Frontend'deki API base'i HTTPS'e güncelle
sed -i 's|http://localhost:8000|https://tecnotools.com|g' static/index.html static/admin.html
```

---

## 4. nginx sertifika ayarı (HTTP-only ile başlat)

İlk başlangıçta SSL sertifikası henüz yok. Geçici olarak `nginx/sites/tecnotools.conf`'taki HTTPS server bloğunu yorumla ve sadece HTTP açık olsun.

```bash
# Geçici: sadece HTTP
sed -i 's|^server {$|# server {|' nginx/sites/tecnotools.conf  # CAUTION manuel düzenle
```

Veya daha basit: ilk kez bir minimal HTTP-only config kullan:

```bash
cat > nginx/sites/tecnotools.conf.initial <<'EOF'
server {
    listen 80;
    server_name tecnotools.com www.tecnotools.com;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 200 'OK'; add_header Content-Type text/plain; }
}
EOF
mv nginx/sites/tecnotools.conf nginx/sites/tecnotools.conf.full
cp nginx/sites/tecnotools.conf.initial nginx/sites/tecnotools.conf
```

---

## 5. İlk başlatma + Let's Encrypt sertifikası

```bash
docker compose -f docker-compose.prod.yml up -d db api nginx

# DB migration
docker compose -f docker-compose.prod.yml exec api alembic upgrade head

# Sertifika al (test sertifikası ile başla, çalışınca prod sertifikası)
docker compose -f docker-compose.prod.yml run --rm certbot certonly \
  --webroot --webroot-path=/var/www/certbot \
  --email senin@email.com \
  --agree-tos --no-eff-email \
  -d tecnotools.com -d www.tecnotools.com
```

Sertifikalar `certbot_certs` volume'ünde `/etc/letsencrypt/live/tecnotools.com/` altında oluşur.

---

## 6. Full HTTPS config

```bash
# Tam config'i geri yükle
mv nginx/sites/tecnotools.conf.full nginx/sites/tecnotools.conf
rm nginx/sites/tecnotools.conf.initial

# nginx yeniden yükle
docker compose -f docker-compose.prod.yml exec nginx nginx -s reload

# Certbot otomatik yenileme service'i başlat (12 saatte bir kontrol)
docker compose -f docker-compose.prod.yml up -d certbot
```

Tarayıcıdan `https://tecnotools.com` → admin paneli açılmalı, padlock ikonu görünmeli.

---

## 7. PayTR Notification URL doğrulama

PayTR Mağaza Paneli → **Ayarlar → Bildirim URL** alanına:
```
https://tecnotools.com/api/payments/paytr/callback
```

Bunu yazdıktan sonra **bir test ödemesi yap** (test kartı: `4355 0843 5508 4644 / 12/24 / 000`). PayTR notification gelmesi ve siparişin admin panelinde `processing` durumuna geçmesi gerekir.

Sorun varsa: `docker compose logs api` → callback isteğini gör.

---

## 8. Otomatik yedekleme + S3 sync

Hazır scriptler `scripts/backup-db.sh` ve `scripts/restore-db.sh` içinde. Cron her gece 03:00'te çalıştırır.

### Kurulum

```bash
cd ~/tecnotools/backend
chmod +x scripts/backup-db.sh scripts/restore-db.sh
mkdir -p backups

# AWS CLI (S3 uyumlu — R2/B2/Spaces da çalışır)
sudo apt install -y awscli
aws configure  # access key, secret, region
```

S3-uyumlu sağlayıcı için (R2, Backblaze, DO Spaces) `~/.aws/config`:
```ini
[default]
region = auto
output = json
```
Ve env değişkeni: `AWS_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com`

### Cron job

```bash
sudo tee /etc/cron.d/tecnotools-backup > /dev/null <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
PROJECT_DIR=/home/deploy/tecnotools/backend
POSTGRES_USER=tecnotools
POSTGRES_DB=tecnotools
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=auto
AWS_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
S3_BACKUP_BUCKET=s3://tecnotools-backups

0 3 * * * deploy /home/deploy/tecnotools/backend/scripts/backup-db.sh >> /var/log/tecnotools-backup.log 2>&1
EOF
```

Local retention 14 gün (script otomatik temizler). S3 retention için bucket lifecycle policy'si kullanın (örn 90 gün sonra Glacier'a, 365 gün sonra sil).

### Geri yükleme

```bash
# Local yedekten
./scripts/restore-db.sh backups/db_20260514_030000.sql.gz

# S3'ten
./scripts/restore-db.sh s3://tecnotools-backups/db_20260514_030000.sql.gz
```

Scripti çalıştırmadan önce mutlaka EVET yazılarak onay istenir.

### Manuel tek seferlik backup

```bash
./scripts/backup-db.sh
```

Log için `/var/log/tecnotools-backup.log` izleyin. Backup başarısız olursa cron @reboot ile email gönderecek bir hook eklenebilir (uyarı için).

---

## 9. Güncellemeler

```bash
cd ~/tecnotools
git pull origin main

# Backend kodu değiştiyse
cd backend
docker compose -f docker-compose.prod.yml build api
docker compose -f docker-compose.prod.yml up -d api

# Migration uygula (yeni revision varsa otomatik atlar)
docker compose -f docker-compose.prod.yml exec api alembic upgrade head

# Test suite'i sunucuda çalıştır (opsiyonel, deploy doğrulaması)
docker compose -f docker-compose.prod.yml exec api pytest --tb=short

# Frontend değiştiyse
cp ../index.html ../admin.html static/
cp ../js/api.js static/js/
sed -i 's|http://localhost:8000|https://tecnotools.com|g' static/index.html static/admin.html
docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
```

**Sıfır kesinti deploy** için: `docker compose up -d --no-deps --build api` (eski container yeni kalkıncaya kadar trafik almaya devam eder).

---

## 10. İzleme

### Log akışı
```bash
docker compose -f docker-compose.prod.yml logs -f api      # backend logu
docker compose -f docker-compose.prod.yml logs -f nginx    # erişim logu
```

### Sağlık kontrolü
```bash
curl https://tecnotools.com/api/health
# {"status":"ok","env":"production"}
```

### Disk kullanımı
```bash
docker system df            # docker imaj/volume kullanımı
df -h                       # genel disk
du -sh backend/uploads/     # resim klasörü
```

### Resim klasörü temizliği
Silinen ürünlerin resimleri uploads klasöründe kalır (dedup için). Aylık manuel temizlik scripti yazılabilir veya boş bırakılabilir (genelde 1-2 GB seviyesinde kalır).

---

## 11. Sertifika yenileme

`certbot` container'ı her 12 saatte bir kontrol eder ve gerekirse yeniler. Yenileme sonrası nginx'i otomatik reload etmek için:

```bash
# Manuel reload (otomasyon istenirse certbot hook kullan)
docker compose -f docker-compose.prod.yml exec nginx nginx -s reload
```

Önemli: Sertifika 90 gün geçerli. Cron işleyene kadar 60. günde yenilenmeye başlar. Telegram/email ile uyarı için:

```bash
echo '0 8 * * * docker compose -f /home/deploy/tecnotools/backend/docker-compose.prod.yml exec -T nginx openssl x509 -checkend 1814400 -noout -in /etc/letsencrypt/live/tecnotools.com/cert.pem || curl -X POST -d "chat_id=YOUR_CHAT&text=TecnoTools SSL bitiyor!" https://api.telegram.org/botYOUR_TOKEN/sendMessage' | crontab -
```

---

## 12. Sertleştirme kontrol listesi

- [ ] `INITIAL_ADMIN_PASSWORD` ilk girişten sonra değiştirildi
- [ ] `APP_SECRET_KEY` ve `POSTGRES_PASSWORD` benzersiz ve uzun
- [ ] Firewall (`ufw`) sadece 22, 80, 443 açık
- [ ] SSH key-only login (şifre devre dışı): `sudo nano /etc/ssh/sshd_config` → `PasswordAuthentication no`
- [ ] `fail2ban` SSH brute force koruması: `sudo apt install fail2ban`
- [ ] Sunucuda otomatik güvenlik güncellemeleri: `sudo apt install unattended-upgrades`
- [ ] PayTR `TEST_MODE=0` (production)
- [ ] CORS `https://...` ile sınırlı (HTTP origin'leri çıkar)
- [ ] HSTS header aktif (yukarıdaki nginx config'te)
- [ ] DB dış dünyaya kapalı (`docker-compose.prod.yml`'de `ports:` yok)
- [ ] Yedekleme cron'u test edildi
- [ ] Mailtrap'ten production SMTP'ye geçildi

---

## Sorun giderme

**API 502 dönüyor:**
```bash
docker compose -f docker-compose.prod.yml logs api | tail -50
# Genelde DATABASE_URL yanlış veya migration çalıştırılmamış
```

**PayTR callback gelmiyor:**
```bash
# Notification URL doğru mu?
curl -v https://tecnotools.com/api/payments/paytr/callback
# 405 Method Not Allowed dönmeli (GET değil POST kabul ediyor)
```

**Email gönderilmiyor:**
```bash
docker compose logs api | grep -i email
# SMTP_HOST boşsa konsola yazar → .env'i kontrol et
```

**Sertifika alınmıyor:**
- DNS propagation tamamlandı mı? `dig tecnotools.com +short` doğru IP'yi göstermeli
- 80 portu açık mı? `curl http://tecnotools.com/.well-known/acme-challenge/test` 200 dönmeli
- Rate limit: Let's Encrypt domain başına haftada 50 sertifika sınırı var. Test ederken `--staging` flag'i kullan.
