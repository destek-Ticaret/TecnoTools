# TecnoTools Backend

FastAPI + PostgreSQL + PayTR ödeme entegrasyonu.

## Hızlı Kurulum (Windows / PowerShell)

```powershell
# 1) Klasöre gir
cd C:\Users\Mert\Desktop\TecnoTools\backend

# 2) Sanal ortam
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3) Bağımlılıklar
pip install --upgrade pip
pip install -r requirements.txt

# 4) .env hazırla
copy .env.example .env
# .env dosyasını aç ve APP_SECRET_KEY + PayTR bilgilerini doldur

# 5) PostgreSQL'i Docker ile başlat (alternatif: lokal kurulum)
docker compose up -d

# 6) Alembic ile migration uygula (initial revision hazır)
alembic upgrade head

# 7) Sunucuyu çalıştır
uvicorn app.main:app --reload --port 8000
```

## Testleri çalıştırma

```powershell
# Bağımlılıklar (dev)
pip install -r requirements-dev.txt

# Tüm test suite — SQLite memory ile izole, hızlı
pytest

# Belirli bir modül
pytest tests/test_auth.py -v

# Coverage raporu
pytest --cov=app --cov-report=term-missing

# Tek bir test
pytest tests/test_orders_checkout.py::test_order_no_counter_monotonic -v
```

Testler her çağrıda yeni bir SQLite memory DB kurar; PayTR ve SMTP boş credential ile mock moda düşer. Postgres gerekmez.

API açılınca:
- **OpenAPI / Swagger UI:** http://localhost:8000/docs
- **Health check:** http://localhost:8000/api/health
- **İlk admin:** `.env` içindeki `INITIAL_ADMIN_USERNAME` / `INITIAL_ADMIN_PASSWORD` ile giriş

## PayTR Sandbox

1. https://www.paytr.com/ üzerinden mağaza aç (sandbox dahil).
2. Mağaza Paneli > Bilgi & Ayarlar > Mağaza Bilgileri'nden:
   - `merchant_id`
   - `merchant_key`
   - `merchant_salt`
3. `.env`'ye yapıştır. `PAYTR_TEST_MODE=1` bırak.
4. Notification URL: `https://<senin-public-url>/api/payments/paytr/callback`
   - Lokal geliştirme için **ngrok** ile public URL aç: `ngrok http 8000`
   - Çıkan URL'yi hem `.env`'deki `PAYTR_NOTIFICATION_URL`'e hem de PayTR panelindeki Bildirim URL alanına yaz.
5. Test kartı: `4355 0843 5508 4644 · 12/24 · 000` (PayTR sandbox)

## Klasör Yapısı

```
backend/
├── app/
│   ├── main.py              # FastAPI uygulama girişi
│   ├── config.py            # Pydantic settings (.env oku)
│   ├── database.py          # SQLAlchemy async engine
│   ├── models.py            # ORM modelleri
│   ├── schemas.py           # Pydantic request/response şemaları
│   ├── security.py          # PBKDF2, JWT, refresh token
│   ├── deps.py              # FastAPI dependencies (auth)
│   ├── routers/
│   │   ├── auth.py          # /api/auth/*
│   │   ├── products.py      # /api/products/*
│   │   ├── categories.py    # /api/categories/*
│   │   ├── orders.py        # /api/orders/* + checkout
│   │   ├── coupons.py       # /api/coupons/*
│   │   ├── payments.py      # /api/payments/paytr/callback
│   │   ├── reservations.py  # /api/reservations/*
│   │   └── misc.py          # newsletter, audit, stock-movements
│   └── services/
│       ├── paytr.py         # PayTR iframe API + hash doğrulama
│       └── totp.py          # RFC 6238 TOTP
├── alembic/                 # DB migration'ları
├── docker-compose.yml       # PostgreSQL container
├── requirements.txt
└── .env.example
```

## API Sözleşmeleri (özet)

| Endpoint                                  | Kullanım                                    |
|-------------------------------------------|---------------------------------------------|
| `POST /api/auth/login`                    | `{username, password, totp_code?}`          |
| `POST /api/auth/refresh`                  | `{refresh_token}` → yeni access + refresh    |
| `GET  /api/products`                      | Public — `?session_id=` ile effective_stock |
| `POST /api/products` (admin)              | Yeni ürün                                   |
| `POST /api/orders/checkout` (public)      | Sipariş + PayTR token döner                 |
| `POST /api/payments/paytr/callback`       | PayTR notification (form-encoded)           |
| `GET  /api/payments/order-status/{no}`    | OK URL'de durumu çek                        |
| `POST /api/reservations/sync` (public)    | Cart'ı backend'e bildir                     |

## Notlar

- **Cart hâlâ frontend'de** (`localStorage.tt_cart`); rezervasyon backend'e ayna olarak gönderilir.
- **Stok düşürme** sadece PayTR success callback'te gerçekleşir — başarısız ödemede stok el değmez.
- **Order ID counter** DB'de (`order_counters` tablosu) — `SELECT ... FOR UPDATE` ile yarış koşulundan korunur.
- **Refresh token rotation** her refresh çağrısında eskiyi iptal edip yeni döner.
- **PayTR test mode = 1** olduğu sürece gerçek paraya dokunulmaz.

## Sonraki Adımlar (henüz yapılmadı)

- [ ] Frontend (`admin.html`, `index.html`) localStorage çağrılarını fetch API'ye çevir
- [ ] Mevcut localStorage verilerini bir kerelik backend'e seed eden migration scripti
- [ ] Resim upload endpoint'i (`/api/uploads/images`) + dosya sistemi veya S3
- [ ] Email gönderim servisi (sipariş onayı, kargo bildirimi)
- [ ] Rate limit middleware
- [ ] Production WSGI/ASGI deploy (gunicorn + uvicorn workers)
