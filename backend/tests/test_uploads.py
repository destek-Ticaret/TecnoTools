"""Upload endpoint'leri — local storage + MIME/boyut kontrolü."""

import io


def _make_png_bytes() -> bytes:
    """1×1 kırmızı PNG (gerçek header'lı, MIME sniff testini geçer)."""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff\x9f\x01\x00\x05"
        b"\xfe\x02\xfe\xa3z\x8bU"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


async def test_upload_image_ok(auth_client):
    """Editör yetkisiyle geçerli bir PNG yükler, /api/uploads/files/ URL'i döner."""
    files = {"file": ("test.png", io.BytesIO(_make_png_bytes()), "image/png")}
    r = await auth_client.post("/api/uploads/images", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"].startswith("/api/uploads/files/")
    assert body["size"] > 0


async def test_upload_rejects_disallowed_mime(client):
    """Yetkisiz çağrı + yanlış MIME → 401 (auth_client kullanmadık)."""
    files = {"file": ("evil.exe", io.BytesIO(b"MZ" + b"\x00" * 100), "application/octet-stream")}
    r = await client.post("/api/uploads/images", files=files)
    # Auth header yok → 401
    assert r.status_code == 401


async def test_upload_rejects_disallowed_mime_authed(auth_client):
    """Editör olsa bile izin verilmeyen MIME → 400."""
    files = {"file": ("evil.pdf", io.BytesIO(b"%PDF-1.4\n%fake"), "application/pdf")}
    r = await auth_client.post("/api/uploads/images", files=files)
    assert r.status_code == 400
    assert "Desteklenmeyen" in r.text or "dosya" in r.text.lower()


async def test_upload_rejects_fake_image_content(auth_client):
    """image/png olarak beyan edilse de içerik gerçek resim değilse → 400.

    Declared Content-Type spoof'lanabilir; magic-byte doğrulaması bunu yakalar."""
    files = {"file": ("fake.png", io.BytesIO(b"<html><script>alert(1)</script>"), "image/png")}
    r = await auth_client.post("/api/uploads/images", files=files)
    assert r.status_code == 400
    assert "resim" in r.text.lower()


async def test_upload_rejects_oversize(auth_client):
    """5 MB'dan büyük dosya → 413."""
    big = b"\x00" * (5 * 1024 * 1024 + 1024)  # 5 MB + 1 KB
    files = {"file": ("big.png", io.BytesIO(big), "image/png")}
    r = await auth_client.post("/api/uploads/images", files=files)
    assert r.status_code == 413
    assert "sınır" in r.text.lower() or "413" in str(r.status_code)


async def test_serve_uploaded_file_ok(auth_client):
    """Yüklenen dosya /api/uploads/files/{name} üzerinden geri sunulur."""
    data = _make_png_bytes()
    files = {"file": ("hello.png", io.BytesIO(data), "image/png")}
    up = await auth_client.post("/api/uploads/images", files=files)
    assert up.status_code == 200
    url = up.json()["url"]
    name = url.rsplit("/", 1)[-1]

    r = await auth_client.get(f"/api/uploads/files/{name}")
    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


async def test_serve_blocks_path_traversal(auth_client):
    """Path traversal denemeleri 400 döner."""
    for bad in ["..%2F..%2Fetc%2Fpasswd", "..\\windows", "../etc/passwd", "a/b.png"]:
        r = await auth_client.get(f"/api/uploads/files/{bad}")
        assert r.status_code in (400, 404), f"Beklenmeyen durum {bad}: {r.status_code}"


async def test_serve_404_for_missing(auth_client):
    r = await auth_client.get("/api/uploads/files/totally-nonexistent.png")
    assert r.status_code == 404


async def test_upload_idempotent_same_content(auth_client):
    """Aynı içerik aynı dosyaya yazılır (SHA-256 dedup)."""
    data = _make_png_bytes()
    r1 = await auth_client.post(
        "/api/uploads/images", files={"file": ("a.png", io.BytesIO(data), "image/png")}
    )
    r2 = await auth_client.post(
        "/api/uploads/images", files={"file": ("b.png", io.BytesIO(data), "image/png")}
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["url"] == r2.json()["url"]
