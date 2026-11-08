// Photos shrink on the phone before they upload: about 2000 px on the long
// side, as a normal JPEG (sharp enough to read a serial plate, ~10x smaller,
// so uploads are quick on mobile data and the server keeps less). A file the
// browser can't read, a GIF, or anything that goes wrong: the original goes
// as before. A file over the server's limit (a long video) is refused here,
// with a message, instead of after a long upload. Every file input on the site.
(() => {
  const MAX_SIDE = 2000;
  const QUALITY = 0.85;
  const MAX_BYTES = 25 * 1024 * 1024; // tasks.models.MAX_TICKET_ATTACHMENT_BYTES
  const arabic = document.documentElement.lang === 'ar';
  const tooBigText = (name) => (arabic
    ? `الملف «${name}» أكبر من 25 ميغابايت. يرجى إرسال فيديو أقصر.`
    : `“${name}” is larger than 25 MB. Please send a shorter video.`);
  const busy = new WeakSet();

  async function shrink(file) {
    if (!file.type.startsWith('image/') || file.type === 'image/gif' || typeof createImageBitmap !== 'function') {
      return file;
    }
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
      const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(bitmap.width * scale);
      canvas.height = Math.round(bitmap.height * scale);
      canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      if (bitmap.close) bitmap.close();
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', QUALITY));
      if (!blob || blob.size >= file.size) return file;
      const name = `${file.name.replace(/\.[^.]+$/, '')}.jpg`;
      return new File([blob], name, { type: 'image/jpeg', lastModified: file.lastModified });
    } catch {
      return file;
    }
  }

  function showProblems(input, names) {
    const holder = input.closest('.field') || input.parentElement;
    holder.querySelectorAll('.js-file-problem').forEach((node) => node.remove());
    names.forEach((name) => {
      const note = document.createElement('p');
      note.className = 'field__error js-file-problem';
      note.textContent = tooBigText(name);
      input.insertAdjacentElement('afterend', note);
    });
  }

  document.addEventListener('change', async (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== 'file' || !input.files.length) return;
    if (typeof DataTransfer === 'undefined') return;
    busy.add(input);
    try {
      const files = await Promise.all([...input.files].map(shrink));
      const kept = files.filter((file) => file.size <= MAX_BYTES);
      showProblems(input, files.filter((file) => file.size > MAX_BYTES).map((file) => file.name));
      const list = new DataTransfer();
      kept.forEach((file) => list.items.add(file));
      input.files = list.files;
    } finally {
      busy.delete(input);
    }
  });

  // Not sent while photos are still being made smaller (a second or two).
  document.addEventListener('submit', (event) => {
    const waiting = [...event.target.querySelectorAll('input[type="file"]')].some((input) => busy.has(input));
    if (waiting) {
      event.preventDefault();
      setTimeout(() => event.target.requestSubmit(event.submitter), 400);
    }
  });
})();
