// Portal ticket form (customers/portal_ticket_new.html): the machine cards.
// All of them are on the page; the empty ones stay hidden until "Add
// another machine" shows the next. The button simply goes away once the
// last card is showing — the limit itself is never mentioned.
//
// Each card's machine list is narrowed to the chosen site, so a customer
// with several sites only sees the machines at the one they picked.
(function () {
    var cards = Array.prototype.slice.call(document.querySelectorAll('[data-machine-card]'));
    var addButton = document.querySelector('[data-add-machine]');
    var siteSelect = document.getElementById('id_site');
    if (!cards.length || !addButton) {
        return;
    }

    function refreshButton() {
        addButton.hidden = !cards.some(function (card) { return card.hidden; });
    }

    addButton.addEventListener('click', function () {
        var next = cards.find(function (card) { return card.hidden; });
        if (next) {
            next.hidden = false;
            var first = next.querySelector('select, input, textarea');
            if (first) {
                first.focus();
            }
        }
        refreshButton();
    });

    function filterMachines() {
        var siteName = siteSelect && siteSelect.selectedIndex > -1 && siteSelect.value
            ? siteSelect.options[siteSelect.selectedIndex].text
            : null;
        cards.forEach(function (card) {
            var select = card.querySelector('select[name$="-asset"]');
            if (!select) {
                return;
            }
            Array.prototype.forEach.call(select.querySelectorAll('optgroup'), function (group) {
                var show = !siteName || group.label === siteName;
                group.hidden = !show;
                group.disabled = !show;
                if (!show && group.contains(select.selectedOptions[0])) {
                    select.value = '';
                }
            });
        });
    }

    if (siteSelect) {
        siteSelect.addEventListener('change', filterMachines);
        filterMachines();
    }
    refreshButton();
})();
