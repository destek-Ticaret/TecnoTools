"""Yeni algoritma servisleri için birim ve entegrasyon testleri."""
import math

from app.services import forecasting, loyalty, shipping
from app.services.cache import TTLCache, ttl_cache
from app.services.text_utils import fuzzy_score, jaccard, levenshtein, normalize, slugify, tokenize


# ── text_utils ─────────────────────────────────────────────────────────────
def test_normalize_strips_tr_diacritics():
    assert normalize("Şarj İstasyonu") == "sarj istasyonu"
    assert normalize("Çağrı") == "cagri"
    assert normalize("ÜRÜN") == "urun"


def test_slugify_handles_punctuation_and_spaces():
    assert slugify("Şarj İstasyonu 3.0!") == "sarj-istasyonu-3-0"
    assert slugify("  Çift   boşluk  ") == "cift-bosluk"
    assert slugify("---") == ""


def test_tokenize_min_length():
    assert tokenize("iPhone 15 Pro Max") == ["iphone", "15", "pro", "max"]
    # 1 karakterli tokenler atılır
    assert tokenize("a b cd") == ["cd"]


def test_levenshtein_basic():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("aaa", "aaa") == 0
    assert levenshtein("", "abc") == 3


def test_fuzzy_score_orders():
    assert fuzzy_score("iphone", "iphone") == 1.0
    assert fuzzy_score("iphone", "iPhone 15 Pro") > 0.85
    assert fuzzy_score("iphon", "iPhone") > 0.5
    # tamamen alakasız → düşük
    assert fuzzy_score("zzzqqq", "Şarj İstasyonu") < 0.3


def test_jaccard_symmetric():
    a = jaccard("samsung", "samsng")
    b = jaccard("samsng", "samsung")
    assert math.isclose(a, b)


# ── cache ──────────────────────────────────────────────────────────────────
def test_ttl_cache_lru_eviction():
    c = TTLCache(ttl_seconds=10, max_size=2)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)  # "a" düşmeli
    assert c.get("a") is None
    assert c.get("b") == 2
    assert c.get("c") == 3


def test_ttl_cache_ttl_expiry():
    c = TTLCache(ttl_seconds=0.05, max_size=4)
    c.set("k", "v")
    assert c.get("k") == "v"
    import time
    time.sleep(0.1)
    assert c.get("k") is None


def test_ttl_cache_decorator_sync():
    calls = {"n": 0}

    @ttl_cache(ttl_seconds=5, max_size=4)
    def expensive(x: int) -> int:
        calls["n"] += 1
        return x * 2

    assert expensive(3) == 6
    assert expensive(3) == 6  # cache hit
    assert calls["n"] == 1
    assert expensive(4) == 8
    assert calls["n"] == 2


# ── shipping ───────────────────────────────────────────────────────────────
def test_shipping_zone_lookup():
    assert shipping.zone_for_city("İstanbul") == "MARMARA"
    assert shipping.zone_for_city("izmir") == "EGE"
    assert shipping.zone_for_city("Diyarbakır") == "GUNEYDOGU_ANADOLU"
    # Bilinmeyen şehir → fallback
    assert shipping.zone_for_city("Atlantis") == "IC_ANADOLU"


def test_shipping_free_above_threshold():
    r = shipping.calc_shipping(city="Ankara", subtotal=1500, free_threshold=750)
    assert r["is_free"] is True
    assert r["fee"] == 0.0
    assert r["remaining_for_free"] == 0.0


def test_shipping_paid_below_threshold():
    r = shipping.calc_shipping(city="Van", subtotal=200, free_threshold=750)
    assert r["is_free"] is False
    assert r["fee"] > 0
    assert r["remaining_for_free"] == 550.0
    assert r["zone"] == "DOGU_ANADOLU"


# ── loyalty ────────────────────────────────────────────────────────────────
def test_loyalty_points_calc():
    assert loyalty.points_for_order_total(subtotal=100, discount=10) == 90
    assert loyalty.points_for_order_total(subtotal=50, discount=100) == 0


def test_loyalty_redeem_caps_at_subtotal_pct():
    # 1000 puan = 100₺ ham karşılık, sepet 500₺ → max %20 = 100₺ → 100 düş
    val = loyalty.redeem_points_to_discount(1000, subtotal=500)
    assert val == 100.0
    # 5000 puan = 500₺, ama sepet 100₺ → en fazla 20₺
    capped = loyalty.redeem_points_to_discount(5000, subtotal=100)
    assert capped == 20.0


def test_loyalty_tier_thresholds():
    assert loyalty._tier_for(0)[0] == "Bronze"
    assert loyalty._tier_for(999)[0] == "Bronze"
    assert loyalty._tier_for(1500)[0] == "Silver"
    assert loyalty._tier_for(7000)[0] == "Gold"
    assert loyalty._tier_for(20000)[0] == "Platinum"


# ── forecasting ────────────────────────────────────────────────────────────
def test_linear_forecast_positive_and_length():
    f = forecasting.linear_forecast([1, 2, 3, 4, 5], horizon_days=3)
    assert len(f) == 3
    assert all(x >= 0 for x in f)
    # Yukarı eğilimli seri → tahmin > son gerçek
    assert f[-1] >= 5


def test_linear_forecast_with_zero_variance():
    f = forecasting.linear_forecast([5, 5, 5, 5], horizon_days=4)
    assert all(abs(v - 5) < 1e-6 for v in f)


def test_pareto_summary_classes():
    rows = [
        {"product_id": 1, "name": "A", "revenue": 800, "qty": 1, "class": "A", "cum_share": 0.8, "rank": 1, "revenue_pct": 80},
        {"product_id": 2, "name": "B", "revenue": 150, "qty": 1, "class": "B", "cum_share": 0.95, "rank": 2, "revenue_pct": 15},
        {"product_id": 3, "name": "C", "revenue": 50, "qty": 1, "class": "C", "cum_share": 1.0, "rank": 3, "revenue_pct": 5},
    ]
    summary = forecasting.pareto_summary(rows)
    assert summary["A"]["count"] == 1
    assert summary["A"]["revenue"] == 800
    assert summary["B"]["count"] == 1
    assert summary["C"]["count"] == 1
    assert abs(summary["A"]["share"] - 80) < 0.1


# ── recommendation HTTP ───────────────────────────────────────────────────
async def test_trending_endpoint_empty_ok(auth_client):
    """Hiç satış olmadığında trending boş liste döner (500 değil)."""
    r = await auth_client.get("/api/algorithms/trending?limit=5")
    assert r.status_code == 200
    assert r.json() == []


async def test_shipping_quote_endpoint(auth_client):
    r = await auth_client.post(
        "/api/algorithms/shipping/quote",
        json={"city": "İstanbul", "subtotal": 200, "item_count": 1},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["zone"] == "MARMARA"
    assert body["is_free"] is False
    assert body["fee"] > 0


async def test_loyalty_status_unknown_email(auth_client):
    r = await auth_client.get("/api/algorithms/loyalty/nobody@example.com")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "Bronze"
    assert body["points"] == 0


async def test_fuzzy_search_finds_turkish_match(auth_client):
    # Aktif bir ürün oluştur
    pr = await auth_client.post(
        "/api/products",
        json={"name": "Şarj İstasyonu Pro", "price": 1500, "stock": 5},
    )
    assert pr.status_code == 201
    # Diakritiksiz / küçük harfli arama
    s = await auth_client.get("/api/products/search/fuzzy?q=sarj+istasyon")
    assert s.status_code == 200
    rows = s.json()
    assert any("Şarj" in r["name"] for r in rows)
    assert rows[0]["slug"].startswith("sarj-istasyonu")


async def test_autocomplete_prefix(auth_client):
    await auth_client.post(
        "/api/products", json={"name": "iPhone 15 Pro", "price": 50000, "stock": 3},
    )
    s = await auth_client.get("/api/products/search/autocomplete?q=iph")
    assert s.status_code == 200
    names = [r["name"] for r in s.json()]
    assert any("iPhone" in n for n in names)


async def test_seo_meta_product(auth_client):
    pr = await auth_client.post(
        "/api/products", json={"name": "Test Ürün", "price": 99, "stock": 1, "description": "Açıklama"},
    )
    pid = pr.json()["id"]
    r = await auth_client.get(f"/api/seo/meta/product/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["title"].startswith("Test Ürün")
    assert body["canonical"].endswith("/test-urun")
    assert body["json_ld"]["@type"] == "Product"
    assert body["json_ld"]["offers"]["price"] == 99
