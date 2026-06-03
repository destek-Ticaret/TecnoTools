# Manuel smoke scriptleri

Bunlar **pytest testi değildir** (o yüzden `test_` öneki yoktur ve `tests/`
dışındadır). Çalışan bir backend'e (`:8000`) karşı uçtan-uca akışı elle
doğrulamak içindir — WebSocket / SSE gibi in-process test edilmesi zor akışlar.

## Çalıştırma

```powershell
# 1) Backend'i ayağa kaldır
cd backend
uvicorn app.main:app --port 8000

# 2) Ayrı terminalde scripti çalıştır
.venv\Scripts\python.exe scripts\manual\chat_member_only.py
.venv\Scripts\python.exe scripts\manual\live_events.py
```

> Admin parolası script içinde sabittir (`Admin2026!`) — lokal `.env`'deki
> `INITIAL_ADMIN_PASSWORD` ile eşleşmeli.

## Scriptler

| Script | Doğrular |
|---|---|
| `chat_member_only.py` | Canlı destek WS'i yalnız üye girişli müşteriye açık (token'sız/geçersiz reddedilir, üye token'ı kabul edilir) |
| `live_events.py` | Admin aksiyonu → public SSE event yayını (`/api/events`) |

> Not: Eski `test_chat_e2e.py` (anonim `session_id` akışı) chat token'lı hale
> getirildiğinde geçersiz kaldı ve `chat_member_only.py` ile değiştirildi.
