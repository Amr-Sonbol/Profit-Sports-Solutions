// Portal "New request" (customers/portal_ticket_new.html) in four steps:
// where, what it's about and what they need, the details, check and send. One step shows at a
// time, with the bar above saying which; a step marked data-step-skip is
// passed over, and one with data-step-require="<radio name>" waits for a
// choice. The details show only what the chosen pair asks for
// (data-show-about / -need / -mode, below). After a refused send, it opens on the
// first step with a mistake. Without this script the form is simply one
// page — nothing is lost.
(function () {
    var form = document.querySelector('[data-report-steps]');
    if (!form) {
        return;
    }
    var steps = Array.prototype.slice.call(form.querySelectorAll('[data-step]'));
    var labels = Array.prototype.slice.call(form.querySelectorAll('[data-step-label]'));
    var current = 0;

    function show(index, scroll) {
        current = index;
        steps.forEach(function (step, i) {
            step.hidden = i !== index;
        });
        labels.forEach(function (label, i) {
            label.classList.toggle('progress-steps__step--done', i < index);
            label.classList.toggle('progress-steps__step--current', i === index);
            if (i === index) {
                label.setAttribute('aria-current', 'step');
            } else {
                label.removeAttribute('aria-current');
            }
        });
        // The way back from the first step the customer actually sees is the page's own link.
        var firstShown = steps[0].hasAttribute('data-step-skip') ? 1 : 0;
        steps[index].querySelectorAll('[data-step-back]').forEach(function (button) {
            button.hidden = index === firstShown;
        });
        if (steps[index].querySelector('[data-request-summary]')) {
            writeSummary();
        }
        if (scroll) {
            form.scrollIntoView({ block: 'start' });
        }
    }

    // "Check and send": what they filled in, one part per row, each with a
    // pencil back to its own step. Labels come from the form itself, so
    // they're already in the customer's language; values go in as text.
    var summary = form.querySelector('[data-request-summary]');

    function shown(element) {
        return element && !element.closest('[hidden]');
    }

    // The field's label as shown: some boxes have one per request type, the others hidden.
    function labelOf(field) {
        var labels = field ? Array.prototype.slice.call(form.querySelectorAll('label[for="' + field.id + '"]')) : [];
        var label = labels.filter(function (each) { return !each.hidden; })[0] || labels[0];
        return label ? label.textContent.trim() : '';
    }

    function fileNames(input) {
        return input && input.files ? Array.prototype.map.call(input.files, function (file) { return file.name; }) : [];
    }

    function writeSummary() {
        var list = summary.querySelector('[data-summary-list]');
        list.textContent = '';
        function row(term, lines, field) {
            lines = lines.filter(function (line) { return line; });
            if (!lines.length) {
                return;
            }
            var item = document.createElement('div');
            item.className = 'request-summary__row';
            var dt = document.createElement('dt');
            dt.textContent = term;
            var dd = document.createElement('dd');
            dd.textContent = lines.join('\n');
            item.append(dt, dd);
            var step = steps.findIndex(function (each) { return each.contains(field); });
            if (step > -1) {
                var edit = summary.querySelector('[data-summary-edit]').content.firstElementChild.cloneNode(true);
                edit.addEventListener('click', function () { show(step, true); });
                item.append(edit);
            }
            list.append(item);
        }

        var site = form.querySelector('#id_site');
        if (site && site.selectedIndex > -1 && site.value) {
            row(labelOf(site), [site.options[site.selectedIndex].text], site);
        }
        var about = checkedValue('about', true);
        var need = checkedValue('need', true);
        if (about && need) {
            row(summary.getAttribute('data-label-type'), [about.closest('label').textContent.trim() + ' · '
                + need.closest('label').textContent.trim()], about);
        }
        form.querySelectorAll('[data-machine-card]').forEach(function (card) {
            if (!shown(card)) {
                return;
            }
            var lines = [];
            var asset = card.querySelector('select[name$="-asset"]');
            if (shown(asset) && asset.value && asset.value !== 'new') {
                lines.push(asset.options[asset.selectedIndex].text);
            }
            ['model_name', 'serial_no', 'code'].forEach(function (name) {
                var input = card.querySelector('[name$="-' + name + '"]');
                if (shown(input) && input.value.trim()) {
                    lines.push(labelOf(input) + ': ' + input.value.trim());
                }
            });
            var description = card.querySelector('textarea[name$="-description"]');
            if (description && description.value.trim()) {
                lines.push(description.value.trim());
            }
            card.querySelectorAll('input[type="file"]').forEach(function (input) {
                if (shown(input) && input.files.length) {
                    lines.push(labelOf(input) + ': ' + fileNames(input).join(', '));
                }
            });
            row(card.querySelector('legend').textContent.trim(), lines, card);
        });
        ['subject', 'message', 'attachments'].forEach(function (name) {
            var field = form.querySelector('#id_' + name);
            if (!shown(field)) {
                return;
            }
            row(labelOf(field), [field.type === 'file' ? fileNames(field).join(', ') : field.value.trim()], field);
        });
        summary.hidden = !list.children.length;
    }

    // The chosen answer to "what is it about?" or "what do you need?" — its
    // value, or (asInput) the radio itself; null until chosen.
    function checkedValue(name, asInput) {
        var input = form.querySelector('input[name$="' + name + '"]:checked');
        return asInput ? input : (input ? input.value : null);
    }

    // Show only what the pair asks for: data-show-about / data-show-need list
    // the answers an element is for, data-show-mode is "machines" (machines
    // with a problem or spare parts: the machine cards) or "message"
    // (everything else). Hidden until its question is answered.
    function showTopic() {
        var about = checkedValue('about');
        var need = checkedValue('need');
        var mode = about && need
            ? (about === 'machines' && (need === 'repair' || need === 'parts') ? 'machines' : 'message') : null;
        var answers = { 'data-show-about': about, 'data-show-need': need, 'data-show-mode': mode };
        Object.keys(answers).forEach(function (attribute) {
            form.querySelectorAll('[' + attribute + ']').forEach(function (element) {
                element.hidden = !answers[attribute]
                    || element.getAttribute(attribute).split(' ').indexOf(answers[attribute]) === -1;
            });
        });
        // Spare parts: the machine card's text box gets its own example.
        form.querySelectorAll('[data-parts-placeholder]').forEach(function (wrapper) {
            var box = wrapper.querySelector('textarea');
            if (!box.hasAttribute('data-problem-placeholder')) {
                box.setAttribute('data-problem-placeholder', box.placeholder);
            }
            box.placeholder = need === 'parts'
                ? wrapper.getAttribute('data-parts-placeholder') : box.getAttribute('data-problem-placeholder');
        });
    }

    // Flooring, Accessories, Lockers, Something else: the short "what" box under the choices.
    var other = form.querySelector('[data-topic-other]');

    function move(direction) {
        var required = (steps[current].getAttribute('data-step-require') || '').split(' ').filter(Boolean);
        var unanswered = required.some(function (name) {
            return !form.querySelector('input[name="' + name + '"]:checked');
        });
        if (direction > 0 && unanswered) {
            steps[current].querySelector('[data-step-required]').hidden = false;
            return;
        }
        if (direction > 0 && other && steps[current].contains(other) && !other.hidden) {
            var subject = other.querySelector('input');
            var missing = !subject.value.trim();
            other.querySelector('[data-topic-other-required]').hidden = !missing;
            if (missing) {
                subject.focus();
                return;
            }
        }
        var next = current + direction;
        while (steps[next] && steps[next].hasAttribute('data-step-skip')) {
            next += direction;
        }
        if (steps[next]) {
            show(next, true);
        }
    }

    form.querySelectorAll('input[name$="about"], input[name$="need"]').forEach(function (radio) {
        radio.addEventListener('change', function () {
            form.querySelectorAll('[data-step-required]').forEach(function (note) { note.hidden = true; });
            showTopic();
            if (other && !other.hidden && /about$/.test(radio.name)) {
                other.querySelector('input').focus();
            }
        });
    });
    showTopic();

    form.querySelectorAll('[data-steps-bar], [data-steps-nav]').forEach(function (element) {
        element.hidden = false;
    });
    form.querySelectorAll('[data-step-next]').forEach(function (button) {
        button.addEventListener('click', function () { move(1); });
    });
    form.querySelectorAll('[data-step-back]').forEach(function (button) {
        button.addEventListener('click', function () { move(-1); });
    });

    var withError = steps.findIndex(function (step) {
        return step.querySelector('.field--error, .field__error:not([data-step-required])');
    });
    if (withError > -1) {
        show(withError);
    } else {
        show(steps[0].hasAttribute('data-step-skip') ? 1 : 0);
    }
})();
