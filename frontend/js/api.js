/* TecnoTools API Client
   ─────────────────────────────────────────────────────────────
   Backend (FastAPI) ile iletişim için tek nokta. JWT access + refresh
   token'ı localStorage'da tutar; 401 alınca otomatik refresh dener,
   tekrar 401 ise oturumu kapatır.

   Kullanım:
     await api.products.listPublic()
     await api.orders.checkout({...})
     api.auth.isLoggedIn()
   ─────────────────────────────────────────────────────────────*/
(function (global) {
  'use strict';

  const DEFAULT_BASE = 'https://api.tecnotools.org';
  const ACCESS_KEY = 'tt_auth_access';
  const REFRESH_KEY = 'tt_auth_refresh';
  const CUSTOMER_ACCESS_KEY = 'tt_customer_access';
  const CUSTOMER_REFRESH_KEY = 'tt_customer_refresh';
  const SESSION_ID_KEY = 'tt_session_id';

  // Origin'e göre backend URL'sini tahmin et — geliştirme rahatlığı için.
  function resolveBaseUrl() {
    if (global.TT_API_BASE) return String(global.TT_API_BASE).replace(/\/$/, '');
    const meta = document.querySelector('meta[name="tt-api-base"]');
    const metaVal = meta && meta.content ? meta.content.trim() : '';
    if (metaVal) return metaVal.replace(/\/$/, '');
    // Sadece localhost/LAN'da :8000 portunu kullan
    if (typeof location !== 'undefined' && /^https?:$/.test(location.protocol)) {
      const h = location.hostname;
      if (h === 'localhost' || h === '127.0.0.1' || /^10\.|^192\.168\.|^172\.(1[6-9]|2\d|3[01])\./.test(h)) {
        return `${location.protocol}//${h}:8000`;
      }
    }
    return DEFAULT_BASE;
  }

  const BASE_URL = resolveBaseUrl();

  // ── Token storage (admin) ──
  function getAccess() { return localStorage.getItem(ACCESS_KEY) || ''; }
  function getRefresh() { return localStorage.getItem(REFRESH_KEY) || ''; }
  function setTokens(access, refresh) {
    if (access) localStorage.setItem(ACCESS_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  }
  function clearTokens() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  }
  // ── Token storage (müşteri üyelik) — admin token'larından bağımsız ──
  function getCustomerAccess() { return localStorage.getItem(CUSTOMER_ACCESS_KEY) || ''; }
  function getCustomerRefresh() { return localStorage.getItem(CUSTOMER_REFRESH_KEY) || ''; }
  function setCustomerTokens(access, refresh) {
    if (access) localStorage.setItem(CUSTOMER_ACCESS_KEY, access);
    if (refresh) localStorage.setItem(CUSTOMER_REFRESH_KEY, refresh);
  }
  function clearCustomerTokens() {
    localStorage.removeItem(CUSTOMER_ACCESS_KEY);
    localStorage.removeItem(CUSTOMER_REFRESH_KEY);
  }

  // ── Çerez izni okuma (granüler) ──
  // Yeni format: localStorage['tt_consent_v2'] = JSON.stringify({essential, preference, analytics, marketing})
  // Eski format ('all' | 'essential' düz string) ile geriye uyum.
  const CONSENT_KEY = 'tt_consent_v2';
  const LEGACY_CONSENT_KEY = 'tt_consent';
  function readConsentCats() {
    try {
      const raw = localStorage.getItem(CONSENT_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        return {
          essential: true,
          preference: !!parsed.preference,
          analytics: !!parsed.analytics,
          marketing: !!parsed.marketing,
        };
      }
    } catch (_) {}
    const legacy = localStorage.getItem(LEGACY_CONSENT_KEY);
    if (legacy === 'all') return { essential: true, preference: true, analytics: true, marketing: true };
    if (legacy === 'essential') return { essential: true, preference: false, analytics: false, marketing: false };
    // Henüz seçim yapılmadı — analytics kapalı (opt-in)
    return { essential: true, preference: false, analytics: false, marketing: false };
  }

  // ── Session ID (rezervasyonlar için) ──
  function getSessionId() {
    let id = sessionStorage.getItem(SESSION_ID_KEY);
    if (!id) {
      id = 'sess_' + Math.random().toString(36).slice(2) + Date.now().toString(36);
      sessionStorage.setItem(SESSION_ID_KEY, id);
    }
    return id;
  }

  // ── Refresh kuyruğu — eşzamanlı 401 isteklerinde tek refresh ──
  let _refreshInFlight = null;
  async function refreshAccess() {
    if (_refreshInFlight) return _refreshInFlight;
    const refresh = getRefresh();
    if (!refresh) throw new ApiError('Refresh token yok', 401);
    _refreshInFlight = fetch(`${BASE_URL}/api/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
    }).then(async (r) => {
      if (!r.ok) { clearTokens(); throw new ApiError('Refresh başarısız', r.status); }
      const data = await r.json();
      setTokens(data.access_token, data.refresh_token);
      return data;
    }).finally(() => { _refreshInFlight = null; });
    return _refreshInFlight;
  }

  // ── Hata sınıfı ──
  class ApiError extends Error {
    constructor(message, status, body) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.body = body;
    }
  }

  // ── Müşteri refresh (admin'inkinden ayrı) ──
  let _customerRefreshInFlight = null;
  async function refreshCustomerAccess() {
    if (_customerRefreshInFlight) return _customerRefreshInFlight;
    const refresh = getCustomerRefresh();
    if (!refresh) throw new ApiError('Refresh token yok', 401);
    _customerRefreshInFlight = fetch(`${BASE_URL}/api/customer-auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
    }).then(async (r) => {
      if (!r.ok) { clearCustomerTokens(); throw new ApiError('Refresh başarısız', r.status); }
      const data = await r.json();
      setCustomerTokens(data.access_token, data.refresh_token);
      return data;
    }).finally(() => { _customerRefreshInFlight = null; });
    return _customerRefreshInFlight;
  }

  // ── Core fetch wrapper ──
  async function request(path, { method = 'GET', body = null, params = null, auth = false, customerAuth = false, raw = false } = {}) {
    const url = new URL(BASE_URL + path);
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v));
      });
    }
    const headers = { 'Accept': 'application/json' };
    let payload = body;
    if (body && typeof body === 'object' && !(body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }
    const access = getAccess();
    const customerAccess = getCustomerAccess();
    if (auth && access) headers['Authorization'] = `Bearer ${access}`;
    else if (customerAuth && customerAccess) headers['Authorization'] = `Bearer ${customerAccess}`;

    let resp;
    try {
      resp = await fetch(url.toString(), { method, headers, body: payload });
    } catch (e) {
      throw new ApiError('Ağ hatası: backend ulaşılamıyor', 0);
    }

    // 401 ise refresh dene (uygun token türüne göre)
    if (resp.status === 401 && auth && access) {
      try {
        await refreshAccess();
        headers['Authorization'] = `Bearer ${getAccess()}`;
        resp = await fetch(url.toString(), { method, headers, body: payload });
      } catch (_) {
        clearTokens();
        throw new ApiError('Oturum süresi doldu', 401);
      }
    } else if (resp.status === 401 && customerAuth && customerAccess) {
      try {
        await refreshCustomerAccess();
        headers['Authorization'] = `Bearer ${getCustomerAccess()}`;
        resp = await fetch(url.toString(), { method, headers, body: payload });
      } catch (_) {
        clearCustomerTokens();
        throw new ApiError('Oturum süresi doldu', 401);
      }
    }

    if (raw) return resp;
    let data = null;
    const text = await resp.text();
    if (text) {
      try { data = JSON.parse(text); } catch (_) { data = text; }
    }
    if (!resp.ok) {
      const msg = (data && (data.detail || data.message)) || `İstek başarısız (${resp.status})`;
      throw new ApiError(typeof msg === 'string' ? msg : JSON.stringify(msg), resp.status, data);
    }
    return data;
  }

  // ── Public API ──
  const api = {
    baseUrl: BASE_URL,
    ApiError,
    getSessionId,

    // ── Müşteri üyelik ──
    customerAuth: {
      isLoggedIn() { return !!getCustomerAccess(); },
      async register(payload) {
        const data = await request('/api/customer-auth/register', { method: 'POST', body: payload });
        setCustomerTokens(data.access_token, data.refresh_token);
        return data;
      },
      async login(email, password) {
        const data = await request('/api/customer-auth/login', {
          method: 'POST',
          body: { email, password },
        });
        setCustomerTokens(data.access_token, data.refresh_token);
        return data;
      },
      async logout() {
        const refresh = getCustomerRefresh();
        try {
          if (refresh) {
            await request('/api/customer-auth/logout', { method: 'POST', body: { refresh_token: refresh }, raw: true });
          }
        } catch (_) {}
        clearCustomerTokens();
      },
      me() { return request('/api/customer-auth/me', { customerAuth: true }); },
      updateMe(payload) { return request('/api/customer-auth/me', { method: 'PATCH', body: payload, customerAuth: true }); },
      changePassword(currentPassword, newPassword) {
        return request('/api/customer-auth/change-password', {
          method: 'POST', customerAuth: true,
          body: { current_password: currentPassword, new_password: newPassword },
        });
      },
      forgotPassword(email) {
        return request('/api/customer-auth/forgot-password', { method: 'POST', body: { email } });
      },
      resetPassword(token, newPassword) {
        return request('/api/customer-auth/reset-password', { method: 'POST', body: { token, new_password: newPassword } });
      },
      verifyEmail(token) {
        return request('/api/customer-auth/verify-email', { method: 'POST', body: { token } });
      },
      resendVerification() {
        return request('/api/customer-auth/resend-verification', { method: 'POST', customerAuth: true });
      },
      myOrders() { return request('/api/customer-auth/orders', { customerAuth: true }); },
      myOrder(orderNo) { return request(`/api/customer-auth/orders/${orderNo}`, { customerAuth: true }); },
      myOrderTracking(orderNo) { return request(`/api/customer-auth/orders/${orderNo}/tracking`, { customerAuth: true }); },
      myLoyalty() { return request('/api/customer-auth/loyalty', { customerAuth: true }); },
    },

    // ── Favoriler (wishlist) — cihazlar arası senkron, müşteri girişi gerekir ──
    wishlist: {
      list() { return request('/api/wishlist', { customerAuth: true }); },
      add(productId) { return request(`/api/wishlist/${productId}`, { method: 'POST', customerAuth: true }); },
      remove(productId) { return request(`/api/wishlist/${productId}`, { method: 'DELETE', customerAuth: true }); },
      // localStorage favorilerini sunucuya taşır, birleşik listeyi döner
      merge(productIds) { return request('/api/wishlist/merge', { method: 'POST', body: { product_ids: productIds }, customerAuth: true }); },
    },

    auth: {
      isLoggedIn() { return !!getAccess(); },
      async login(username, password, totpCode) {
        const data = await request('/api/auth/login', {
          method: 'POST',
          body: { username, password, totp_code: totpCode || null },
        });
        setTokens(data.access_token, data.refresh_token);
        return data;
      },
      async logout() {
        const refresh = getRefresh();
        try {
          if (refresh) {
            await request('/api/auth/logout', { method: 'POST', body: { refresh_token: refresh }, raw: true });
          }
        } catch (_) {}
        clearTokens();
      },
      async me() { return request('/api/auth/me', { auth: true }); },
      forgotPassword(username) {
        return request('/api/auth/forgot-password', { method: 'POST', body: { username } });
      },
      resetPassword(token, newPassword) {
        return request('/api/auth/reset-password', { method: 'POST', body: { token, new_password: newPassword } });
      },
      twoFAStatus() { return request('/api/auth/2fa/status', { auth: true }); },
      twoFASetup(secret, code) {
        return request('/api/auth/2fa/setup', { method: 'POST', auth: true, body: { secret, code } });
      },
      twoFADisable(code) {
        return request('/api/auth/2fa/disable', { method: 'POST', auth: true, body: { code } });
      },
    },

    products: {
      listPublic(opts = {}) {
        return request('/api/products', {
          params: {
            session_id: getSessionId(),
            category_id: opts.categoryId,
            q: opts.query,
            currency: opts.currency || localStorage.getItem('tt_currency') || undefined,
          },
        });
      },
      getPublic(id, opts = {}) {
        return request(`/api/products/${id}`, {
          params: {
            session_id: getSessionId(),
            currency: opts.currency || localStorage.getItem('tt_currency') || undefined,
          },
        });
      },
      listAdmin() { return request('/api/products/admin/all', { auth: true }); },
      create(payload) { return request('/api/products', { method: 'POST', body: payload, auth: true }); },
      update(id, payload) { return request(`/api/products/${id}`, { method: 'PUT', body: payload, auth: true }); },
      delete(id) { return request(`/api/products/${id}`, { method: 'DELETE', auth: true, raw: true }); },
      bulkPriceUpdate(payload) {
        return request('/api/products/bulk/price', { method: 'POST', body: payload, auth: true });
      },
      reviews(id) { return request(`/api/products/${id}/reviews`); },
      submitReview(id, payload) {
        return request(`/api/products/${id}/reviews`, { method: 'POST', body: payload });
      },
      // Canlı arama önerisi (hızlı prefix/substring, diakritik-bağımsız)
      searchAutocomplete(q, limit = 8) {
        return request('/api/products/search/autocomplete', { params: { q, limit } });
      },
      // Yazım hatası toleranslı fuzzy arama (skorlu)
      searchFuzzy(q, { limit = 20, category_id } = {}) {
        return request('/api/products/search/fuzzy', { params: { q, limit, category_id } });
      },
      // Soru-cevap (Q&A) — public
      questions(id) { return request(`/api/products/${id}/questions`); },
      askQuestion(id, payload) {
        return request(`/api/products/${id}/questions`, { method: 'POST', body: payload });
      },
      notifyRestock(id, email) {
        return request(`/api/products/${id}/notify-restock`, { method: 'POST', body: { email } });
      },
    },

    categories: {
      list() { return request('/api/categories'); },
      create(payload) { return request('/api/categories', { method: 'POST', body: payload, auth: true }); },
      update(id, payload) { return request(`/api/categories/${id}`, { method: 'PUT', body: payload, auth: true }); },
      delete(id) { return request(`/api/categories/${id}`, { method: 'DELETE', auth: true, raw: true }); },
    },

    orders: {
      checkout(payload) {
        return request('/api/orders/checkout', {
          method: 'POST',
          body: { ...payload, session_id: getSessionId() },
          // Puan kullanılıyorsa müşteri token'ı şart (backend doğrular)
          customerAuth: !!(payload && payload.use_loyalty_points),
        });
      },
      /** Public sipariş takibi — order_no + email ile, üyelik gerekmez. */
      track(orderNo, email) {
        return request('/api/orders/track', { params: { order_no: orderNo, email } });
      },
      list() { return request('/api/orders', { auth: true }); },
      get(orderNo) { return request(`/api/orders/${orderNo}`, { auth: true }); },
      updateStatus(orderNo, status) {
        return request(`/api/orders/${orderNo}/status`, { method: 'PATCH', body: { status }, auth: true });
      },
      patch(orderNo, payload) {
        return request(`/api/orders/${orderNo}`, { method: 'PATCH', body: payload, auth: true });
      },
      delete(orderNo) { return request(`/api/orders/${orderNo}`, { method: 'DELETE', auth: true, raw: true }); },
      addNote(orderNo, text) {
        return request(`/api/orders/${orderNo}/notes`, { method: 'POST', body: { text }, auth: true });
      },
      deleteNote(orderNo, idx) {
        return request(`/api/orders/${orderNo}/notes/${idx}`, { method: 'DELETE', auth: true });
      },
      signedBarcode(orderNo) {
        return request(`/api/orders/${orderNo}/signed-barcode`, { auth: true });
      },
      verifyBarcode(token) {
        return request('/api/orders/verify-barcode', { params: { t: token } });
      },
    },

    payments: {
      getOrderStatus(orderNo) { return request(`/api/payments/order-status/${orderNo}`); },
      /** Kart BIN (ilk 6-8 hane) + tutara göre taksit seçenekleri. */
      installments(bin, price) { return request('/api/payments/installments', { params: { bin, price } }); },
    },

    shipping: {
      /** Public — order_no'ya bağlı kargo event listesi (PII içermez). */
      track(orderNo) { return request(`/api/shipping/track/${encodeURIComponent(orderNo)}`); },
      /** Admin — sipariş için carrier + tracking_no ata. */
      assign(orderNo, payload) {
        return request(`/api/shipping/assign/${encodeURIComponent(orderNo)}`, {
          method: 'POST', auth: true, body: payload,
        });
      },
      /** Admin — adapter.fetch() ile canlı sync. */
      sync(orderNo) {
        return request(`/api/shipping/sync/${encodeURIComponent(orderNo)}`, {
          method: 'POST', auth: true,
        });
      },
    },

    coupons: {
      validate(code) { return request(`/api/coupons/validate/${encodeURIComponent(code)}`); },
      list() { return request('/api/coupons', { auth: true }); },
      create(payload) { return request('/api/coupons', { method: 'POST', body: payload, auth: true }); },
      update(id, payload) { return request(`/api/coupons/${id}`, { method: 'PUT', body: payload, auth: true }); },
      delete(id) { return request(`/api/coupons/${id}`, { method: 'DELETE', auth: true, raw: true }); },
    },

    returns: {
      create(payload) { return request('/api/returns', { method: 'POST', body: payload }); },
      lookup(orderNo, email) {
        return request('/api/returns/lookup', { params: { order_no: orderNo, email } });
      },
      cancelMine(id, email) {
        return request(`/api/returns/${id}/cancel?email=${encodeURIComponent(email)}`, { method: 'POST' });
      },
      list(opts = {}) {
        return request('/api/returns', { auth: true, params: { status: opts.status } });
      },
      get(id) { return request(`/api/returns/${id}`, { auth: true }); },
      updateStatus(id, statusValue, adminNote) {
        return request(`/api/returns/${id}/status`, {
          method: 'PATCH', auth: true,
          body: { status: statusValue, admin_note: adminNote || null },
        });
      },
    },

    reservations: {
      sync(items) {
        return request('/api/reservations/sync', {
          method: 'POST',
          body: { session_id: getSessionId(), items },
        });
      },
      release() {
        return request('/api/reservations/release', {
          method: 'POST',
          body: { session_id: getSessionId() },
        });
      },
    },

    newsletter: {
      subscribe(email) { return request('/api/newsletter', { method: 'POST', body: { email } }); },
      listSubscribers() { return request('/api/newsletter/subscribers', { auth: true }); },
      deleteSubscriber(id) { return request(`/api/newsletter/subscribers/${id}`, { method: 'DELETE', auth: true, raw: true }); },
      campaigns: {
        list() { return request('/api/newsletter/campaigns', { auth: true }); },
        get(id) { return request(`/api/newsletter/campaigns/${id}`, { auth: true }); },
        create(payload) { return request('/api/newsletter/campaigns', { method: 'POST', body: payload, auth: true }); },
        send(id) { return request(`/api/newsletter/campaigns/${id}/send`, { method: 'POST', auth: true }); },
        delete(id) { return request(`/api/newsletter/campaigns/${id}`, { method: 'DELETE', auth: true, raw: true }); },
      },
    },

    audit: {
      list() { return request('/api/audit', { auth: true }); },
      clear() { return request('/api/audit', { method: 'DELETE', auth: true, raw: true }); },
    },

    reviews: {
      listAdmin(approved) {
        return request('/api/admin/reviews', { auth: true, params: { approved } });
      },
      approve(id, isApproved) {
        return request(`/api/admin/reviews/${id}`, {
          method: 'PATCH', auth: true, body: { is_approved: isApproved },
        });
      },
      delete(id) { return request(`/api/admin/reviews/${id}`, { method: 'DELETE', auth: true, raw: true }); },
    },

    // Ürün soru-cevap yönetimi (admin)
    questions: {
      listAdmin({ published, answered } = {}) {
        return request('/api/admin/questions', { auth: true, params: { published, answered } });
      },
      update(id, payload) {
        return request(`/api/admin/questions/${id}`, { method: 'PATCH', auth: true, body: payload });
      },
      delete(id) { return request(`/api/admin/questions/${id}`, { method: 'DELETE', auth: true, raw: true }); },
    },

    exports: {
      ordersUrl() { return `${BASE_URL}/api/exports/orders.xlsx`; },
      productsUrl() { return `${BASE_URL}/api/exports/products.xlsx`; },
      customersUrl() { return `${BASE_URL}/api/exports/customers.xlsx`; },
      returnsUrl() { return `${BASE_URL}/api/exports/returns.xlsx`; },
    },

    settings: {
      list() { return request('/api/settings'); },
      update(payload) { return request('/api/settings', { method: 'PUT', body: payload, auth: true }); },
    },

    currency: {
      list() { return request('/api/currency'); },
      rate(base, quote) { return request(`/api/currency/rate?base=${base}&quote=${quote}`); },
    },

    analytics: {
      track(event, meta) {
        // KVKK çerez izni: analytics kategorisi kapalıysa track gönderme.
        // Geriye uyum: eski 'tt_consent' = 'essential' string formatı da reddedilir.
        const cats = readConsentCats();
        if (!cats.analytics) return Promise.resolve();
        return fetch(`${BASE_URL}/api/analytics/track`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            event,
            path: location.pathname + location.hash,
            referrer: document.referrer || null,
            session_id: getSessionId(),
            meta: meta || null,
          }),
          keepalive: true,
        }).catch(() => {});
      },
      summary(days = 7) { return request(`/api/analytics/summary?days=${days}`, { auth: true }); },
    },

    stock: {
      movements() { return request('/api/stock-movements', { auth: true }); },
      clear() { return request('/api/stock-movements', { method: 'DELETE', auth: true, raw: true }); },
      delete(id) { return request(`/api/stock-movements/${id}`, { method: 'DELETE', auth: true, raw: true }); },
    },

    customers: {
      list() { return request('/api/customers', { auth: true }); },
      delete(id) { return request(`/api/customers/${id}`, { method: 'DELETE', auth: true, raw: true }); },
    },

    invoices: {
      list(opts = {}) {
        return request('/api/invoices', { auth: true, params: { status: opts.status } });
      },
      get(id) { return request(`/api/invoices/${id}`, { auth: true }); },
      issue(orderNo, payload) {
        return request(`/api/invoices/orders/${encodeURIComponent(orderNo)}/issue`, {
          method: 'POST', auth: true, body: payload || {},
        });
      },
      cancel(id, reason) {
        return request(`/api/invoices/${id}/cancel`, {
          method: 'POST', auth: true, body: { reason },
        });
      },
      pdfUrlAdmin(id) {
        // Auth gerekli — yeni sekmede açmak için token'lı blob yöntemi
        return request(`/api/invoices/${id}/pdf`, { auth: true, raw: true });
      },
      pdfUrlPublic(ettn, email) {
        return `${BASE_URL}/api/invoices/public/${encodeURIComponent(ettn)}?email=${encodeURIComponent(email)}`;
      },
      myList() { return request('/api/invoices/my/list', { customerAuth: true }); },
    },

    adminUsers: {
      list() { return request('/api/admin/users', { auth: true }); },
      create(payload) { return request('/api/admin/users', { method: 'POST', body: payload, auth: true }); },
      update(id, payload) { return request(`/api/admin/users/${id}`, { method: 'PUT', body: payload, auth: true }); },
      setActive(id, isActive) { return request(`/api/admin/users/${id}`, { method: 'PUT', body: { is_active: !!isActive }, auth: true }); },
      delete(id) { return request(`/api/admin/users/${id}`, { method: 'DELETE', auth: true, raw: true }); },
      changeMyPassword(currentPassword, newPassword) {
        return request('/api/admin/users/me/change-password', {
          method: 'POST', auth: true,
          body: { current_password: currentPassword, new_password: newPassword },
        });
      },
      // Granüler yetki
      permissionCatalog() { return request('/api/admin/users/permissions/catalog', { auth: true }); },
      myPermissions() { return request('/api/admin/users/me/permissions', { auth: true }); },
      getPermissions(id) { return request(`/api/admin/users/${id}/permissions`, { auth: true }); },
      setPermissions(id, permissions) {
        return request(`/api/admin/users/${id}/permissions`, { method: 'PUT', auth: true, body: { permissions } });
      },
    },

    // ── Banner / vitrin yönetimi ──
    banners: {
      listPublic(position) { return request('/api/banners', { params: { position } }); },
      listAdmin() { return request('/api/banners/admin/all', { auth: true }); },
      create(payload) { return request('/api/banners', { method: 'POST', auth: true, body: payload }); },
      update(id, payload) { return request(`/api/banners/${id}`, { method: 'PUT', auth: true, body: payload }); },
      reorder(order) { return request('/api/banners/reorder', { method: 'POST', auth: true, body: { order } }); },
      delete(id) { return request(`/api/banners/${id}`, { method: 'DELETE', auth: true, raw: true }); },
    },

    // ── Ana sayfa düzeni (sürükle-bırak) ──
    homepage: {
      listPublic() { return request('/api/homepage'); },
      listAdmin() { return request('/api/homepage/admin/all', { auth: true }); },
      create(payload) { return request('/api/homepage', { method: 'POST', auth: true, body: payload }); },
      update(id, payload) { return request(`/api/homepage/${id}`, { method: 'PUT', auth: true, body: payload }); },
      reorder(order) { return request('/api/homepage/reorder', { method: 'POST', auth: true, body: { order } }); },
      delete(id) { return request(`/api/homepage/${id}`, { method: 'DELETE', auth: true, raw: true }); },
    },

    // ── Blog / haber (CMS) ──
    blog: {
      listPublic(tag) { return request('/api/blog', { params: { tag } }); },
      getPublic(slug) { return request(`/api/blog/${slug}`); },
      listAdmin() { return request('/api/blog/admin/all', { auth: true }); },
      create(payload) { return request('/api/blog', { method: 'POST', auth: true, body: payload }); },
      update(id, payload) { return request(`/api/blog/${id}`, { method: 'PUT', auth: true, body: payload }); },
      delete(id) { return request(`/api/blog/${id}`, { method: 'DELETE', auth: true, raw: true }); },
    },

    // ── CMS statik sayfalar ──
    pages: {
      listPublic() { return request('/api/pages'); },
      getPublic(slug) { return request(`/api/pages/${slug}`); },
      listAdmin() { return request('/api/pages/admin/all', { auth: true }); },
      create(payload) { return request('/api/pages', { method: 'POST', auth: true, body: payload }); },
      update(id, payload) { return request(`/api/pages/${id}`, { method: 'PUT', auth: true, body: payload }); },
      delete(id) { return request(`/api/pages/${id}`, { method: 'DELETE', auth: true, raw: true }); },
    },

    // ── Otomatik fiyatlandırma kuralları ──
    pricingRules: {
      list() { return request('/api/pricing-rules', { auth: true }); },
      create(payload) { return request('/api/pricing-rules', { method: 'POST', auth: true, body: payload }); },
      update(id, payload) { return request(`/api/pricing-rules/${id}`, { method: 'PUT', auth: true, body: payload }); },
      preview(id) { return request(`/api/pricing-rules/${id}/preview`, { method: 'POST', auth: true }); },
      apply(id) { return request(`/api/pricing-rules/${id}/apply`, { method: 'POST', auth: true }); },
      delete(id) { return request(`/api/pricing-rules/${id}`, { method: 'DELETE', auth: true, raw: true }); },
    },

    // ── Toplu ürün içe aktarma ──
    imports: {
      templateUrl() { return `${BASE_URL}/api/imports/products/template.xlsx`; },
      importProducts(file, { dryRun = false } = {}) {
        const fd = new FormData();
        fd.append('file', file);
        return request('/api/imports/products', { method: 'POST', auth: true, body: fd, params: { dry_run: dryRun } });
      },
    },

    /* ── Dropshipping — tedarikçiden ürün kaynaklama ── */
    dropshipping: {
      preview(url, markup = null) {
        const params = { url };
        if (markup != null) params.markup = markup;
        return request('/api/dropshipping/preview', { auth: true, params });
      },
      importOne({ url, markup = null, categoryId = null, isActive = false }) {
        const body = { url, is_active: isActive };
        if (markup != null) body.markup = markup;
        if (categoryId != null) body.category_id = categoryId;
        return request('/api/dropshipping/import', { method: 'POST', auth: true, body });
      },
      orderFulfillment(orderId) {
        return request(`/api/dropshipping/orders/${orderId}/fulfillment`, { auth: true });
      },
      syncProduct(productId, reprice = true) {
        return request(`/api/dropshipping/products/${productId}/sync`, { method: 'POST', auth: true, body: { reprice } });
      },
      syncAll(reprice = true) {
        return request('/api/dropshipping/sync', { method: 'POST', auth: true, body: { reprice } });
      },
    },

    /* ── Canlı destek (chat) ── */
    chat: {
      /**
       * Müşteri chat WS bağlantısı — sadece üye girişi yapmış müşteriler için.
       * Üye girişi yoksa hata fırlatır. Token query string'de gönderilir;
       * sunucu JWT'yi doğrulayıp her müşteriye `cust:<id>` kalıcı oturumu açar.
       */
      connectCustomer(onEvent) {
        if (!getCustomerAccess() && !getCustomerRefresh()) {
          throw new ApiError('Canlı destek için üye girişi gerekli', 401);
        }
        const wsUrl = (t) => BASE_URL.replace(/^http/, 'ws') +
          '/api/ws/chat/customer?token=' + encodeURIComponent(t);
        let ws, closed = false, retry = null, attempts = 0, refreshedSinceOpen = false;
        // Süresi dolmuş token reddi handshake'te 1006 olarak döner (4401 değil);
        // bu yüzden reconnect'te token'ı taze oku, gerekirse yenile. Yenileme
        // başarısızsa (oturum bitti) sonsuz denemeyi durdur. Bir başarısızlık
        // dizisinde token yalnız bir kez yenilenir (başarılı bağlantıda sıfırlanır).
        const open = async () => {
          if (closed) return;
          let token = getCustomerAccess();
          if (!token) {
            try { await refreshCustomerAccess(); token = getCustomerAccess(); refreshedSinceOpen = true; }
            catch (_) { closed = true; return; }
          }
          if (!token || closed) return;
          ws = new WebSocket(wsUrl(token));
          ws.onopen = () => { attempts = 0; refreshedSinceOpen = false; };
          ws.onmessage = (e) => {
            if (e.data === 'pong') return;
            try { const m = JSON.parse(e.data); onEvent(m.event, m.data); } catch (_) {}
          };
          ws.onclose = async () => {
            if (closed) return;
            attempts += 1;
            // İlk başarısızlıkta token'ı bir kez yenile (muhtemelen süresi dolmuş);
            // yenileme başarısızsa oturum gerçekten bitmiştir → dur.
            if (!refreshedSinceOpen) {
              refreshedSinceOpen = true;
              try { await refreshCustomerAccess(); }
              catch (_) { closed = true; return; }
            }
            retry = setTimeout(open, Math.min(3000 * attempts, 30000));
          };
          ws.onerror = () => {};
        };
        open();
        const send = (obj) => { try { ws && ws.readyState === 1 && ws.send(JSON.stringify(obj)); } catch (_) {} };
        return {
          close: () => { closed = true; clearTimeout(retry); try { ws && ws.close(); } catch (_) {} },
          sendMessage: (body) => send({ action: 'send', body }),
          markRead: () => send({ action: 'mark_read' }),
          isOpen: () => !!(ws && ws.readyState === 1),
        };
      },
      /**
       * Admin chat WS — tüm konuşmaları dinler, hangisine cevap vereceğini
       * sendMessage(session_id, body) ile seçer.
       */
      connectAdmin(onEvent) {
        if (!getAccess()) throw new ApiError('Önce login olun', 401);
        const wsUrl = (t) => BASE_URL.replace(/^http/, 'ws') +
          '/api/ws/chat/admin?token=' + encodeURIComponent(t);
        let ws, closed = false, retry = null, attempts = 0, refreshedSinceOpen = false;
        const open = async () => {
          if (closed) return;
          let token = getAccess();
          if (!token) {
            try { await refreshAccess(); token = getAccess(); refreshedSinceOpen = true; }
            catch (_) { closed = true; return; }
          }
          if (!token || closed) return;
          ws = new WebSocket(wsUrl(token));
          ws.onopen = () => { attempts = 0; refreshedSinceOpen = false; };
          ws.onmessage = (e) => {
            if (e.data === 'pong') return;
            try { const m = JSON.parse(e.data); onEvent(m.event, m.data); } catch (_) {}
          };
          ws.onclose = async () => {
            if (closed) return;
            attempts += 1;
            if (!refreshedSinceOpen) {
              refreshedSinceOpen = true;
              try { await refreshAccess(); }
              catch (_) { closed = true; return; }
            }
            retry = setTimeout(open, Math.min(3000 * attempts, 30000));
          };
          ws.onerror = () => {};
        };
        open();
        const send = (obj) => { try { ws && ws.readyState === 1 && ws.send(JSON.stringify(obj)); } catch (_) {} };
        return {
          close: () => { closed = true; clearTimeout(retry); try { ws && ws.close(); } catch (_) {} },
          sendMessage: (sessionId, body) => send({ action: 'send', session_id: sessionId, body }),
          closeSession: (sessionId) => send({ action: 'close', session_id: sessionId }),
          markRead: (sessionId) => send({ action: 'mark_read', session_id: sessionId }),
          isOpen: () => !!(ws && ws.readyState === 1),
        };
      },
      adminListSessions() { return request('/api/chat/admin/sessions', { auth: true }); },
      adminMessages(sessionPk) {
        return request(`/api/chat/admin/sessions/${sessionPk}/messages`, { auth: true });
      },
      adminCloseSession(sessionPk) {
        return request(`/api/chat/admin/sessions/${sessionPk}/close`, { method: 'POST', auth: true });
      },
      adminDeleteMessage(messageId) {
        return request(`/api/chat/admin/messages/${messageId}`, { method: 'DELETE', auth: true, raw: true });
      },
      adminDeleteSession(sessionPk) {
        return request(`/api/chat/admin/sessions/${sessionPk}`, { method: 'DELETE', auth: true, raw: true });
      },
    },

    /* ── WebSocket (çift yönlü) ── */
    ws: {
      /**
       * Public WS bağlantısı. onEvent(name, data). closure(): kapatır.
       * Hata/disconnect'te 3sn sonra otomatik tekrar bağlanır.
       */
      connectPublic(onEvent) {
        let ws, closed = false, retry = null;
        const url = BASE_URL.replace(/^http/, 'ws') + '/api/ws/public';
        const open = () => {
          ws = new WebSocket(url);
          ws.onmessage = (e) => {
            try { const m = JSON.parse(e.data); onEvent(m.event, m.data); } catch (_) {}
          };
          ws.onclose = () => {
            if (closed) return;
            retry = setTimeout(open, 3000);
          };
          ws.onerror = () => {};
        };
        open();
        return () => { closed = true; clearTimeout(retry); try { ws && ws.close(); } catch (_) {} };
      },
      /**
       * Admin WS — token query string'de geçer. Çift yönlü.
       */
      connectAdmin(onEvent, onOpen) {
        const token = getAccess();
        if (!token) throw new ApiError('Önce login olun', 401);
        let ws, closed = false, retry = null;
        const url = BASE_URL.replace(/^http/, 'ws') + '/api/ws/admin?token=' + encodeURIComponent(token);
        const open = () => {
          ws = new WebSocket(url);
          ws.onopen = () => onOpen && onOpen();
          ws.onmessage = (e) => {
            try { const m = JSON.parse(e.data); onEvent(m.event, m.data); } catch (_) {}
          };
          ws.onclose = () => { if (!closed) retry = setTimeout(open, 3000); };
          ws.onerror = () => {};
        };
        open();
        return {
          close: () => { closed = true; clearTimeout(retry); try { ws && ws.close(); } catch (_) {} },
          send: (msg) => { try { ws && ws.readyState === 1 && ws.send(JSON.stringify(msg)); } catch (_) {} },
        };
      },
    },

    /* ── Server-Sent Events ── */
    events: {
      /**
       * SSE dinleyici açar. onEvent(name, data) callback alır.
       * Bağlantı koparsa EventSource otomatik tekrar dener.
       * Çıktı: kapatmak için kullanılacak fonksiyon.
       */
      subscribe(onEvent) {
        const es = new EventSource(`${BASE_URL}/api/events`);
        es.onmessage = (e) => {
          try {
            const payload = JSON.parse(e.data);
            onEvent(payload.event, payload.data);
          } catch (_) {}
        };
        es.onerror = () => { /* tarayıcı otomatik reconnect eder */ };
        return () => es.close();
      },
    },

    /* ── KVKK: çerez izni, veri ihracı, hesap silme ── */
    privacy: {
      /** Çerez tercihini hem localStorage'a yaz, hem backend'e log gönder. */
      saveConsent(cats) {
        const merged = { essential: true, preference: !!cats.preference, analytics: !!cats.analytics, marketing: !!cats.marketing };
        try { localStorage.setItem(CONSENT_KEY, JSON.stringify(merged)); } catch (_) {}
        // Eski anahtarı geriye uyum için boş bırakma yerine silelim
        try { localStorage.removeItem(LEGACY_CONSENT_KEY); } catch (_) {}
        // Backend'e logla (best-effort, hata olursa kullanıcıyı engellemez)
        fetch(`${BASE_URL}/api/privacy/consent`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(getCustomerAccess() ? { 'Authorization': `Bearer ${getCustomerAccess()}` } : {}),
          },
          body: JSON.stringify({ session_id: getSessionId(), categories: merged, policy_version: '1.0' }),
          keepalive: true,
        }).catch(() => {});
        return merged;
      },
      readConsent() { return readConsentCats(); },
      hasConsent() {
        try { return !!localStorage.getItem(CONSENT_KEY) || !!localStorage.getItem(LEGACY_CONSENT_KEY); } catch (_) { return false; }
      },
      /** KVKK m.11(d) — kişisel verinin JSON dökümü. */
      exportMyData() {
        return request('/api/customer-auth/me/data-export', { customerAuth: true });
      },
      /** Silme talebi başlat — mail'e onay linki gider. */
      requestDeletion(password, reason) {
        return request('/api/customer-auth/me/delete-request', {
          method: 'POST', customerAuth: true,
          body: { password, reason: reason || null },
        });
      },
      /** Mail'deki token ile silme talebini onayla (oturum şart değil). */
      confirmDeletion(token) {
        return request('/api/customer-auth/me/delete-confirm', {
          method: 'POST', body: { token },
        });
      },
      /** Admin: silme talepleri yönetimi. */
      adminListDeletions(status) {
        return request('/api/privacy/deletion-requests', { auth: true, params: { status } });
      },
      adminCancelDeletion(id) {
        return request(`/api/privacy/deletion-requests/${id}/cancel`, { method: 'POST', auth: true });
      },
      adminRunDeletion(id) {
        return request(`/api/privacy/deletion-requests/${id}/run`, { method: 'POST', auth: true });
      },
      adminConsentLogs(params) {
        return request('/api/privacy/consent-logs', { auth: true, params });
      },
    },

    /* ── GİB mükellef / TCKN-VKN doğrulama ── */
    tax: {
      /** value = TCKN (11 hane) veya VKN (10 hane). */
      lookup(value, opts = {}) {
        return request('/api/tax/lookup', {
          params: { value, query_gib: opts.queryGib === false ? false : undefined },
        });
      },
    },

    uploads: {
      async image(file) {
        const fd = new FormData();
        // Blob'a filename ekle (UploadFile'ın doğru parse etmesi için)
        const name = file.name || ('upload-' + Date.now() + '.jpg');
        fd.append('file', file, name);
        // request() wrapper'ı kullan — 401 refresh + ApiError otomatik
        const data = await request('/api/uploads/images', {
          method: 'POST', body: fd, auth: true,
        });
        data.full_url = data.url.startsWith('http') ? data.url : BASE_URL + data.url;
        return data;
      },
      absoluteUrl(urlOrPath) {
        if (!urlOrPath) return '';
        if (urlOrPath.startsWith('http') || urlOrPath.startsWith('data:')) return urlOrPath;
        return BASE_URL + urlOrPath;
      },
    },
  };

  global.api = api;
})(window);
