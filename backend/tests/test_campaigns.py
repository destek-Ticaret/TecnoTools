"""Newsletter kampanya yönetimi — admin only, draft → sending → completed."""


async def test_list_campaigns_requires_admin(client, auth_client):
    r1 = await client.get("/api/newsletter/campaigns")
    assert r1.status_code == 401
    r2 = await auth_client.get("/api/newsletter/campaigns")
    assert r2.status_code == 200
    assert isinstance(r2.json(), list)


async def test_create_campaign_ok(auth_client):
    r = await auth_client.post("/api/newsletter/campaigns", json={
        "subject": "Bahar Kampanyası",
        "html_body": "<h1>Bahar indirimi %20</h1><p>Tüm ürünlerde geçerlidir.</p>",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["subject"] == "Bahar Kampanyası"
    assert body["status"] == "draft"
    assert body["total_recipients"] == 0
    assert body["sent_count"] == 0


async def test_create_campaign_validation_errors(auth_client):
    """subject 3 karakterden kısa olamaz, html_body 10 karakterden kısa olamaz."""
    r1 = await auth_client.post("/api/newsletter/campaigns", json={"subject": "ab", "html_body": "<p>x</p>"})
    assert r1.status_code == 422
    r2 = await auth_client.post("/api/newsletter/campaigns", json={"subject": "abc", "html_body": "kısa"})
    assert r2.status_code == 422


async def test_get_campaign_ok(auth_client):
    cr = await auth_client.post("/api/newsletter/campaigns", json={
        "subject": "Test", "html_body": "<p>İçerik burada.</p>"
    })
    cid = cr.json()["id"]
    r = await auth_client.get(f"/api/newsletter/campaigns/{cid}")
    assert r.status_code == 200
    assert r.json()["id"] == cid


async def test_get_campaign_404(auth_client):
    r = await auth_client.get("/api/newsletter/campaigns/99999")
    assert r.status_code == 404


async def test_send_campaign_idempotent_state_machine(auth_client, db_session):
    """draft → sending → completed (tekrar gönderilemez)."""
    from app.models import NewsletterCampaign, NewsletterSubscriber

    # 3 abone ekle (background gönderimde sayılır)
    for i in range(3):
        db_session.add(NewsletterSubscriber(email=f"sub{i}@x.com"))
    await db_session.commit()

    cr = await auth_client.post("/api/newsletter/campaigns", json={
        "subject": "Kampanya", "html_body": "<p>İçerik yeterince uzun.</p>"
    })
    cid = cr.json()["id"]

    # Gönderimi tetikle → sending durumuna geçer
    snd = await auth_client.post(f"/api/newsletter/campaigns/{cid}/send")
    assert snd.status_code == 200
    assert snd.json()["status"] == "sending"

    # sending durumundayken tekrar gönderilemez
    snd2 = await auth_client.post(f"/api/newsletter/campaigns/{cid}/send")
    assert snd2.status_code == 409


async def test_send_nonexistent_404(auth_client):
    r = await auth_client.post("/api/newsletter/campaigns/99999/send")
    assert r.status_code == 404


async def test_delete_campaign_draft(auth_client):
    cr = await auth_client.post("/api/newsletter/campaigns", json={
        "subject": "Silinecek", "html_body": "<p>İçerik uzunluğu yeterli.</p>"
    })
    cid = cr.json()["id"]
    r = await auth_client.delete(f"/api/newsletter/campaigns/{cid}")
    assert r.status_code == 204

    # Silinmiş mi?
    r2 = await auth_client.get(f"/api/newsletter/campaigns/{cid}")
    assert r2.status_code == 404


async def test_delete_404(auth_client):
    r = await auth_client.delete("/api/newsletter/campaigns/99999")
    assert r.status_code == 404
