/* TecnoTools — çok dilli çeviri motoru (tüm sayfalarda paylaşılır)
   ─────────────────────────────────────────────────────────────
   HTML Türkçe yazılır; bu motor görünür metni hedef dile çevirir.
   Desteklenen diller: TR (kaynak), EN, DE, FR, ES, IT.

   Çalışma mantığı:
     • Dil değişince tt_lang + tt_currency kaydedilir ve sayfa YENİDEN YÜKLENİR.
       Böylece her zaman Türkçe DOM'dan hedef dile temiz çeviri yapılır
       (diller arası ters-harita karmaşası yok).
     • Sayfa yüklenince applyI18n() DOM'u gezip Türkçe metni hedef dile çevirir.
     • MutationObserver dinamik içeriği (JS ile eklenen) de çevirir.
   ───────────────────────────────────────────────────────────── */
(function (global) {
  'use strict';

  var LANGS = ['tr', 'en', 'de', 'fr', 'es', 'it'];
  var LANG_NAMES = { tr: 'Türkçe', en: 'English', de: 'Deutsch', fr: 'Français', es: 'Español', it: 'Italiano' };
  var LANG_FLAGS = { tr: '🇹🇷', en: '🇬🇧', de: '🇩🇪', fr: '🇫🇷', es: '🇪🇸', it: '🇮🇹' };

  function storeLang() { return localStorage.getItem('tt_lang') || 'tr'; }

  // İlk ziyarette tarayıcı diline göre dil + para birimi (bir kez).
  (function initLocale() {
    try {
      if (!localStorage.getItem('tt_lang')) {
        var nav = (navigator.language || navigator.userLanguage || 'tr').toLowerCase().slice(0, 2);
        var lang = LANGS.indexOf(nav) >= 0 ? nav : 'en';
        localStorage.setItem('tt_lang', lang);
        if (!localStorage.getItem('tt_currency')) {
          localStorage.setItem('tt_currency', lang === 'tr' ? 'TRY' : 'EUR');
        }
      }
    } catch (e) {}
  })();

  // ── Sözlük: Türkçe ifade → { dil: çeviri }. Eksik dil EN'e, o da yoksa TR'ye düşer.
  var DICT = {
    // Topbar / navbar
    "Kategoriler": { en:"Categories", de:"Kategorien", fr:"Catégories", es:"Categorías", it:"Categorie" },
    "Ürünler": { en:"Products", de:"Produkte", fr:"Produits", es:"Productos", it:"Prodotti" },
    "Kampanyalar": { en:"Promotions", de:"Aktionen", fr:"Promotions", es:"Promociones", it:"Promozioni" },
    "Sipariş Takip": { en:"Order Tracking", de:"Sendungsverfolgung", fr:"Suivi de commande", es:"Seguimiento de pedido", it:"Tracciamento ordine" },
    "Yorumlar": { en:"Reviews", de:"Bewertungen", fr:"Avis", es:"Opiniones", it:"Recensioni" },
    "İletişim": { en:"Contact", de:"Kontakt", fr:"Contact", es:"Contacto", it:"Contatti" },
    "Aradığınız ürünü yazın...": { en:"Search for a product...", de:"Produkt suchen...", fr:"Rechercher un produit...", es:"Buscar un producto...", it:"Cerca un prodotto..." },
    "Tema değiştir": { en:"Toggle theme", de:"Thema wechseln", fr:"Changer de thème", es:"Cambiar tema", it:"Cambia tema" },
    "Hesabım": { en:"My Account", de:"Mein Konto", fr:"Mon compte", es:"Mi cuenta", it:"Il mio account" },
    "Favorilerim": { en:"My Wishlist", de:"Meine Wunschliste", fr:"Mes favoris", es:"Mis favoritos", it:"I miei preferiti" },
    "Sepet": { en:"Cart", de:"Warenkorb", fr:"Panier", es:"Carrito", it:"Carrello" },
    "Sepetim": { en:"My Cart", de:"Mein Warenkorb", fr:"Mon panier", es:"Mi carrito", it:"Il mio carrello" },
    "TecnoTools ana sayfa": { en:"TecnoTools home", de:"TecnoTools Startseite", fr:"Accueil TecnoTools", es:"Inicio TecnoTools", it:"Home TecnoTools" },
    // Hero
    "Yeni Sezon · İlkbahar 2026": { en:"New Season · Spring 2026", de:"Neue Saison · Frühling 2026", fr:"Nouvelle saison · Printemps 2026", es:"Nueva temporada · Primavera 2026", it:"Nuova stagione · Primavera 2026" },
    "Profesyoneller İçin": { en:"For Professionals", de:"Für Profis", fr:"Pour les professionnels", es:"Para profesionales", it:"Per professionisti" },
    "Doğru Alet, Doğru Adres.": { en:"Right Tool, Right Place.", de:"Richtiges Werkzeug, richtige Adresse.", fr:"Le bon outil, au bon endroit.", es:"La herramienta correcta, en el lugar correcto.", it:"L'attrezzo giusto, nel posto giusto." },
    "Endüstri standartlarını karşılayan el aletleri, elektrikli ekipmanlar ve ölçüm cihazları. Aynı gün kargo, 14 gün iade.": { en:"Industry-standard hand tools, power equipment and measurement devices. Same-day shipping, 14-day returns.", de:"Handwerkzeuge, Elektrogeräte und Messgeräte nach Industriestandard. Versand am selben Tag, 14 Tage Rückgabe.", fr:"Outils à main, équipements électriques et instruments de mesure aux normes industrielles. Expédition le jour même, retour sous 14 jours.", es:"Herramientas manuales, equipos eléctricos e instrumentos de medición según normas industriales. Envío el mismo día, devolución en 14 días.", it:"Utensili manuali, apparecchiature elettriche e strumenti di misura a norma industriale. Spedizione in giornata, reso entro 14 giorni." },
    "Alışverişe Başla": { en:"Start Shopping", de:"Jetzt einkaufen", fr:"Commencer mes achats", es:"Empezar a comprar", it:"Inizia lo shopping" },
    "Kampanyaları İncele": { en:"Browse Promotions", de:"Aktionen ansehen", fr:"Voir les promotions", es:"Ver promociones", it:"Vedi le promozioni" },
    "Ürün Çeşidi": { en:"Product Variety", de:"Produktvielfalt", fr:"Variété de produits", es:"Variedad de productos", it:"Varietà di prodotti" },
    "Mutlu Müşteri": { en:"Happy Customers", de:"Zufriedene Kunden", fr:"Clients satisfaits", es:"Clientes satisfechos", it:"Clienti soddisfatti" },
    "Ortalama Puan": { en:"Average Rating", de:"Durchschnittsbewertung", fr:"Note moyenne", es:"Valoración media", it:"Valutazione media" },
    "Canlı Destek": { en:"Live Support", de:"Live-Support", fr:"Support en direct", es:"Soporte en vivo", it:"Supporto dal vivo" },
    // Features strip
    "Ücretsiz Kargo": { en:"Free Shipping", de:"Kostenloser Versand", fr:"Livraison gratuite", es:"Envío gratis", it:"Spedizione gratuita" },
    "500₺ ve üzeri siparişlerde": { en:"On orders ₺500 and above", de:"Ab ₺500 Bestellwert", fr:"Pour les commandes de ₺500 et plus", es:"En pedidos de ₺500 o más", it:"Sugli ordini da ₺500 in su" },
    "14 Gün İade": { en:"14-Day Returns", de:"14 Tage Rückgabe", fr:"Retours sous 14 jours", es:"Devoluciones en 14 días", it:"Reso entro 14 giorni" },
    "Kutu açılmamışsa 14 gün içinde iade": { en:"Returns within 14 days if the box is unopened", de:"Rückgabe innerhalb von 14 Tagen bei ungeöffneter Verpackung", fr:"Retour sous 14 jours si la boîte n'est pas ouverte", es:"Devolución en 14 días si la caja está sin abrir", it:"Reso entro 14 giorni se la confezione è chiusa" },
    "Güvenli Ödeme": { en:"Secure Payment", de:"Sichere Zahlung", fr:"Paiement sécurisé", es:"Pago seguro", it:"Pagamento sicuro" },
    "256-bit SSL şifreleme": { en:"256-bit SSL encryption", de:"256-Bit-SSL-Verschlüsselung", fr:"Chiffrement SSL 256 bits", es:"Cifrado SSL de 256 bits", it:"Crittografia SSL a 256 bit" },
    "7/24 Destek": { en:"24/7 Support", de:"24/7 Support", fr:"Assistance 24/7", es:"Soporte 24/7", it:"Supporto 24/7" },
    "Canlı sohbet & e-posta": { en:"Live chat & email", de:"Live-Chat & E-Mail", fr:"Chat en direct & e-mail", es:"Chat en vivo y correo", it:"Chat dal vivo ed e-mail" },
    // Sections
    "Keşfet": { en:"Discover", de:"Entdecken", fr:"Découvrir", es:"Descubrir", it:"Scopri" },
    "Popüler Kategoriler": { en:"Popular Categories", de:"Beliebte Kategorien", fr:"Catégories populaires", es:"Categorías populares", it:"Categorie popolari" },
    "Tümünü Gör →": { en:"See All →", de:"Alle ansehen →", fr:"Voir tout →", es:"Ver todo →", it:"Vedi tutto →" },
    "Sınırlı Süre": { en:"Limited Time", de:"Begrenzte Zeit", fr:"Durée limitée", es:"Tiempo limitado", it:"Tempo limitato" },
    "İlk Alışverişinizde": { en:"On Your First Order", de:"Bei Ihrer ersten Bestellung", fr:"Sur votre première commande", es:"En tu primera compra", it:"Sul tuo primo ordine" },
    "%15 Hoşgeldin İndirimi": { en:"15% Welcome Discount", de:"15% Willkommensrabatt", fr:"15% de remise de bienvenue", es:"15% de descuento de bienvenida", it:"15% di sconto di benvenuto" },
    "Aşağıdaki kuponu sepette uygulayarak tüm ürünlerde geçerli %15 indirimden faydalanın. 31 Mayıs'a kadar.": { en:"Apply the coupon below at checkout to enjoy 15% off all products. Until May 31.", de:"Lösen Sie den Gutschein unten an der Kasse ein und erhalten Sie 15% auf alle Produkte. Bis 31. Mai.", fr:"Appliquez le code ci-dessous au paiement pour 15% sur tous les produits. Jusqu'au 31 mai.", es:"Aplica el cupón de abajo al pagar para un 15% en todos los productos. Hasta el 31 de mayo.", it:"Applica il coupon qui sotto al pagamento per il 15% su tutti i prodotti. Fino al 31 maggio." },
    "Kuponu kopyalamak için tıklayın": { en:"Click to copy the coupon", de:"Zum Kopieren des Gutscheins klicken", fr:"Cliquez pour copier le code", es:"Haz clic para copiar el cupón", it:"Clicca per copiare il coupon" },
    "En Çok Tercih Edilenler": { en:"Most Popular", de:"Am beliebtesten", fr:"Les plus populaires", es:"Los más populares", it:"I più popolari" },
    "Öne Çıkan Ürünler": { en:"Featured Products", de:"Empfohlene Produkte", fr:"Produits en vedette", es:"Productos destacados", it:"Prodotti in evidenza" },
    "Sırala:": { en:"Sort by:", de:"Sortieren:", fr:"Trier :", es:"Ordenar:", it:"Ordina:" },
    "Önerilen": { en:"Featured", de:"Empfohlen", fr:"Recommandé", es:"Recomendado", it:"Consigliato" },
    "Fiyat: Düşükten Yükseğe": { en:"Price: Low to High", de:"Preis: aufsteigend", fr:"Prix : croissant", es:"Precio: de menor a mayor", it:"Prezzo: dal più basso" },
    "Fiyat: Yüksekten Düşüğe": { en:"Price: High to Low", de:"Preis: absteigend", fr:"Prix : décroissant", es:"Precio: de mayor a menor", it:"Prezzo: dal più alto" },
    "En Yüksek Puan": { en:"Highest Rated", de:"Beste Bewertung", fr:"Mieux notés", es:"Mejor valorados", it:"Più votati" },
    "Yeni Gelenler": { en:"New Arrivals", de:"Neuheiten", fr:"Nouveautés", es:"Novedades", it:"Novità" },
    "Müşteri Deneyimleri": { en:"Customer Experiences", de:"Kundenerfahrungen", fr:"Expériences clients", es:"Experiencias de clientes", it:"Esperienze dei clienti" },
    "Deneyimini Paylaş": { en:"Share Your Experience", de:"Teilen Sie Ihre Erfahrung", fr:"Partagez votre expérience", es:"Comparte tu experiencia", it:"Condividi la tua esperienza" },
    "(0 değerlendirme)": { en:"(0 reviews)", de:"(0 Bewertungen)", fr:"(0 avis)", es:"(0 opiniones)", it:"(0 recensioni)" },
    "Henüz yayınlanmış yorum yok. İlk yorumu sen bırak!": { en:"No reviews yet. Be the first!", de:"Noch keine Bewertungen. Seien Sie der Erste!", fr:"Pas encore d'avis. Soyez le premier !", es:"Aún no hay opiniones. ¡Sé el primero!", it:"Ancora nessuna recensione. Sii il primo!" },
    "Bir yorum bırak": { en:"Leave a review", de:"Bewertung abgeben", fr:"Laisser un avis", es:"Dejar una opinión", it:"Lascia una recensione" },
    "Ürün": { en:"Product", de:"Produkt", fr:"Produit", es:"Producto", it:"Prodotto" },
    "Puan": { en:"Rating", de:"Bewertung", fr:"Note", es:"Valoración", it:"Valutazione" },
    "Bir ürün seçin…": { en:"Select a product…", de:"Produkt auswählen…", fr:"Sélectionnez un produit…", es:"Selecciona un producto…", it:"Seleziona un prodotto…" },
    "Adın": { en:"Your Name", de:"Ihr Name", fr:"Votre nom", es:"Tu nombre", it:"Il tuo nome" },
    "E-posta (opsiyonel)": { en:"Email (optional)", de:"E-Mail (optional)", fr:"E-mail (facultatif)", es:"Correo (opcional)", it:"E-mail (facoltativa)" },
    "Başlık (opsiyonel)": { en:"Title (optional)", de:"Titel (optional)", fr:"Titre (facultatif)", es:"Título (opcional)", it:"Titolo (facoltativo)" },
    "Yorumun": { en:"Your Review", de:"Ihre Bewertung", fr:"Votre avis", es:"Tu opinión", it:"La tua recensione" },
    "Ürün hakkındaki görüşlerini paylaş…": { en:"Share your thoughts about the product…", de:"Teilen Sie Ihre Meinung zum Produkt…", fr:"Partagez votre avis sur le produit…", es:"Comparte tu opinión sobre el producto…", it:"Condividi la tua opinione sul prodotto…" },
    "Yorumu Gönder": { en:"Submit Review", de:"Bewertung senden", fr:"Envoyer l'avis", es:"Enviar opinión", it:"Invia recensione" },
    // Newsletter
    "İlk Senden Haberdar Ol": { en:"Be the First to Know", de:"Erfahren Sie es als Erster", fr:"Soyez le premier informé", es:"Sé el primero en saberlo", it:"Sii il primo a saperlo" },
    "E-posta adresiniz": { en:"Your email address", de:"Ihre E-Mail-Adresse", fr:"Votre adresse e-mail", es:"Tu correo electrónico", it:"Il tuo indirizzo e-mail" },
    "Abone Ol": { en:"Subscribe", de:"Abonnieren", fr:"S'abonner", es:"Suscribirse", it:"Iscriviti" },
    // Footer
    "Mağaza": { en:"Shop", de:"Shop", fr:"Boutique", es:"Tienda", it:"Negozio" },
    "Tüm Ürünler": { en:"All Products", de:"Alle Produkte", fr:"Tous les produits", es:"Todos los productos", it:"Tutti i prodotti" },
    "Çok Satanlar": { en:"Best Sellers", de:"Bestseller", fr:"Meilleures ventes", es:"Más vendidos", it:"Più venduti" },
    "İndirimli Ürünler": { en:"On Sale", de:"Im Angebot", fr:"En promotion", es:"En oferta", it:"In offerta" },
    "Yardım": { en:"Help", de:"Hilfe", fr:"Aide", es:"Ayuda", it:"Aiuto" },
    "Kargo Bilgisi": { en:"Shipping Info", de:"Versandinfo", fr:"Infos livraison", es:"Información de envío", it:"Info spedizione" },
    "İade & Değişim": { en:"Returns & Exchanges", de:"Rückgabe & Umtausch", fr:"Retours & échanges", es:"Devoluciones y cambios", it:"Resi e cambi" },
    "Sipariş Takibi": { en:"Order Tracking", de:"Sendungsverfolgung", fr:"Suivi de commande", es:"Seguimiento de pedido", it:"Tracciamento ordine" },
    "Sıkça Sorulan Sorular": { en:"FAQ", de:"Häufige Fragen", fr:"FAQ", es:"Preguntas frecuentes", it:"Domande frequenti" },
    "Garanti Koşulları": { en:"Warranty Terms", de:"Garantiebedingungen", fr:"Conditions de garantie", es:"Términos de garantía", it:"Condizioni di garanzia" },
    "Şirket": { en:"Company", de:"Unternehmen", fr:"Entreprise", es:"Empresa", it:"Azienda" },
    "Hakkımızda": { en:"About Us", de:"Über uns", fr:"À propos", es:"Sobre nosotros", it:"Chi siamo" },
    "Kariyer": { en:"Careers", de:"Karriere", fr:"Carrières", es:"Empleo", it:"Lavora con noi" },
    "Gizlilik & Çerez Politikası": { en:"Privacy & Cookie Policy", de:"Datenschutz & Cookie-Richtlinie", fr:"Politique de confidentialité et cookies", es:"Política de privacidad y cookies", it:"Privacy e cookie" },
    "Çerez ayarları": { en:"Cookie settings", de:"Cookie-Einstellungen", fr:"Paramètres des cookies", es:"Configuración de cookies", it:"Impostazioni cookie" },
    "Pzt-Cmt 09:00 - 19:00": { en:"Mon-Sat 09:00 - 19:00", de:"Mo-Sa 09:00 - 19:00", fr:"Lun-Sam 09:00 - 19:00", es:"Lun-Sáb 09:00 - 19:00", it:"Lun-Sab 09:00 - 19:00" },
    // Cart panel
    "Ara toplam": { en:"Subtotal", de:"Zwischensumme", fr:"Sous-total", es:"Subtotal", it:"Subtotale" },
    "KDV (%20)": { en:"VAT (20%)", de:"MwSt. (20%)", fr:"TVA (20%)", es:"IVA (20%)", it:"IVA (20%)" },
    "Kargo": { en:"Shipping", de:"Versand", fr:"Livraison", es:"Envío", it:"Spedizione" },
    "Toplam": { en:"Total", de:"Gesamt", fr:"Total", es:"Total", it:"Totale" },
    "Güvenli Ödemeye Geç": { en:"Proceed to Secure Checkout", de:"Zur sicheren Kasse", fr:"Passer au paiement sécurisé", es:"Ir al pago seguro", it:"Vai al pagamento sicuro" },
    "256-bit SSL Şifreli · Güvenli Ödeme": { en:"256-bit SSL Encrypted · Secure Payment", de:"256-Bit-SSL-verschlüsselt · Sichere Zahlung", fr:"Chiffré SSL 256 bits · Paiement sécurisé", es:"Cifrado SSL de 256 bits · Pago seguro", it:"Crittografia SSL 256 bit · Pagamento sicuro" },
    // Checkout
    "Siparişi Tamamla": { en:"Complete Order", de:"Bestellung abschließen", fr:"Finaliser la commande", es:"Completar pedido", it:"Completa l'ordine" },
    "Kapat": { en:"Close", de:"Schließen", fr:"Fermer", es:"Cerrar", it:"Chiudi" },
    "İletişim Bilgileri": { en:"Contact Information", de:"Kontaktdaten", fr:"Coordonnées", es:"Información de contacto", it:"Informazioni di contatto" },
    "Ad Soyad *": { en:"Full Name *", de:"Vollständiger Name *", fr:"Nom complet *", es:"Nombre completo *", it:"Nome completo *" },
    "Telefon *": { en:"Phone *", de:"Telefon *", fr:"Téléphone *", es:"Teléfono *", it:"Telefono *" },
    "E-posta *": { en:"Email *", de:"E-Mail *", fr:"E-mail *", es:"Correo *", it:"E-mail *" },
    "Teslimat Adresi": { en:"Shipping Address", de:"Lieferadresse", fr:"Adresse de livraison", es:"Dirección de envío", it:"Indirizzo di spedizione" },
    "Şehir *": { en:"City *", de:"Stadt *", fr:"Ville *", es:"Ciudad *", it:"Città *" },
    "Şehir girin": { en:"Enter city", de:"Stadt eingeben", fr:"Saisir la ville", es:"Introduce la ciudad", it:"Inserisci la città" },
    "İlçe": { en:"District", de:"Bezirk", fr:"District", es:"Distrito", it:"Distretto" },
    "Açık Adres *": { en:"Full Address *", de:"Vollständige Adresse *", fr:"Adresse complète *", es:"Dirección completa *", it:"Indirizzo completo *" },
    "Sipariş Notu (opsiyonel)": { en:"Order Note (optional)", de:"Bestellnotiz (optional)", fr:"Note de commande (facultatif)", es:"Nota del pedido (opcional)", it:"Nota dell'ordine (facoltativa)" },
    "Ödeme": { en:"Payment", de:"Zahlung", fr:"Paiement", es:"Pago", it:"Pagamento" },
    "İndirim Kuponu": { en:"Discount Coupon", de:"Rabattgutschein", fr:"Code de réduction", es:"Cupón de descuento", it:"Coupon sconto" },
    "Kupon kodu girin": { en:"Enter coupon code", de:"Gutscheincode eingeben", fr:"Saisir le code", es:"Introduce el código", it:"Inserisci il codice" },
    "Uygula": { en:"Apply", de:"Anwenden", fr:"Appliquer", es:"Aplicar", it:"Applica" },
    "Sipariş Özeti": { en:"Order Summary", de:"Bestellübersicht", fr:"Récapitulatif", es:"Resumen del pedido", it:"Riepilogo ordine" },
    "KDV dahil": { en:"VAT included", de:"inkl. MwSt.", fr:"TVA incluse", es:"IVA incluido", it:"IVA inclusa" },
    // Guest choice modal
    "Üye Girişi": { en:"Sign In", de:"Anmelden", fr:"Se connecter", es:"Iniciar sesión", it:"Accedi" },
    "Hemen Üye Ol": { en:"Register Now", de:"Jetzt registrieren", fr:"S'inscrire", es:"Regístrate ahora", it:"Registrati ora" },
    "Üye Olmadan Devam Et": { en:"Continue as Guest", de:"Als Gast fortfahren", fr:"Continuer en tant qu'invité", es:"Continuar como invitado", it:"Continua come ospite" },
    // Return modal
    "Ürün İade Talebi": { en:"Product Return Request", de:"Rücksendeantrag", fr:"Demande de retour", es:"Solicitud de devolución", it:"Richiesta di reso" },
    "Sipariş No": { en:"Order Number", de:"Bestellnummer", fr:"Numéro de commande", es:"Número de pedido", it:"Numero ordine" },
    "E-posta": { en:"Email", de:"E-Mail", fr:"E-mail", es:"Correo", it:"E-mail" },
    "İptal": { en:"Cancel", de:"Abbrechen", fr:"Annuler", es:"Cancelar", it:"Annulla" },
    "Devam Et": { en:"Continue", de:"Weiter", fr:"Continuer", es:"Continuar", it:"Continua" },
    "İade Nedeni": { en:"Return Reason", de:"Rückgabegrund", fr:"Motif du retour", es:"Motivo de devolución", it:"Motivo del reso" },
    "Diğer": { en:"Other", de:"Sonstiges", fr:"Autre", es:"Otro", it:"Altro" },
    "Sorgula": { en:"Look Up", de:"Abfragen", fr:"Rechercher", es:"Consultar", it:"Cerca" },
    // Order success
    "Ödemeniz Başarılı!": { en:"Payment Successful!", de:"Zahlung erfolgreich!", fr:"Paiement réussi !", es:"¡Pago exitoso!", it:"Pagamento riuscito!" },
    "Alışverişiniz için teşekkür ederiz 🎉": { en:"Thank you for your purchase 🎉", de:"Vielen Dank für Ihren Einkauf 🎉", fr:"Merci pour votre achat 🎉", es:"Gracias por tu compra 🎉", it:"Grazie per il tuo acquisto 🎉" },
    "Ürün Sayısı": { en:"Items", de:"Artikel", fr:"Articles", es:"Artículos", it:"Articoli" },
    "Tahmini Teslimat": { en:"Estimated Delivery", de:"Voraussichtliche Lieferung", fr:"Livraison estimée", es:"Entrega estimada", it:"Consegna stimata" },
    "Alışverişe Devam Et": { en:"Continue Shopping", de:"Weiter einkaufen", fr:"Continuer mes achats", es:"Seguir comprando", it:"Continua lo shopping" },
    // Product grid / cart / wishlist (JS-rendered)
    "Henüz ürün eklenmedi": { en:"No products yet", de:"Noch keine Produkte", fr:"Aucun produit pour le moment", es:"Aún no hay productos", it:"Ancora nessun prodotto" },
    "Sonuç bulunamadı": { en:"No results found", de:"Keine Ergebnisse gefunden", fr:"Aucun résultat", es:"No se encontraron resultados", it:"Nessun risultato trovato" },
    "Sepete Ekle": { en:"Add to Cart", de:"In den Warenkorb", fr:"Ajouter au panier", es:"Añadir al carrito", it:"Aggiungi al carrello" },
    "Stokta Yok": { en:"Out of Stock", de:"Nicht vorrätig", fr:"Rupture de stock", es:"Agotado", it:"Esaurito" },
    "Ekle": { en:"Add", de:"Hinzufügen", fr:"Ajouter", es:"Añadir", it:"Aggiungi" },
    "Tümü": { en:"All", de:"Alle", fr:"Tout", es:"Todo", it:"Tutti" },
    "Hızlı bakış": { en:"Quick view", de:"Schnellansicht", fr:"Aperçu rapide", es:"Vista rápida", it:"Anteprima rapida" },
    "Sepetiniz henüz boş": { en:"Your cart is empty", de:"Ihr Warenkorb ist leer", fr:"Votre panier est vide", es:"Tu carrito está vacío", it:"Il tuo carrello è vuoto" },
    "Favori listeniz boş": { en:"Your wishlist is empty", de:"Ihre Wunschliste ist leer", fr:"Votre liste de favoris est vide", es:"Tu lista de favoritos está vacía", it:"La tua lista dei preferiti è vuota" },
    "Kaldır": { en:"Remove", de:"Entfernen", fr:"Retirer", es:"Quitar", it:"Rimuovi" },
    "Ücretsiz": { en:"Free", de:"Kostenlos", fr:"Gratuit", es:"Gratis", it:"Gratis" },
    "Bağlantıyı kopyala": { en:"Copy link", de:"Link kopieren", fr:"Copier le lien", es:"Copiar enlace", it:"Copia link" },
    // Account & auth
    "Üye Ol": { en:"Register", de:"Registrieren", fr:"S'inscrire", es:"Registrarse", it:"Registrati" },
    "Giriş Yap": { en:"Sign In", de:"Anmelden", fr:"Se connecter", es:"Iniciar sesión", it:"Accedi" },
    "Gönder": { en:"Send", de:"Senden", fr:"Envoyer", es:"Enviar", it:"Invia" },
    // Toasts / statuses
    "Ürün bulunamadı": { en:"Product not found", de:"Produkt nicht gefunden", fr:"Produit introuvable", es:"Producto no encontrado", it:"Prodotto non trovato" },
    "Bu ürün şu anda stokta yok": { en:"This product is currently out of stock", de:"Dieses Produkt ist derzeit nicht vorrätig", fr:"Ce produit est actuellement en rupture", es:"Este producto está agotado", it:"Questo prodotto è esaurito" },
    "Favorilere eklendi": { en:"Added to favorites", de:"Zu Favoriten hinzugefügt", fr:"Ajouté aux favoris", es:"Añadido a favoritos", it:"Aggiunto ai preferiti" },
    "Favorilerden kaldırıldı": { en:"Removed from favorites", de:"Aus Favoriten entfernt", fr:"Retiré des favoris", es:"Eliminado de favoritos", it:"Rimosso dai preferiti" },
    // Chat widget
    "Canlı destek": { en:"Live support", de:"Live-Support", fr:"Support en direct", es:"Soporte en vivo", it:"Supporto dal vivo" },
    "Mesajınızı yazın...": { en:"Type your message...", de:"Nachricht eingeben...", fr:"Saisissez votre message...", es:"Escribe tu mensaje...", it:"Scrivi il tuo messaggio..." }
  };

  // ── Çeviri motoru ──
  var SKIP_TAGS = { SCRIPT:1, STYLE:1, NOSCRIPT:1, IFRAME:1, TEXTAREA:1, CODE:1, PRE:1 };
  var ATTRS = ['placeholder', 'title', 'aria-label', 'alt', 'value'];

  function lookup(trimmed, lang) {
    var row = DICT[trimmed];
    if (!row) return null;
    var val = row[lang] || row.en;
    return (val && val !== trimmed) ? val : null;
  }

  function translateText(str, lang) {
    if (!str) return null;
    var trimmed = str.trim();
    if (!trimmed) return null;
    var repl = lookup(trimmed, lang);
    if (repl === null) return null;
    var lead = str.slice(0, str.indexOf(trimmed));
    var tail = str.slice(str.indexOf(trimmed) + trimmed.length);
    return lead + repl + tail;
  }

  function walk(root, lang) {
    var tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        var p = node.parentNode;
        if (!p || SKIP_TAGS[p.nodeName]) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var nodes = [], n;
    while ((n = tw.nextNode())) nodes.push(n);
    for (var i = 0; i < nodes.length; i++) {
      var out = translateText(nodes[i].nodeValue, lang);
      if (out !== null) nodes[i].nodeValue = out;
    }
    var elems = root.nodeType === 1 ? [root].concat([].slice.call(root.querySelectorAll('*')))
      : (root.querySelectorAll ? [].slice.call(root.querySelectorAll('*')) : []);
    for (var e = 0; e < elems.length; e++) {
      var el = elems[e];
      if (SKIP_TAGS[el.nodeName]) continue;
      for (var a = 0; a < ATTRS.length; a++) {
        var attr = ATTRS[a];
        if (!el.hasAttribute(attr)) continue;
        if (attr === 'value' && !(el.tagName === 'INPUT' && /^(submit|reset|button)$/i.test(el.type))) continue;
        var o = translateText(el.getAttribute(attr), lang);
        if (o !== null) el.setAttribute(attr, o);
      }
    }
  }

  var _mo = null;
  function startObserver(lang) {
    if (_mo) _mo.disconnect();
    _mo = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var m = muts[i];
        if (m.type === 'characterData') {
          var out = translateText(m.target.nodeValue, lang);
          if (out !== null) m.target.nodeValue = out;
        } else if (m.type === 'childList') {
          m.addedNodes.forEach(function (node) {
            if (node.nodeType === 1) walk(node, lang);
            else if (node.nodeType === 3) {
              var o = translateText(node.nodeValue, lang);
              if (o !== null) node.nodeValue = o;
            }
          });
        } else if (m.type === 'attributes') {
          var el = m.target;
          if (el.nodeType !== 1) continue;
          if (ATTRS.indexOf(m.attributeName) < 0) continue;
          var ov = translateText(el.getAttribute(m.attributeName), lang);
          if (ov !== null) el.setAttribute(m.attributeName, ov);
        }
      }
    });
    _mo.observe(document.body, {
      subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: ATTRS
    });
  }

  function applyI18n() {
    var lang = storeLang();
    document.documentElement.setAttribute('lang', lang);
    if (lang === 'tr' || !document.body) return; // kaynak dil; çeviri yok
    walk(document.body, lang);
    startObserver(lang);
  }

  // Dil değiştir: dil + para birimi kaydet, sayfayı yeniden yükle (temiz çeviri)
  function setLang(code) {
    if (LANGS.indexOf(code) < 0) return;
    try {
      localStorage.setItem('tt_lang', code);
      localStorage.setItem('tt_currency', code === 'tr' ? 'TRY' : 'EUR');
    } catch (e) {}
    location.reload();
  }

  // Dil seçici <select> üret ve sayfadaki #ttLangMount'a koy; yoksa sağ üste sabitle.
  function buildSwitcher() {
    if (document.getElementById('ttLangSelect')) return;
    var sel = document.createElement('select');
    sel.id = 'ttLangSelect';
    sel.setAttribute('aria-label', 'Language');
    for (var i = 0; i < LANGS.length; i++) {
      var o = document.createElement('option');
      o.value = LANGS[i];
      o.textContent = LANG_FLAGS[LANGS[i]] + ' ' + LANG_NAMES[LANGS[i]];
      if (LANGS[i] === storeLang()) o.selected = true;
      sel.appendChild(o);
    }
    sel.onchange = function () { setLang(this.value); };
    var base = 'padding:.35rem .5rem;border-radius:8px;border:1px solid rgba(128,128,128,.35);background:transparent;color:inherit;font:inherit;font-size:.85rem;cursor:pointer;';
    var mount = document.getElementById('ttLangMount');
    if (mount) {
      sel.style.cssText = base;
      mount.appendChild(sel);
    } else {
      sel.style.cssText = base + 'position:fixed;top:10px;right:10px;z-index:99999;background:#fff;color:#111;box-shadow:0 2px 8px rgba(0,0,0,.15);';
      (document.body || document.documentElement).appendChild(sel);
    }
  }

  function init() {
    applyI18n();
    buildSwitcher();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Diğer sekmede dil değişirse bu sekme de yenilensin
  window.addEventListener('storage', function (e) {
    if (e.key === 'tt_lang') location.reload();
  });

  // Global API (index.html gibi kendi mantığı olan sayfalar kullanabilir)
  global.TTI18n = {
    langs: LANGS, names: LANG_NAMES, flags: LANG_FLAGS,
    storeLang: storeLang, setLang: setLang, apply: applyI18n, dict: DICT
  };
})(window);
