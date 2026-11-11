// Every file box on the site (base.html loads this once).
//
// Photos shrink on the phone before they upload: about 2000 px on the long
// side, as a normal JPEG (sharp enough to read a serial plate, ~10x smaller,
// so uploads are quick on mobile data). A file the browser can't read, a
// GIF, or anything that goes wrong: the original goes as it is. A file over
// the server's limit (a long video) is refused here, with its name, instead
// of after a long upload.
//
// A box that takes several files (`multiple`) keeps adding: each pick goes
// with the ones already chosen, once each, up to 10 (data-max-files). Each
// chosen file shows under the box (a photo as a thumbnail) with a red X in a
// box to take just that one back, and "3 of 10". The files live in the input
// itself, so the form sends exactly what's shown. They're also kept in the
// browser (IndexedDB) until the page is next opened: if the form comes back
// with an error, they're put back; opened without one, they're forgotten.
//
// Without this script the browser's own picker still works, one pick at a time.
(() => {
  if (typeof DataTransfer === 'undefined') return;

  const MAX_SIDE = 2000;
  const QUALITY = 0.85;
  const MAX_BYTES = 25 * 1024 * 1024; // tasks.models.MAX_TICKET_ATTACHMENT_BYTES
  const MAX_FILES = 10; // tasks.models.MAX_TICKET_ATTACHMENT_COUNT
  const arabic = document.documentElement.lang === 'ar';
  const text = {
    tooBig: (name) => (arabic
      ? `الملف «${name}» أكبر من 25 ميغابايت. يرجى إرسال فيديو أقصر.`
      : `“${name}” is larger than 25 MB. Please send a shorter video.`),
    tooMany: (max) => (arabic ? `${max} ملفات كحد أقصى.` : `At most ${max} files.`),
    count: (n, max) => (arabic ? `${n} من ${max}` : `${n} of ${max}`),
    remove: arabic ? 'إزالة' : 'Remove',
  };
  const chosen = new WeakMap(); // input → File[]
  const lists = new WeakMap(); // input → its <ul>
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

  function showProblems(input, problems) {
    const holder = input.closest('.field, .composer') || input.parentElement;
    holder.querySelectorAll('.js-file-problem').forEach((node) => node.remove());
    problems.forEach((problem) => {
      const note = document.createElement('p');
      note.className = 'field__error js-file-problem';
      note.textContent = problem;
      (lists.get(input) || input).insertAdjacentElement('afterend', note);
    });
  }

  // --- Kept in the browser until the page is next opened (IndexedDB) ---
  // This page, this form (its address and hidden `action`, when a page has several), this box.
  function storeKey(input) {
    const form = input.form;
    const action = form && form.querySelector('input[type="hidden"][name="action"]');
    return [location.pathname, form ? form.getAttribute('action') || '' : '', action ? action.value : '', input.name].join('|');
  }

  function database() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open('spots-file-picks', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('picks');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async function keep(input, files) {
    try {
      const db = await database();
      db.transaction('picks', 'readwrite').objectStore('picks').put(files, storeKey(input));
    } catch { /* private window, storage off: nothing kept, nothing lost */ }
  }

  async function takeBack(input) {
    try {
      const db = await database();
      const store = db.transaction('picks', 'readwrite').objectStore('picks');
      const files = await new Promise((resolve) => {
        const request = store.get(storeKey(input));
        request.onsuccess = () => resolve(request.result || []);
        request.onerror = () => resolve([]);
      });
      store.delete(storeKey(input));
      return files;
    } catch {
      return [];
    }
  }

  // --- What's chosen, under the box ---
  function listFor(input) {
    if (!lists.has(input)) {
      // A message box has its own place for them (templates/_composer.html).
      const own = input.closest('.composer') && input.closest('.composer').querySelector('[data-composer-files]');
      const list = own || document.createElement('ul');
      list.classList.add('file-picks');
      if (!own) input.insertAdjacentElement('afterend', list);
      lists.set(input, list);
    }
    return lists.get(input);
  }

  function render(input) {
    const files = chosen.get(input) || [];
    const max = Number(input.dataset.maxFiles) || MAX_FILES;
    const list = listFor(input);
    list.querySelectorAll('img').forEach((img) => URL.revokeObjectURL(img.src));
    list.innerHTML = '';
    files.forEach((file, index) => {
      const item = document.createElement('li');
      item.className = 'file-picks__item';
      if (file.type.startsWith('image/')) {
        const img = document.createElement('img');
        img.src = URL.createObjectURL(file);
        img.alt = '';
        item.appendChild(img);
      }
      const name = document.createElement('span');
      name.className = 'file-picks__name';
      name.dir = 'auto';
      name.textContent = file.name;
      item.appendChild(name);
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'file-picks__remove';
      remove.title = text.remove;
      remove.setAttribute('aria-label', `${text.remove}: ${file.name}`);
      remove.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        + 'stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"></line>'
        + '<line x1="6" y1="6" x2="18" y2="18"></line></svg>';
      remove.addEventListener('click', () => set(input, files.filter((_, other) => other !== index)));
      item.appendChild(remove);
      list.appendChild(item);
    });
    if (files.length) {
      const count = document.createElement('li');
      count.className = 'file-picks__count';
      count.textContent = text.count(files.length, max);
      list.appendChild(count);
    }
    list.hidden = files.length === 0;
  }

  function set(input, files) {
    chosen.set(input, files);
    const transfer = new DataTransfer();
    files.forEach((file) => transfer.items.add(file));
    input.files = transfer.files;
    render(input);
    keep(input, files);
  }

  const same = (a, b) => a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;

  document.addEventListener('change', async (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== 'file') return;
    busy.add(input);
    try {
      const picked = await Promise.all([...input.files].map(shrink));
      const problems = picked.filter((file) => file.size > MAX_BYTES).map((file) => text.tooBig(file.name));
      const fitting = picked.filter((file) => file.size <= MAX_BYTES);
      if (!input.multiple) {
        const transfer = new DataTransfer();
        fitting.forEach((file) => transfer.items.add(file));
        input.files = transfer.files;
        showProblems(input, problems);
        return;
      }
      // Several: the new picks go with those already chosen, once each.
      const max = Number(input.dataset.maxFiles) || MAX_FILES;
      const files = [...(chosen.get(input) || [])];
      fitting.forEach((file) => {
        if (files.some((other) => same(other, file))) return;
        if (files.length < max) files.push(file);
        else if (!problems.includes(text.tooMany(max))) problems.push(text.tooMany(max));
      });
      set(input, files);
      showProblems(input, problems);
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

  // Opened again: its form back with an error, the files chosen before are put back; otherwise forgotten.
  const ERRORS = '.field__error, .field--error, .flash--error, .composer__box--error';
  document.querySelectorAll('input[type="file"][multiple]').forEach(async (input) => {
    const files = await takeBack(input);
    const cameBackWithAnError = input.form && input.form.querySelector(ERRORS);
    if (cameBackWithAnError && files.length && !(chosen.get(input) || []).length) set(input, files);
  });
})();
