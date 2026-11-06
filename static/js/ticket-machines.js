// Portal ticket form (customers/portal_ticket_new.html): the machine cards.
// All of them are on the page; the empty ones stay hidden until "Add
// another machine" shows the next. The button simply goes away once the
// last card is showing — the limit itself is never mentioned. While more
// than one card is open, each has an X: it empties that card (so the
// server skips it), hides it and moves it to the back, and the open cards
// are numbered again 1, 2, 3.
//
// Each card's machine list is narrowed to the chosen site, so a customer
// with several sites only sees the machines at the one they picked. Each
// card's dropdown ends with "My machine isn't listed", which shows the
// fields for a new machine's details.
(function () {
    var cards = Array.prototype.slice.call(document.querySelectorAll('[data-machine-card]'));
    var addButton = document.querySelector('[data-add-machine]');
    var siteSelect = document.getElementById('id_site');
    if (!cards.length || !addButton) {
        return;
    }

    var container = cards[0].parentNode;
    var legends = cards.map(function (card) { return card.querySelector('legend').textContent; });

    function refreshButton() {
        // Cards in page order: a closed one has moved to the back.
        cards = Array.prototype.slice.call(container.querySelectorAll('[data-machine-card]'));
        var open = cards.filter(function (card) { return !card.hidden; });
        addButton.hidden = open.length === cards.length;
        cards.forEach(function (card, i) {
            card.querySelector('legend').textContent = legends[i];
            card.querySelector('[data-remove-machine]').hidden = open.length < 2;
        });
    }

    // Empty, as if never opened: the server skips a card with nothing in it.
    function emptyCard(card) {
        Array.prototype.forEach.call(card.querySelectorAll('input, select, textarea'), function (field) {
            if (field.type === 'hidden') {
                return;
            }
            if (field.type === 'radio') {
                // Back to the first answer, the form's own default.
                field.checked = field === card.querySelector('input[type="radio"][name="' + field.name + '"]');
            } else if (field.type === 'checkbox') {
                field.checked = false;
            } else if (field.tagName === 'SELECT') {
                field.selectedIndex = 0;
            } else {
                field.value = '';
            }
        });
        Array.prototype.forEach.call(card.querySelectorAll('.field--error'), function (field) {
            field.classList.remove('field--error');
        });
        Array.prototype.forEach.call(card.querySelectorAll('.field__error'), function (error) {
            error.remove();
        });
        showForAnswer(card);
    }

    cards.forEach(function (card) {
        card.querySelector('[data-remove-machine]').addEventListener('click', function () {
            emptyCard(card);
            card.hidden = true;
            container.appendChild(card);
            refreshButton();
            addButton.focus();
        });
    });

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

    // Which machine: one of theirs, or "My machine isn't listed" (the
    // dropdown's "new"), which asks for the model, serial and sticker photo.
    // A customer with no machines has no dropdown: always a new one.
    function showForAnswer(card) {
        var select = card.querySelector('select[name$="-asset"]');
        var noMachines = card.querySelector('input[type="hidden"][name$="-which"][value="new"]');
        var answer = noMachines || (select && select.value === 'new') ? 'new' : 'list';
        Array.prototype.forEach.call(card.querySelectorAll('[data-show-for]'), function (field) {
            field.hidden = field.getAttribute('data-show-for') !== answer;
        });
    }

    cards.forEach(function (card) {
        var select = card.querySelector('select[name$="-asset"]');
        if (select) {
            select.addEventListener('change', function () { showForAnswer(card); });
        }
        showForAnswer(card);
    });

    if (siteSelect) {
        siteSelect.addEventListener('change', filterMachines);
        filterMachines();
    }
    refreshButton();
})();
