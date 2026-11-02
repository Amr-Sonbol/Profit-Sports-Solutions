// Portal "Report a problem" (customers/portal_ticket_new.html) in three
// steps: where, what's wrong, check and send. One step shows at a time,
// with the bar above saying which; a step the customer doesn't need (the
// site, when they have only one) is passed over. After a refused send, it
// opens on the first step with a mistake. Without this script the form is
// simply one page — nothing is lost.
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

    function move(direction) {
        var next = current + direction;
        while (steps[next] && steps[next].hasAttribute('data-step-skip')) {
            next += direction;
        }
        if (steps[next]) {
            show(next, true);
        }
    }

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
        return step.querySelector('.field--error, .field__error');
    });
    if (withError > -1) {
        show(withError);
    } else {
        show(steps[0].hasAttribute('data-step-skip') ? 1 : 0);
    }
})();
