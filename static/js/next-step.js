// Task page: "Create the next step" — the "Where" list shows only the
// sites for the chosen step (our warehouses for loading, customers' sites
// for an installation), and picks the first of them.
(function () {
    var step = document.querySelector('[data-next-step]');
    var site = document.getElementById('next-site');
    if (!step || !site) {
        return;
    }
    function show() {
        var first = null;
        Array.prototype.forEach.call(site.querySelectorAll('optgroup'), function (group) {
            var on = group.getAttribute('data-for-step') === step.value;
            group.hidden = !on;
            group.disabled = !on;
            if (on && !first) {
                first = group.querySelector('option');
            }
        });
        if (first && (!site.selectedOptions[0] || site.selectedOptions[0].parentNode.disabled)) {
            site.value = first.value;
        }
    }
    step.addEventListener('change', show);
    show();
})();
