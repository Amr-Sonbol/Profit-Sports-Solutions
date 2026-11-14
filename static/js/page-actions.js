// Small page behaviours that used to be inline attributes (onchange=,
// onclick=) — the Content-Security-Policy (spots.middleware) runs no inline
// script without its nonce, so they live here. Every page.
//
// data-auto-submit on a select: choosing an option sends its form (the
//   header's country switch).
// data-history-back on a link: back where the person came from, or the
//   link's own address when there's no history (the page's Back link).
document.addEventListener('change', (event) => {
  const field = event.target;
  if (field instanceof HTMLSelectElement && field.hasAttribute('data-auto-submit') && field.form) {
    field.form.submit();
  }
});

document.addEventListener('click', (event) => {
  const link = event.target instanceof Element && event.target.closest('a[data-history-back]');
  if (link && history.length > 1) {
    event.preventDefault();
    history.back();
  }
});

// A form sends once: right after it's sent, its buttons wait ("is-sending") so a
// double click — or a second press on a slow connection — never sends it twice.
// Not a form some script stopped (a confirm, a check), nor one opening elsewhere;
// back on the page (the browser's Back), the buttons work again.
document.addEventListener('submit', (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== 'post' || form.target) return;
  setTimeout(() => {
    if (event.defaultPrevented) return;
    form.querySelectorAll('button:not([type="button"]), input[type="submit"]').forEach((button) => {
      button.disabled = true;
      button.classList.add('is-sending');
    });
  }, 0);
});

window.addEventListener('pageshow', () => {
  document.querySelectorAll('.is-sending').forEach((button) => {
    button.disabled = false;
    button.classList.remove('is-sending');
  });
});
