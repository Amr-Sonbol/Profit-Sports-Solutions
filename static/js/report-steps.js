// Portal "New request" (customers/portal_ticket_new.html) in four steps:
// where, what they need, the details, check and send. One step shows at a
// time, with the bar above saying which; a step marked data-step-skip is
// passed over, and one with data-step-require="<radio name>" waits for a
// choice. The details show only what the chosen need asks for
// (data-show-topic="<topic> ..."). After a refused send, it opens on the
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
        if (scroll) {
            form.scrollIntoView({ block: 'start' });
        }
    }

    // The details for the chosen need only; with none chosen yet, all of them.
    function showTopic() {
        var checked = form.querySelector('input[name$="topic"]:checked');
        form.querySelectorAll('[data-show-topic]').forEach(function (element) {
            element.hidden = !!checked && element.getAttribute('data-show-topic').split(' ').indexOf(checked.value) === -1;
        });
    }

    function move(direction) {
        var required = steps[current].getAttribute('data-step-require');
        if (direction > 0 && required && !form.querySelector('input[name="' + required + '"]:checked')) {
            steps[current].querySelector('[data-step-required]').hidden = false;
            return;
        }
        var next = current + direction;
        while (steps[next] && steps[next].hasAttribute('data-step-skip')) {
            next += direction;
        }
        if (steps[next]) {
            show(next, true);
        }
    }

    form.querySelectorAll('input[name$="topic"]').forEach(function (radio) {
        radio.addEventListener('change', function () {
            form.querySelectorAll('[data-step-required]').forEach(function (note) { note.hidden = true; });
            showTopic();
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
