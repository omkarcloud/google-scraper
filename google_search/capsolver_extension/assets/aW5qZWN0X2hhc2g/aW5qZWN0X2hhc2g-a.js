var domain = 'awswaf.com';
var alList = [
  '/problem',
  '/verify',
];

(function () {
  var origFetch = window.fetch;
  window.fetch = async function (...args) {
    var _url = args[0];
    var response = await origFetch(...args);

    response
      .clone()
      .blob()
      .then(async data => {
        if (_url.indexOf(domain) === -1) return;
        
        const domainIndex = _url.indexOf(domain);
        const isInList = alList.some(url => {
          if (_url.indexOf(url) === -1) return false;
          const urlIndex = _url.indexOf(url);

          if (domainIndex > urlIndex) return false;

          return true;
        });
        if (isInList) {
          window.postMessage(
            {
              type: 'fetch',
              data: await data.text(),
              url: _url,
            },
            '*',
          );
        }
      })
      .catch(err => {
        console.log(err);
      });

    return response;
  };
})();
