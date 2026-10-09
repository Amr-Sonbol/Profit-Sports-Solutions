// Sends where the phone is along with a technician's tap (forms marked
// data-locate) — checked against the site by tasks.location. Only taken
// at the tap itself. If location is off, denied or slow, the tap still
// goes through; it's just recorded as "No location sent".
(function () {
  'use strict';

  var TIMEOUT_MS = 10000;

  function addField(form, name, value) {
    var input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = value;
    form.appendChild(input);
  }

  document.querySelectorAll('form[data-locate]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      if (form.dataset.located || !navigator.geolocation) {
        return;
      }
      event.preventDefault();
      var sent = false;
      function send() {
        if (sent) {
          return;
        }
        sent = true;
        form.dataset.located = '1';
        form.submit();
      }
      // A backstop in case the browser never answers at all.
      setTimeout(send, TIMEOUT_MS + 1000);
      navigator.geolocation.getCurrentPosition(
        function (position) {
          addField(form, 'latitude', position.coords.latitude);
          addField(form, 'longitude', position.coords.longitude);
          addField(form, 'accuracy', Math.round(position.coords.accuracy));
          send();
        },
        send,
        { enableHighAccuracy: true, timeout: TIMEOUT_MS, maximumAge: 60000 }
      );
    });
  });
})();
