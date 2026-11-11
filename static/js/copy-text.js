// A "Copy" button for the text in the element its data-copy names: copies it,
// says "Copied" for a moment; where the browser refuses, selects the text instead.
document.querySelectorAll('[data-copy]').forEach((button) => {
  button.addEventListener('click', () => {
    const source = document.getElementById(button.dataset.copy);
    const text = 'value' in source ? source.value : source.textContent;
    const label = button.textContent;
    const done = () => {
      button.textContent = button.dataset.copied;
      setTimeout(() => { button.textContent = label; }, 2000);
    };
    const select = () => {
      if (source.select) source.select();
      else window.getSelection().selectAllChildren(source);
    };
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, select);
    else select();
  });
});
