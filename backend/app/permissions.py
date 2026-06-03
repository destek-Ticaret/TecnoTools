"""Granüler yetki (RBAC) kataloğu ve çözümleme.

Her admin kullanıcısının etkin izinleri şöyle hesaplanır:
  1) Rolün varsayılan izin seti (ROLE_DEFAULTS) taban alınır.
  2) Kullanıcının `permissions` JSON override'ı varsa tek tek uygulanır
     ({"products.delete": false} gibi).
  3) ADMIN rolü daima tüm izinlere sahiptir (override edilemez).

UI bu katalogu `GET /api/admin-users/permissions/catalog` ile çekip
kullanıcı düzenleme ekranında onay kutusu matrisi olarak gösterir.
"""
from __future__ import annotations

from app.models import User, UserRole

# Yetki kataloğu — gruplanmış (UI'da başlık başına gösterilir).
# key: "resource.action", label: Türkçe açıklama.
PERMISSION_CATALOG: dict[str, list[dict]] = {
    "Siparişler": [
        {"key": "orders.view", "label": "Siparişleri görüntüle"},
        {"key": "orders.edit", "label": "Sipariş durumu/kargo güncelle"},
        {"key": "orders.delete", "label": "Sipariş sil"},
    ],
    "Ürünler": [
        {"key": "products.view", "label": "Ürünleri görüntüle"},
        {"key": "products.edit", "label": "Ürün ekle/düzenle"},
        {"key": "products.delete", "label": "Ürün sil"},
        {"key": "products.import", "label": "Toplu içe aktar"},
        {"key": "pricing.manage", "label": "Fiyat kuralları yönet"},
    ],
    "İçerik": [
        {"key": "content.banners", "label": "Banner / vitrin yönet"},
        {"key": "content.homepage", "label": "Ana sayfa düzeni"},
        {"key": "content.blog", "label": "Blog yazıları"},
        {"key": "content.pages", "label": "Statik sayfalar"},
    ],
    "Müşteri & Pazarlama": [
        {"key": "customers.view", "label": "Müşterileri görüntüle"},
        {"key": "coupons.manage", "label": "Kupon yönet"},
        {"key": "campaigns.manage", "label": "Kampanya / e-posta"},
        {"key": "returns.manage", "label": "İade yönet"},
        {"key": "reviews.moderate", "label": "Yorum / soru moderasyon"},
        {"key": "chat.handle", "label": "Canlı destek"},
    ],
    "Sistem": [
        {"key": "reports.view", "label": "Raporlar & analitik"},
        {"key": "settings.manage", "label": "Site ayarları"},
        {"key": "users.manage", "label": "Admin kullanıcı yönet"},
        {"key": "audit.view", "label": "Aktivite geçmişi"},
    ],
}

# Düz key listesi
ALL_PERMISSIONS: list[str] = [p["key"] for group in PERMISSION_CATALOG.values() for p in group]

# Rol varsayılanları
_EDITOR_PERMS = {
    "orders.view", "orders.edit",
    "products.view", "products.edit", "products.import", "pricing.manage",
    "content.banners", "content.homepage", "content.blog", "content.pages",
    "customers.view", "coupons.manage", "campaigns.manage", "returns.manage",
    "reviews.moderate", "chat.handle", "reports.view",
}
_VIEWER_PERMS = {
    "orders.view", "products.view", "customers.view", "reports.view", "audit.view",
}

ROLE_DEFAULTS: dict[str, set[str]] = {
    UserRole.ADMIN.value: set(ALL_PERMISSIONS),
    UserRole.EDITOR.value: _EDITOR_PERMS,
    UserRole.VIEWER.value: _VIEWER_PERMS,
}


def effective_permissions(user: User) -> set[str]:
    """Kullanıcının etkin izin setini döndür (rol varsayılanı + override)."""
    if user.role == UserRole.ADMIN.value:
        return set(ALL_PERMISSIONS)
    perms = set(ROLE_DEFAULTS.get(user.role, set()))
    override = user.permissions or {}
    if isinstance(override, dict):
        for key, allowed in override.items():
            if key not in ALL_PERMISSIONS:
                continue
            if allowed:
                perms.add(key)
            else:
                perms.discard(key)
    return perms


def has_permission(user: User, permission: str) -> bool:
    return permission in effective_permissions(user)
