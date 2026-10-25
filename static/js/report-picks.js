// The report form's smart choices, like the app's: a pick fills in what
// follows from it, unless already chosen. "No fault found" means no action
// and "Inspected OK"; a replacement means "Replaced"; a repair action
// means "Repaired". Works on both machine tables (existing-N-, new-N-).
(function () {
    var REPLACED = ['part_replaced', 'cable_replaced', 'belt_replaced', 'console_replaced'];
    var REPAIRED = ['adjusted', 'greased', 'tightened', 'belt_adjusted', 'calibrated'];

    function sibling(select, field) {
        var name = select.name.replace(/[a-z_]+$/, field);
        return select.form.elements[name];
    }

    function fill(select, value) {
        if (select && !select.value) {
            select.value = value;
        }
    }

    document.addEventListener('change', function (event) {
        var select = event.target;
        if (!select.name) {
            return;
        }
        if (/-finding_code$/.test(select.name) && select.value === 'no_fault') {
            fill(sibling(select, 'action_code'), 'none_needed');
            fill(sibling(select, 'outcome'), 'inspected_ok');
        } else if (/-action_code$/.test(select.name)) {
            if (REPLACED.indexOf(select.value) !== -1) {
                fill(sibling(select, 'outcome'), 'replaced');
            } else if (REPAIRED.indexOf(select.value) !== -1) {
                fill(sibling(select, 'outcome'), 'repaired');
            }
        }
    });
})();
