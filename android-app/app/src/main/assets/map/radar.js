/* AEGIS MESH radar map. The app pushes data with aegis.update(...), aegis.pings(...), aegis.sweep(). */
(function () {
  var map = L.map('map', { preferCanvas: true, zoomControl: true }).setView([50.0647, 19.9234], 15);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 21, maxNativeZoom: 19, attribution: '© OpenStreetMap'
  }).addTo(map);

  var last = { routers: [], devices: [] }, selected = null;
  var follow = true, first = true, me = null, meRing = null, sweepMarker = null;
  var pingLayer = L.layerGroup().addTo(map);
  var routerLayer = L.layerGroup().addTo(map);
  var deviceLayer = L.layerGroup().addTo(map);
  var note = document.getElementById('note');
  map.on('dragstart', function () { follow = false; });

  // -40 dBm green (close) .. -90 dBm red (far)
  function heat(rssi) {
    var d = Math.min(1, Math.max(0, (-40 - rssi) / 50));
    var a = d < 0.5 ? [67, 214, 110] : [212, 175, 55], b = d < 0.5 ? [212, 175, 55] : [229, 57, 53];
    var t = d < 0.5 ? d * 2 : (d - 0.5) * 2;
    return 'rgb(' + a.map(function (v, i) { return Math.round(v + (b[i] - v) * t); }).join(',') + ')';
  }

  var RED = '#e53935';

  // tap on a router or device: tell the app (it marks the row) and redraw at once
  function pick(id) {
    selected = id; draw();
    if (window.AegisApp) window.AegisApp.select(id);
  }

  /** Only dots; the area is drawn just for the selected router, which is red, bigger and on top. */
  function draw() {
    routerLayer.clearLayers();
    deviceLayer.clearLayers();
    var chosen = null;
    (last.routers || []).forEach(function (r) {
      if (r.id === selected) { chosen = r; return; }
      L.circleMarker([r.lat, r.lon], { radius: 5, color: '#0a0a0a', weight: 1, fillColor: r.color, fillOpacity: 1 })
        .bindTooltip(r.label, { className: 'router-label' }).on('click', function () { pick(r.id); }).addTo(routerLayer);
    });
    // devices heard in the latest scan, around the phone at their rough distance
    (last.devices || []).forEach(function (d) {
      var on = d.id === selected;
      L.circleMarker([d.lat, d.lon], on
        ? { radius: 8, color: '#fff', weight: 2, fillColor: RED, fillOpacity: 1 }
        : { radius: 3, color: d.color, weight: 1, fillColor: d.color, fillOpacity: 0.5 })
        .bindTooltip(d.label, { className: 'router-label' }).on('click', function () { pick(d.id); }).addTo(deviceLayer);
    });
    if (chosen) {
      L.circle([chosen.lat, chosen.lon], { radius: chosen.r, color: RED, weight: 3, fillColor: RED, fillOpacity: 0.1, interactive: false }).addTo(routerLayer);
      L.circleMarker([chosen.lat, chosen.lon], { radius: 11, color: '#fff', weight: 2, fillColor: RED, fillOpacity: 1 })
        .bindTooltip(chosen.label, { className: 'router-label', permanent: true, direction: 'top', offset: [0, -10] }).addTo(routerLayer);
    }
  }

  window.aegis = {
    /** me: {lat, lon, acc} or null; routers: [{id, lat, lon, r, color, label}]; devices: [{id, lat, lon, color, label}]; weak: note text. */
    update: function (data) {
      if (data.me) {
        var at = [data.me.lat, data.me.lon];
        if (!me) {
          me = L.circleMarker(at, { radius: 8, color: '#fff', weight: 2, fillColor: '#2b8cff', fillOpacity: 1 }).addTo(map);
          meRing = L.circle(at, { radius: data.me.acc, color: '#2b8cff', weight: 1, dashArray: '3 5', fillOpacity: 0.04, interactive: false }).addTo(map);
        } else { me.setLatLng(at); meRing.setLatLng(at).setRadius(data.me.acc); }
        if (first) { map.setView(at, 18); first = false; } else if (follow) map.panTo(at);
        note.style.display = 'none';
      } else {
        note.textContent = data.waiting || 'Czekam na GPS…'; note.style.display = 'block';
      }
      if (data.weak) { note.textContent = data.weak; note.style.display = 'block'; }
      if ('selected' in data) selected = data.selected;
      last = data; draw();
    },
    /** Marks one router (by BSSID) red and bigger; null clears it. */
    select: function (id) { selected = id || null; draw(); },
    /** All pings as [lat, lon, rssi, goodGps]: small dots coloured by signal; hollow when the GPS was weak. */
    pings: function (list) {
      pingLayer.clearLayers();
      list.forEach(function (p) {
        var good = p[3] !== 0;
        L.circleMarker([p[0], p[1]], good
          ? { radius: 2, stroke: false, fillColor: heat(p[2]), fillOpacity: 0.6, interactive: false }
          : { radius: 2, weight: 1, opacity: 0.5, color: heat(p[2]), fill: false, interactive: false }).addTo(pingLayer);
      });
    },
    /** One and a half turns of the radar beam around the phone, then it fades out. */
    sweep: function () {
      if (!me) return;
      if (sweepMarker) map.removeLayer(sweepMarker);
      sweepMarker = L.marker(me.getLatLng(), {
        interactive: false, keyboard: false,
        icon: L.divIcon({ className: '', iconSize: [0, 0], html: '<div class="sweep"></div>' })
      }).addTo(map);
    },
    center: function () { follow = true; if (me) map.setView(me.getLatLng(), Math.max(map.getZoom(), 18)); },
    focus: function (lat, lon, id) { follow = false; if (id) { selected = id; draw(); } map.setView([lat, lon], 19); }
  };
})();
