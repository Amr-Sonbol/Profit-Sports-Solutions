// Message boxes (templates/_composer.html): the paperclip adds files —
// several at once, or one after another — and each chosen file shows as a
// chip with its name and an × to take it off again before sending. The
// chosen files live in the file input itself (a DataTransfer), so the form
// sends exactly what the chips show. Without this script the browser's own
// file picker still works, one choice at a time.
(function () {
    if (typeof DataTransfer === 'undefined') {
        return;
    }
    Array.prototype.forEach.call(document.querySelectorAll('.composer'), function (composer) {
        // Two boxes on a page load this twice: each box is set up once.
        if (composer.dataset.ready) {
            return;
        }
        composer.dataset.ready = '1';
        var input = composer.querySelector('.composer__attach input[type="file"]');
        var list = composer.querySelector('[data-composer-files]');
        if (!input || !list) {
            return;
        }
        var chosen = new DataTransfer();

        function render() {
            list.innerHTML = '';
            Array.prototype.forEach.call(chosen.files, function (file, index) {
                var chip = document.createElement('li');
                chip.className = 'attachment';
                var name = document.createElement('span');
                name.className = 'attachment__name';
                name.dir = 'auto';
                name.textContent = file.name;
                var remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'attachment__remove';
                remove.textContent = '×';
                remove.setAttribute('aria-label', remove.title = composer.dataset.removeLabel || 'Remove');
                remove.addEventListener('click', function () {
                    var kept = new DataTransfer();
                    Array.prototype.forEach.call(chosen.files, function (other, i) {
                        if (i !== index) {
                            kept.items.add(other);
                        }
                    });
                    chosen = kept;
                    input.files = chosen.files;
                    render();
                });
                chip.appendChild(name);
                chip.appendChild(remove);
                list.appendChild(chip);
            });
            list.hidden = chosen.files.length === 0;
        }

        input.addEventListener('change', function () {
            // Add the new picks to those already chosen, once each.
            Array.prototype.forEach.call(input.files, function (file) {
                var already = Array.prototype.some.call(chosen.files, function (other) {
                    return other.name === file.name && other.size === file.size;
                });
                if (!already) {
                    chosen.items.add(file);
                }
            });
            input.files = chosen.files;
            render();
        });
    });
})();
