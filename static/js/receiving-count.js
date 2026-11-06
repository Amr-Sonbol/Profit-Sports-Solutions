// Receiving checklist: "The rest" (missing or damaged) only matters when
// fewer arrived than listed, so it shows only then. Without this script
// it's always shown, and the server ignores it unless the count is short.
(function () {
    Array.prototype.forEach.call(document.querySelectorAll('.checklist-card[data-listed]'), function (card) {
        var status = card.querySelector('[data-field="status"] select');
        var received = card.querySelector('[data-field="received"] input');
        var rest = card.querySelector('[data-field="rest_status"]');
        if (!status || !received || !rest) {
            return;
        }
        var listed = Number(card.getAttribute('data-listed'));
        function show() {
            rest.hidden = !(status.value === 'done' && received.value !== '' && Number(received.value) < listed);
        }
        status.addEventListener('change', show);
        received.addEventListener('input', show);
        show();
    });
})();
