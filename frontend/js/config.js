(function () {
  var h = location.hostname;
  if (h !== 'localhost' && h !== '127.0.0.1') {
    window.TT_API_BASE = 'https://api.tecnotools.org';
  }
})();

/* ── Ölçüm ID'leri (js/tracking.js okur; boşken hiçbir dış script yüklenmez) ── */
window.TT_META_PIXEL_ID = '1589131622149868'; // Meta Pixel (Events Manager → TecnoTools)
window.TT_GA4_ID = 'G-E7Z9VGPZY6'; // GA4 Measurement ID (TecnoTools web akışı)
