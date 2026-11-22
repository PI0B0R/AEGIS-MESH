/* AEGIS MESH radar map. The app pushes data with aegis.update(...), aegis.pings(...), aegis.sweep(). */
(function () {
  var map = L.map('map', { preferCanvas: true, zoomControl: true }).setView([50.0647, 19.9234], 15);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 21, maxNativeZoom: 19, attribution: '© OpenStreetMap'
  }).addTo(map);

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

  window.aegis = {
    /** me: {lat, lon, acc} or null; routers: [{lat, lon, r, color, label}]. */
    update: function (data) {
      if (data.me) {
        var at = [data.me.lat, data.me.lon];
        if (!me) {
          me = L.circleMarker(at, { radius: 8, color: '#fff', weight: 2, fillColor: '#2b8cff', fillOpacity: 1 }).addTo(map);
          meRing = L.circle(at, { radius: data.me.acc, color: '#2b8cff', weight: 1, fillOpacity: 0.12 }).addTo(map);
        } else { me.setLatLng(at); meRing.setLatLng(at).setRadius(data.me.acc); }
        if (first) { map.setView(at, 18); first = false; } else if (follow) map.panTo(at);
        note.style.display = 'none';
      } else {
        note.textContent = data.waiting || 'Czekam na GPS…'; note.style.display = 'block';
      }
      routerLayer.clearLayers();
      (data.routers || []).forEach(function (r) {
        L.circle([r.lat, r.lon], { radius: r.r, color: r.color, weight: 1.5, dashArray: '4 4', fillOpacity: 0.08 }).addTo(routerLayer);
        L.circleMarker([r.lat, r.lon], { radius: 7, color: '#0a0a0a', weight: 2, fillColor: r.color, fillOpacity: 1 })
          .bindTooltip(r.label, { className: 'router-label' }).addTo(routerLayer);
      });
      // devices heard in the latest scan, around the phone at their rough distance (tap for name and signal)
      deviceLayer.clearLayers();
      var centre = me ? me.getLatLng() : null;
      (data.devices || []).forEach(function (d) {
        if (centre) L.polyline([centre, [d.lat, d.lon]], { color: d.color, weight: 1, opacity: 0.35 }).addTo(deviceLayer);
        L.circleMarker([d.lat, d.lon], { radius: 6, color: '#0a0a0a', weight: 1.5, fillColor: d.color, fillOpacity: 0.95 })
          .bindTooltip(d.label, { className: 'router-label' }).addTo(deviceLayer);
      });
    },
    /** All pings as [lat, lon, rssi, goodGps]: small dots coloured by signal; hollow when the GPS was weak. */
    pings: function (list) {
      pingLayer.clearLayers();
      list.forEach(function (p) {
        var good = p[3] !== 0;
        L.circleMarker([p[0], p[1]], good
          ? { radius: 3, stroke: false, fillColor: heat(p[2]), fillOpacity: 0.7 }
          : { radius: 3, weight: 1, color: heat(p[2]), fillOpacity: 0 }).addTo(pingLayer);
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
    focus: function (lat, lon) { follow = false; map.setView([lat, lon], 19); }
  };
})();
