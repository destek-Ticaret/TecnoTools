(function () {
  var h = location.hostname;
  if (h !== 'localhost' && h !== '127.0.0.1') {
    window.TT_API_BASE = 'https://api.tecnotools.org';
  }
})();
