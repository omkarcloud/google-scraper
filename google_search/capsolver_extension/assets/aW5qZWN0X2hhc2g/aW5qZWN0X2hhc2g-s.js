(function () {
  window.addEventListener('message', function (event) {
    if (!['capsolverCallback', 'capsolverDetectedCallback', 'capsolverFailedCallback'].includes(event.data.type))
      return;
    window[event.data.callback] && window[event.data.callback](event.data?.data);
  });
})();
