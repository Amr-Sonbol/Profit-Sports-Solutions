// Team map (tasks/team_map.html): one pin per technician at their last
// tap today. Red pins are taps away from the site.
(function () {
    var element = document.getElementById('team-map');
    var data = document.getElementById('team-map-pins');
    if (!element || !data || !window.L) {
        return;
    }
    var pins = JSON.parse(data.textContent);
    var map = L.map(element);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);

    var points = [];
    pins.forEach(function (pin) {
        var popup = document.createElement('div');
        var name = document.createElement('strong');
        name.textContent = pin.name;
        var detail = document.createElement('div');
        detail.textContent = pin.detail;
        var place = document.createElement('a');
        place.href = pin.url;
        place.textContent = pin.place;
        popup.append(name, detail, place);

        L.circleMarker([pin.lat, pin.lng], {
            radius: 9,
            weight: 2,
            color: '#fff',
            fillColor: pin.away ? '#c0392b' : '#000',
            fillOpacity: 1,
        }).bindPopup(popup).bindTooltip(pin.name).addTo(map);
        points.push([pin.lat, pin.lng]);
    });
    map.fitBounds(points, {padding: [40, 40], maxZoom: 15});
})();
