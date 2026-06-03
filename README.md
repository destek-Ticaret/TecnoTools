# TecnoTools

Tam kapsamlı e-ticaret platformu — **FastAPI** backend (REST + WebSocket + SSE) ve
**vanilla JS** (build adımsız) storefront + admin paneli.

> Backend kurulum/dağıtım detayları için: [`backend/README.md`](backend/README.md) ·
> [`backend/DEPLOY.md`](backend/DEPLOY.md)

---

## Mimari

```
┌─────────────────────────┐         ┌──────────────────────────────┐
│  Frontend (statik)       │  HTTP   │  Backend (FastAPI, :8000)     │
│  serve_frontend.py :5500 │ ──────► │  REST + WS (/api/ws/*) + SSE  │
│  index/product/cart/...  │  WS/SSE │  SQLAlchemy (async)           │
│  js/common.js + api.js   │ ◄────── │  PostgreSQL (prod) / SQLite   │
│  css/base.css            │         │  PayTR · Stripe · SMTP · S3   │
└─────────────────────────┘         └──────────────────────────────┘
```

- **Frontend**: Derleme yok. Her sayfa bağımsız HTML + inline CSS/JS. Ortak parçalar:
  - `js/common.js` — tema sistemi, `escapeHtml`, `ttReady()` (her sayfada `<head>`'de bloklayıcı yüklenir).
  - `js/api.js` — backend ile tüm iletişim (JWT access/refresh, otomatik 401 yenileme).
  - `css/base.css` — tek tasarım sistemi (renk/gölge/spacing token'ları). Sayfa-özel sapmalar küçük `<style>` override'larında belgelenir.
- **Backend**: `backend/app/` — router'lar (`routers/`), iş mantığı (`services/`), ORM (`models.py`), şemalar (`schemas.py`).

---

## Hızlı başlangıç (geliştirme)

İki sunucu paralel çalışır: API (:8000) ve statik frontend (:5500).

### 1) Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt        # ruff, mypy, pytest dahil
copy .env.example .env                      # APP_SECRET_KEY (≥32 krk) doldur
uvicorn app.main:app --reload --port 8000
```

Geliştirmede `DATABASE_URL` SQLite olabilir (`sqlite+aiosqlite:///./tecnotools.db`);
PostgreSQL gerekmez. İlk admin `.env`'deki `INITIAL_ADMIN_*` ile seed edilir.

### 2) Frontend

```powershell
# Proje kökünden (ayrı terminal)
python serve_frontend.py 5500
```

Aç: <http://localhost:5500/index.html> · Admin: <http://localhost:5500/admin.html>
(admin yalnız `ADMIN_IP_WHITELIST` + localhost'tan erişilebilir).

API dokümanı: <http://localhost:8000/docs> · Health: <http://localhost:8000/api/health>

---

## Geliştirme akışı

```powershell
cd backend
ruff check app tests        # lint (CI'da bloklar)
ruff format app tests       # otomatik biçimlendirme
mypy app                    # tip kontrolü (CI'da şimdilik rapor modu)
pytest                      # test suite (SQLite memory, izole, hızlı)
pytest --cov=app            # coverage
```

- **Pre-commit hook'ları** (opsiyonel ama önerilir): `pip install pre-commit && pre-commit install`
  — her commit'te ruff lint + format otomatik çalışır.
- **CI** (`.github/workflows/ci.yml`): test + coverage, ruff lint/format (bloklar),
  mypy (rapor), Alembic migration sanity (Postgres), Docker build.

---

## Sayfalar (frontend)

| Sayfa | Açıklama |
|---|---|
| `index.html` | Vitrin / ürün listeleme |
| `product.html` | Ürün detayı (galeri, yorum, Q&A) |
| `cart.html` · `checkout.html` | Sepet ve ödeme |
| `account.html` | Müşteri paneli (profil, siparişler, favoriler, KVKK) |
| `track-order.html` | Üyeliksiz sipariş takibi (sipariş no + e-posta) |
| `admin.html` | Yönetim paneli (IP-kısıtlı) |

---

## Önemli konvansiyonlar

- **Para birimi**: fiyatlar `BASE_CURRENCY`'de saklanır, gösterimde çevrilir.
- **Stok**: yalnız ödeme başarılı callback'inde düşülür; sepet rezervasyonu ayna olarak tutulur.
- **Auth**: admin ve müşteri JWT'leri `type` ile ayrılır; refresh token rotation aktif.
- **Güvenlik**: production'da zayıf `APP_SECRET_KEY` ile başlatma engellenir (fail-fast);
  IP/rate-limit kararları nginx'in sabitlediği `X-Real-IP`'ye dayanır (XFF'e güvenilmez).
- **Satır sonları**: repo genelinde LF (`.gitattributes`).

---

## Lisans / İletişim

Özel proje. Sorular için repo sahibine ulaşın.
