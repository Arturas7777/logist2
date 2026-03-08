(function(){
  document.addEventListener('htmx:responseError', function(event) {
    try {
      console.error('HTMX response error:', event.detail.xhr.status, event.detail.xhr.response);
    } catch (e) {}
  });
})();
