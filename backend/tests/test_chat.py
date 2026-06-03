"""Canlı destek — REST endpoint'leri (WebSocket testleri integration testlerinde yapılır)."""


async def test_list_sessions_requires_auth(client, auth_client):
    r1 = await client.get("/api/chat/admin/sessions")
    assert r1.status_code == 401
    r2 = await auth_client.get("/api/chat/admin/sessions")
    assert r2.status_code == 200
    assert isinstance(r2.json(), list)


async def test_admin_session_messages_404(auth_client):
    r = await auth_client.get("/api/chat/admin/sessions/99999/messages")
    assert r.status_code == 404


async def test_admin_session_messages_empty(auth_client, db_session):
    from app.models import ChatSession, ChatSessionStatus

    s = ChatSession(
        session_id="sess_test_1",
        status=ChatSessionStatus.OPEN.value,
        customer_name="Test",
        customer_email="t@t.com",
    )
    db_session.add(s)
    await db_session.commit()
    await db_session.refresh(s)

    r = await auth_client.get(f"/api/chat/admin/sessions/{s.id}/messages")
    assert r.status_code == 200
    body = r.json()
    assert body["session"]["session_id"] == "sess_test_1"
    assert body["messages"] == []


async def test_admin_session_messages_with_msgs(auth_client, db_session):
    from app.models import ChatMessage, ChatSession, ChatSessionStatus

    s = ChatSession(
        session_id="sess_msgs",
        status=ChatSessionStatus.OPEN.value,
        customer_name="Ali",
        customer_email="a@a.com",
        unread_admin=2,
    )
    db_session.add(s)
    await db_session.flush()
    for i in range(3):
        db_session.add(
            ChatMessage(session_pk=s.id, sender="customer", sender_name="Ali", body=f"mesaj {i}")
        )
    await db_session.commit()
    await db_session.refresh(s)

    r = await auth_client.get(f"/api/chat/admin/sessions/{s.id}/messages")
    assert r.status_code == 200
    body = r.json()
    assert len(body["messages"]) == 3
    # unread_admin sıfırlanmış olmalı
    assert body["session"]["unread_admin"] == 0


async def test_admin_close_session(auth_client, db_session):
    from app.models import ChatSession, ChatSessionStatus

    s = ChatSession(
        session_id="sess_close",
        status=ChatSessionStatus.OPEN.value,
        customer_name="X",
        customer_email="x@x.com",
    )
    db_session.add(s)
    await db_session.commit()
    await db_session.refresh(s)

    r = await auth_client.post(f"/api/chat/admin/sessions/{s.id}/close")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Status güncellendi mi?
    r2 = await auth_client.get("/api/chat/admin/sessions")
    target = next(x for x in r2.json() if x["id"] == s.id)
    assert target["status"] == ChatSessionStatus.CLOSED.value


async def test_admin_close_404(auth_client):
    r = await auth_client.post("/api/chat/admin/sessions/99999/close")
    assert r.status_code == 404


async def test_admin_delete_message(auth_client, db_session):
    from app.models import ChatMessage, ChatSession, ChatSessionStatus

    s = ChatSession(
        session_id="sess_del_msg",
        status=ChatSessionStatus.OPEN.value,
        customer_name="Y",
        customer_email="y@y.com",
    )
    db_session.add(s)
    await db_session.flush()
    m = ChatMessage(session_pk=s.id, sender="customer", sender_name="Y", body="silinecek")
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)

    r = await auth_client.delete(f"/api/chat/admin/messages/{m.id}")
    assert r.status_code == 204


async def test_admin_delete_message_404(auth_client):
    r = await auth_client.delete("/api/chat/admin/messages/99999")
    assert r.status_code == 404


async def test_admin_delete_session_cascades_messages(auth_client, db_session):
    from sqlalchemy import func, select

    from app.models import ChatMessage, ChatSession, ChatSessionStatus

    s = ChatSession(
        session_id="sess_del_all",
        status=ChatSessionStatus.OPEN.value,
        customer_name="Z",
        customer_email="z@z.com",
    )
    db_session.add(s)
    await db_session.flush()
    db_session.add(ChatMessage(session_pk=s.id, sender="customer", sender_name="Z", body="a"))
    db_session.add(ChatMessage(session_pk=s.id, sender="admin", sender_name="Admin", body="b"))
    await db_session.commit()
    await db_session.refresh(s)

    r = await auth_client.delete(f"/api/chat/admin/sessions/{s.id}")
    assert r.status_code == 204

    # Mesajlar da silinmiş olmalı
    n = await db_session.execute(
        select(func.count()).select_from(ChatMessage).where(ChatMessage.session_pk == s.id)
    )
    assert n.scalar() == 0


async def test_admin_delete_session_404(auth_client):
    r = await auth_client.delete("/api/chat/admin/sessions/99999")
    assert r.status_code == 404
