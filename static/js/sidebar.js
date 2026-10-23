// The three-line menu button (templates/base.html). On a phone it opens the
// slide-in menu (the hidden #nav-toggle checkbox). On desktop, where the
// sidebar is open by default, it hides or shows it instead, remembered on
// this computer; the <head> applies the saved choice before the page draws.
(function () {
    var button = document.querySelector('.nav-toggle-button');
    if (!button) {
        return;
    }
    var desktop = window.matchMedia('(min-width: 1024px)');
    var root = document.documentElement;

    function syncExpanded() {
        var open = desktop.matches
            ? !root.classList.contains('sidebar-collapsed')
            : document.getElementById('nav-toggle').checked;
        button.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    button.addEventListener('click', function (event) {
        if (desktop.matches) {
            event.preventDefault();
            var collapsed = root.classList.toggle('sidebar-collapsed');
            try {
                localStorage.setItem('sidebarCollapsed', collapsed ? '1' : '0');
            } catch (error) {
                // Private browsing: it still works, just isn't remembered.
            }
        }
        // The checkbox flips after this handler on a phone.
        setTimeout(syncExpanded, 0);
    });

    // A <label> can't be focused or pressed from the keyboard by itself.
    button.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            button.click();
        }
    });

    desktop.addEventListener('change', syncExpanded);
    syncExpanded();
})();
