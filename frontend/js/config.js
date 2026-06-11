(function () {
  var h = location.hostname;
  if (h !== 'localhost' && h !== '127.0.0.1') {
    window.TT_API_BASE = 'https://api.tecnotools.org';
  }
})();

/* ── Ölçüm ID'leri (js/tracking.js okur; boşken hiçbir dış script yüklenmez) ── */
window.TT_META_PIXEL_ID = ''; // Meta Pixel ID — örn '1234567890123456'
window.TT_GA4_ID = '';        // GA4 Measurement ID — örn 'G-XXXXXXXXXX'
