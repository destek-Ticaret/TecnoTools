"""Admin action → public SSE event sniff testi."""
import asyncio, json, httpx

BASE = "http://127.0.0.1:8000"


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as http:
        r = await http.post("/api/auth/login", json={"username":"admin","password":"Admin2026!","totp_code":None})
        r.raise_for_status()
        access = r.json()["access_token"]
        H = {"Authorization": f"Bearer {access}"}
        print("[1] admin logged in")

        # Public SSE'i dinle (background task)
        events_seen = []
        async def sniff():
            async with httpx.AsyncClient(base_url=BASE, timeout=30) as h2:
                async with h2.stream("GET", "/api/events") as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            try:
                                payload = json.loads(line[6:])
                                events_seen.append((payload.get("event"), payload.get("data")))
                            except Exception:
                                pass
                        if len(events_seen) >= 4:
                            return
        sniffer = asyncio.create_task(sniff())
        await asyncio.sleep(0.5)

        # 2) Kategori ekle
        c = await http.post("/api/categories", json={"name":"LiveTest Kategori","sort_order":99}, headers=H)
        c.raise_for_status()
        cid = c.json()["id"]
        print(f"[2] created category id={cid}")

        # 3) Kupon ekle
        cp = await http.post("/api/coupons", json={
            "code":"LIVE2026","type":"percent","value":10,"min_order":0,"max_uses":100,"is_active":True
        }, headers=H)
        if cp.status_code >= 300:
            print("  coupon failed:", cp.status_code, cp.text)
        else:
            print("[3] coupon created")

        # 4) Ayar değiştir
        s = await http.put("/api/settings", json={"shipping_fee_default":"45.50"}, headers=H)
        s.raise_for_status()
        print("[4] settings updated:", s.json())

        # 5) Kategoriyi sil
        d = await http.delete(f"/api/categories/{cid}", headers=H)
        if d.status_code in (200, 204):
            print("[5] category deleted")

        # SSE event'leri topla
        try:
            await asyncio.wait_for(sniffer, timeout=6.0)
        except asyncio.TimeoutError:
            sniffer.cancel()

        print("\nSSE'de yakalanan event'ler:")
        for e, d in events_seen:
            print(f"  • {e}: {d}")

        names = {e for e, _ in events_seen}
        for expected in ("category_created", "coupon_created", "settings_updated", "category_deleted"):
            assert expected in names, f"BEKLENİYORDU AMA YOK: {expected}"
        print("\nALL EVENTS BROADCAST OK")


asyncio.run(main())
