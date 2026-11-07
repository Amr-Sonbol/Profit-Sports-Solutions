// The bell, fresh when someone comes back: after at least 30 seconds on
// another tab or window, it asks once for the bell (/bell/) and swaps it in.
// Nothing at all while they stay on the page or the tab sits in the
// background, so an open page costs the database nothing.
(() => {
  const AWAY_MS = 30000;
  let leftAt = null;

  const left = () => { if (leftAt === null) leftAt = Date.now(); };

  async function cameBack() {
    if (leftAt === null) return;
    const away = Date.now() - leftAt;
    leftAt = null;
    const slot = document.getElementById('bell');
    // Not when it's open in front of them: it would close under their hand.
    if (!slot || away < AWAY_MS || slot.querySelector('.notif-toggle')?.checked) return;
    try {
      const response = await fetch(slot.dataset.url, { credentials: 'same-origin' });
      if (response.status !== 200) return;
      const holder = document.createElement('div');
      holder.innerHTML = await response.text();
      const fresh = holder.querySelector('#bell');
      if (fresh) slot.replaceWith(fresh);
    } catch {
      // Offline or signed out meanwhile: keep the bell as it is.
    }
  }

  document.addEventListener('visibilitychange', () => (document.hidden ? left() : cameBack()));
  window.addEventListener('blur', left);
  window.addEventListener('focus', cameBack);
})();
