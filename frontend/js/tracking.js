/* Meta Pixel + GA4 ölçüm katmanı.
 *
 * ID'ler js/config.js'te tanımlanır:
 *   window.TT_META_PIXEL_ID = '1234567890';   // Meta (Facebook/Instagram) Pixel
 *   window.TT_GA4_ID        = 'G-XXXXXXXXXX'; // Google Analytics 4
 * İkisi de boşsa hiçbir dış script yüklenmez (dev modu / ölçümsüz çalışma).
 *
 * Sayfalardan kullanım:
 *   ttTrack('ViewContent', { id, name, price })
 *   ttTrack('AddToCart',   { id, name, price, qty, value })
 *   ttTrack('InitiateCheckout', { value })
 *   ttTrackPurchaseOnce(orderNo, total)  // sipariş başına 1 kez (yenilemede tekrarlamaz)
 */
(function () {
  var PIXEL = window.TT_META_PIXEL_ID || '';
  var GA4 = window.TT_GA4_ID || '';

  if (PIXEL) {
    /* Meta Pixel resmi base snippet */
    !(function (f, b, e, v, n, t, s) {
      if (f.fbq) return; n = f.fbq = function () {
        n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments);
      };
      if (!f._fbq) f._fbq = n; n.push = n; n.loaded = !0; n.version = '2.0';
      n.queue = []; t = b.createElement(e); t.async = !0; t.src = v;
      s = b.getElementsByTagName(e)[0]; s.parentNode.insertBefore(t, s);
    })(window, document, 'script', 'https://connect.facebook.net/en_US/fbevents.js');
    fbq('init', PIXEL);
    fbq('track', 'PageView');
  }

  if (GA4) {
    var gs = document.createElement('script');
    gs.async = true;
    gs.src = 'https://www.googletagmanager.com/gtag/js?id=' + encodeURIComponent(GA4);
    document.head.appendChild(gs);
    window.dataLayer = window.dataLayer || [];
    window.gtag = window.gtag || function () { window.dataLayer.push(arguments); };
    gtag('js', new Date());
    gtag('config', GA4);
  }

  var GA4_EVENT = {
    ViewContent: 'view_item',
    AddToCart: 'add_to_cart',
    InitiateCheckout: 'begin_checkout',
    Purchase: 'purchase',
  };

  window.ttTrack = function (event, data) {
    data = data || {};
    try {
      if (PIXEL && window.fbq) {
        var fb = { currency: data.currency || 'TRY' };
        if (data.value != null) fb.value = Number(data.value) || 0;
        if (data.id != null) { fb.content_ids = [String(data.id)]; fb.content_type = 'product'; }
        if (data.name) fb.content_name = data.name;
        /* eventID: Purchase'ta sunucu/yenileme tekrarına karşı tekilleştirme */
        var opts = data.order_no ? { eventID: 'order-' + data.order_no } : undefined;
        fbq('track', event, fb, opts);
      }
      if (GA4 && window.gtag) {
        var g = { currency: data.currency || 'TRY' };
        if (data.value != null) g.value = Number(data.value) || 0;
        if (data.order_no) g.transaction_id = String(data.order_no);
        if (data.id != null || data.name) {
          g.items = [{
            item_id: String(data.id != null ? data.id : ''),
            item_name: data.name || '',
            price: Number(data.price != null ? data.price : data.value) || 0,
            quantity: data.qty || 1,
          }];
        }
        gtag('event', GA4_EVENT[event] || event, g);
      }
    } catch (_) { /* ölçüm asla sayfayı kırmasın */ }
  };

  window.ttTrackPurchaseOnce = function (orderNo, total) {
    if (!orderNo) return;
    var k = 'tt_purchase_' + orderNo;
    try {
      if (localStorage.getItem(k)) return;
      localStorage.setItem(k, '1');
    } catch (_) { /* private mode vb. — yine de gönder */ }
    window.ttTrack('Purchase', { value: Number(total) || 0, order_no: orderNo });
  };
})();
