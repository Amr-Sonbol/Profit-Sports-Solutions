// Portal Settings (customers/portal_settings.html): the customer's portal
// color. The full picker and the typed code (#RRGGBB, the field that's
// saved) stay in step. Without this script, the code box alone works.
(function () {
    var box = document.querySelector('[data-color-choice]');
    if (!box) {
        return;
    }
    var code = box.querySelector('[data-color-code]');
    var native = box.querySelector('[data-color-native]');

    // "0B1F4D" or "#0b1f4d" -> "#0b1f4d"; anything else -> null.
    function normalise(value) {
        value = (value || '').trim().toLowerCase();
        if (value && value.charAt(0) !== '#') {
            value = '#' + value;
        }
        return /^#[0-9a-f]{6}$/.test(value) ? value : null;
    }


    function show(color, from) {
        if (from !== code) {
            code.value = color;
        }
        if (from !== native) {
            native.value = color;
        }
    }

    native.addEventListener('input', function () { show(native.value, native); });
    code.addEventListener('input', function () {
        var color = normalise(code.value);
        if (color) {
            show(color, code);
        }
    });

    show(normalise(code.value) || native.value, code);
})();
