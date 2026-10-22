// "Show passwords" (templates/_show_password.html): shows or hides every
// password box in the same form, so a password can be checked on a phone
// before it's saved.
document.addEventListener('change', function (event) {
    var box = event.target;
    if (!box.matches || !box.matches('[data-show-password]') || !box.form) {
        return;
    }
    box.form.querySelectorAll('input[type="password"], input[data-shown-password]').forEach(function (input) {
        input.type = box.checked ? 'text' : 'password';
        input.toggleAttribute('data-shown-password', box.checked);
    });
});
