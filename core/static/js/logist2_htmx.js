// Minimal HTMX helpers and logging used across admin pages
(function(){
  console.log('logist2_htmx.js loaded');
  document.addEventListener('htmx:afterRequest', function(event) {
    try {
      console.log('HTMX request completed:', event.detail.xhr.status, event.detail.xhr.responseURL);
    } catch (e) {}
  });
  document.addEventListener('htmx:responseError', function(event) {
    try {
      console.error('HTMX response error:', event.detail.xhr.status, event.detail.xhr.response);
    } catch (e) {}
  });
})();


