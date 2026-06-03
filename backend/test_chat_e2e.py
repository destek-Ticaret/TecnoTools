"""End-to-end smoke test for live chat (run with backend up on :8000)."""
import asyncio
import json

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000"
SID = "test_session_e2e_001"


async def main():
    # 1) Admin login
    async with httpx.AsyncClient(base_url=BASE) as http:
        r = await http.post("/api/auth/login", json={
            "username": "admin", "password": "Admin2026!", "totp_code": None,
        })
        r.raise_for_status()
        access = r.json()["access_token"]
        print("[1] admin login OK")

        # 2) Customer connects, sends a message
        async with websockets.connect(f"{WS}/api/ws/chat/customer?session_id={SID}") as cust:
            # Greeting frames: chat_session + chat_history
            ev1 = json.loads(await cust.recv()); assert ev1["event"] == "chat_session", ev1
            ev2 = json.loads(await cust.recv()); assert ev2["event"] == "chat_history", ev2
            print(f"[2] customer connected; status={ev1['data']['status']}, history={len(ev2['data'])}")

            await cust.send(json.dumps({"action": "identify", "name": "Test Müşteri", "email": "test@example.com"}))
            await cust.send(json.dumps({"action": "send", "body": "Merhaba, ürün hakkında soru sormak istiyorum"}))

            # Expect echo of the message
            ev3 = json.loads(await cust.recv()); assert ev3["event"] == "chat_message", ev3
            assert ev3["data"]["body"].startswith("Merhaba"), ev3
            print("[3] customer message echoed back")

            # 3) Admin lists sessions, finds ours
            r = await http.get("/api/chat/admin/sessions", headers={"Authorization": f"Bearer {access}"})
            r.raise_for_status()
            sessions = r.json()
            ours = next((s for s in sessions if s["session_id"] == SID), None)
            assert ours, f"session not in list: {sessions}"
            assert ours["customer_name"] == "Test Müşteri"
            assert ours["unread_admin"] >= 1, ours
            print(f"[4] admin list sees session: name={ours['customer_name']}, unread_admin={ours['unread_admin']}")

            # 4) Admin pulls message history (marks read)
            r = await http.get(f"/api/chat/admin/sessions/{ours['id']}/messages",
                               headers={"Authorization": f"Bearer {access}"})
            r.raise_for_status()
            data = r.json()
            assert any(m["body"].startswith("Merhaba") for m in data["messages"]), data
            print(f"[5] admin pulled {len(data['messages'])} message(s), unread now={data['session']['unread_admin']}")

            # 5) Admin connects via WS, replies
            async with websockets.connect(f"{WS}/api/ws/chat/admin?token={access}") as admin:
                await admin.send(json.dumps({"action": "send", "session_id": SID, "body": "Merhaba! Yardımcı olabilirim."}))

                # Customer should receive admin reply
                ev4 = json.loads(await cust.recv()); assert ev4["event"] == "chat_message", ev4
                assert ev4["data"]["sender"] == "admin", ev4
                assert "Yardımcı" in ev4["data"]["body"], ev4
                print("[6] customer received admin reply")

                # Admin should also receive its own message + session update
                got_msg = False
                for _ in range(3):
                    f = json.loads(await asyncio.wait_for(admin.recv(), timeout=2.0))
                    if f["event"] == "chat_message" and f["data"]["sender"] == "admin":
                        got_msg = True; break
                assert got_msg, "admin did not receive own message frame"
                print("[7] admin received echo of its own send")

                # 6) Admin closes session via REST
                r = await http.post(f"/api/chat/admin/sessions/{ours['id']}/close",
                                    headers={"Authorization": f"Bearer {access}"})
                r.raise_for_status()
                # Customer should see status change
                got_close = False
                for _ in range(3):
                    f = json.loads(await asyncio.wait_for(cust.recv(), timeout=2.0))
                    if f["event"] == "chat_session" and f["data"]["status"] == "closed":
                        got_close = True; break
                assert got_close, "customer did not see close event"
                print("[8] close event reached customer")

    print("\nALL PASS ✅")


if __name__ == "__main__":
    asyncio.run(main())
