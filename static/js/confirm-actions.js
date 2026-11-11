// "Are you sure?" before a step that changes a task: any form (or the
// button pressed) with a data-confirm message asks it first — a mistaken tap on "I have arrived"
// or "Start work" would record the wrong time. Runs before every other
// submit handler (capture), so Cancel also stops what they'd do (a form's
// location, static/js/tap-location.js). Every page.
document.addEventListener('submit', (event) => {
  const button = event.submitter;
  const message = (button && button.dataset.confirm) || (event.target.dataset && event.target.dataset.confirm);
  if (message && !window.confirm(message)) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }
}, true);
