// "Answer without a visit": choosing a reason fills in its suggested answer
// (the option's data-suggestion) — unless the answer has been written or
// edited by hand, which is never overwritten.
document.querySelectorAll('[data-answer-reason]').forEach((select) => {
  const answer = select.form && select.form.querySelector('[data-answer-text]');
  if (!answer) {
    return;
  }
  let suggested = '';
  select.addEventListener('change', () => {
    const option = select.selectedOptions[0];
    const next = (option && option.dataset.suggestion) || '';
    if (answer.value.trim() === '' || answer.value === suggested) {
      answer.value = next;
      suggested = next;
    }
  });
});
