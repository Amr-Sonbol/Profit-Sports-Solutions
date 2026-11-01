// Report form, "Parts used": each row shows the chosen part's catalogue
// photo, so the technician can check it's the right part. The photos come
// from the parts list's options (data-photo).
(function () {
    var list = document.getElementById('parts-catalogue');
    if (!list) {
        return;
    }
    var photos = {};
    Array.prototype.forEach.call(list.querySelectorAll('option[data-photo]'), function (option) {
        photos[option.value.toUpperCase()] = option.getAttribute('data-photo');
    });
    Array.prototype.forEach.call(document.querySelectorAll('input[list="parts-catalogue"]'), function (input) {
        var row = input.closest('tr');
        var img = row && row.querySelector('[data-part-photo]');
        if (!img) {
            return;
        }
        function show() {
            var url = photos[input.value.trim().toUpperCase()];
            img.hidden = !url;
            if (url) {
                img.src = url;
            }
        }
        input.addEventListener('input', show);
        show();
    });
})();
