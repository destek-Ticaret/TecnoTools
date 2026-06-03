"""Chat artık sadece üye girisli müsterilere açık olmalı."""
import asyncio, json, secrets
import httpx, websockets

BASE = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000"


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as http:
        # 1) Token'sız WS — reddedilmeli
        try:
            async with websockets.connect(f"{WS}/api/ws/chat/customer"):
                raise AssertionError("Token'sız bağlantı kabul edildi!")
        except (websockets.exceptions.InvalidStatus, websockets.exceptions.InvalidStatusCode):
            print("[1] Token'sız bağlantı 422/403 ile reddedildi (Query param eksik) OK")
        except websockets.exceptions.ConnectionClosed as e:
            print(f"[1] Token'sız bağlantı reddedildi: close code {e.code} OK")

        # 2) Geçersiz token — reddedilmeli (HTTP 403 veya close 4401)
        try:
            async with websockets.connect(f"{WS}/api/ws/chat/customer?token=GECERSIZ") as ws:
                await ws.recv()
                raise AssertionError("Geçersiz token kabul edildi!")
        except (websockets.exceptions.ConnectionClosed, websockets.exceptions.InvalidStatus) as e:
            print(f"[2] Gecersiz token reddedildi ({type(e).__name__}) OK")

        # 3) Yeni bir müsteri olustur (register)
        email = f"chat_member_{secrets.token_hex(4)}@example.com"
        r = await http.post("/api/customer-auth/register", json={
            "email": email, "name": "Chat Üyesi", "password": "Member2026!"
        })
        r.raise_for_status()
        cust_token = r.json()["access_token"]
        print(f"[3] Müsteri kaydedildi: {email}")

        # 4) Geçerli müsteri token'ı ile bağlan — chat_session + history almalı
        async with websockets.connect(f"{WS}/api/ws/chat/customer?token={cust_token}") as ws:
            f1 = json.loads(await ws.recv())
            f2 = json.loads(await ws.recv())
            assert f1["event"] == "chat_session"
            assert f2["event"] == "chat_history"
            sid = f1["data"]["session_id"]
            assert sid.startswith("cust:"), f"session_id beklenen cust:* formatında değil: {sid}"
            assert f1["data"]["customer_name"] == "Chat Üyesi"
            assert f1["data"]["customer_email"] == email
            print(f"[4] Üye bağlandı: session_id={sid}, name={f1['data']['customer_name']}")

            # Mesaj gönder
            await ws.send(json.dumps({"action": "send", "body": "Selam, deneme mesajı"}))
            msg = json.loads(await ws.recv())
            assert msg["event"] == "chat_message"
            assert msg["data"]["sender"] == "customer"
            assert msg["data"]["sender_name"] == "Chat Üyesi"
            print(f"[5] Mesaj gönderildi ve echo'landı (sender_name otomatik DB'den)")

        # 5) Aynı müsteri tekrar bağlanırsa aynı oturuma düsmeli
        async with websockets.connect(f"{WS}/api/ws/chat/customer?token={cust_token}") as ws:
            f1 = json.loads(await ws.recv())
            f2 = json.loads(await ws.recv())
            assert f1["data"]["session_id"] == sid, "İkinci bağlantı yeni oturum açtı"
            history = f2["data"]
            assert len(history) >= 1 and history[0]["body"] == "Selam, deneme mesajı"
            print(f"[6] Aynı müsteri yeniden bağlandı, {len(history)} mesajlık geçmis geldi")

    print("\nALL CHECKS PASS")


asyncio.run(main())
