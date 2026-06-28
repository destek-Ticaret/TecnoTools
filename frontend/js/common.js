/* TecnoTools — ortak frontend yardımcıları
   ─────────────────────────────────────────────────────────────
   Tüm storefront sayfalarının paylaştığı kod tek noktada:
     • tema sistemi (applyTheme / toggleTheme)
     • HTML kaçışı (escapeHtml / escapeAttr)
     • API hazır-olma bekleyici (ttReady)

   <head> içinde BLOKLAYICI (defer'sız) yüklenir; bu sayede:
     1. Tema gövde render'ından ÖNCE uygulanır → koyu mod "flash"ı (FOUC) olmaz.
     2. Global yardımcılar, sayfanın inline <script>'i çalışmadan önce hazırdır.

   Not: api.js `defer` ile yüklendiğinden henüz hazır olmayabilir; bu yüzden
   sayfa init'i doğrudan değil `ttReady(init)` ile çağrılmalıdır.
   ─────────────────────────────────────────────────────────────*/
(function (global) {
  'use strict';

  const THEME_KEY = 'tt_theme';

  // İlk ziyarette varsayılan dil İngilizce + EUR (uluslararası öncelik).
  (function initLocale() {
    try {
      if (!localStorage.getItem('tt_lang')) {
        localStorage.setItem('tt_lang', 'en');
        if (!localStorage.getItem('tt_currency')) localStorage.setItem('tt_currency', 'EUR');
      }
    } catch (e) {}
  })();

  function applyTheme() {
    const t = localStorage.getItem(THEME_KEY) || 'light';
    document.documentElement.dataset.theme = t === 'dark' ? 'dark' : '';
  }

  function toggleTheme() {
    const cur = localStorage.getItem(THEME_KEY) || 'light';
    localStorage.setItem(THEME_KEY, cur === 'dark' ? 'light' : 'dark');
    applyTheme();
  }

  const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  const escapeAttr = (s) => escapeHtml(s);

  /** api.js (defer) yüklenip `window.api` hazır olunca cb()'yi çağırır.
   *  ~5sn (50×100ms) timeout; backend gecikse bile sayfa init olur. */
  function ttReady(cb) {
    let tries = 0;
    (async () => {
      while (!global.api && tries++ < 50) {
        await new Promise((r) => setTimeout(r, 100));
      }
      cb();
    })();
  }

  // Tema FOUC'unu önlemek için hemen uygula (html elementi parse anında mevcut).
  applyTheme();

  global.applyTheme = applyTheme;
  global.toggleTheme = toggleTheme;
  global.escapeHtml = escapeHtml;
  global.escapeAttr = escapeAttr;
  global.ttReady = ttReady;
})(window);
