// A machine card's "+ Serial plate / Before / After" (tasks/_visit_photos.html):
// choosing the photo sends it, no second button to press.
document.querySelectorAll('[data-photo-slot-input]').forEach((input) => {
    input.addEventListener('change', () => {
        if (input.files.length) {
            input.closest('form').submit();
        }
    });
});
