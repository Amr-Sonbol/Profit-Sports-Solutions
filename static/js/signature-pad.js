// The work report's on-screen signature pad. Progressive enhancement:
// the pad stays hidden without JavaScript, and the plain photo upload
// next to it works either way. A drawn signature is sent as a PNG data
// URL in the hidden signature_drawn field — the same field the mobile
// app fills — and WorkReportForm turns it into the signature file.
(function () {
  var pad = document.querySelector('[data-signature-pad]');
  if (!pad) {
    return;
  }
  var form = pad.closest('form');
  var canvas = pad.querySelector('canvas');
  var hiddenInput = form.querySelector('input[name="signature_drawn"]');
  var context = canvas.getContext('2d');
  var drawing = false;
  var hasDrawn = false;

  pad.hidden = false;

  function resetCanvas() {
    // Sized from the element's own width so it fills the field on any
    // screen, scaled for high-DPI displays so the line stays sharp.
    var ratio = window.devicePixelRatio || 1;
    canvas.width = canvas.offsetWidth * ratio;
    canvas.height = canvas.offsetHeight * ratio;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, canvas.offsetWidth, canvas.offsetHeight);
    context.strokeStyle = '#000000';
    context.lineWidth = 2;
    context.lineCap = 'round';
    context.lineJoin = 'round';
    hasDrawn = false;
  }

  function point(event) {
    var rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  canvas.addEventListener('pointerdown', function (event) {
    drawing = true;
    hasDrawn = true;
    canvas.setPointerCapture(event.pointerId);
    var p = point(event);
    context.beginPath();
    context.moveTo(p.x, p.y);
    // A single tap still leaves a dot.
    context.lineTo(p.x + 0.1, p.y + 0.1);
    context.stroke();
  });

  canvas.addEventListener('pointermove', function (event) {
    if (!drawing) {
      return;
    }
    var p = point(event);
    context.lineTo(p.x, p.y);
    context.stroke();
  });

  function stop() {
    drawing = false;
  }
  canvas.addEventListener('pointerup', stop);
  canvas.addEventListener('pointercancel', stop);

  pad.querySelector('[data-signature-clear]').addEventListener('click', resetCanvas);

  form.addEventListener('submit', function () {
    hiddenInput.value = hasDrawn ? canvas.toDataURL('image/png') : '';
  });

  resetCanvas();
})();
