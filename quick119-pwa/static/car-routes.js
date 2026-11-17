/* GPS and route data stay in memory; REST credentials stay on the server. */
(() => {
  'use strict';
  const panel = document.getElementById('car-route-panel');
  if (!panel) return;
  const byId = id => document.getElementById(id);
  const status = byId('car-route-status');
  const result = byId('car-route-result');
  const buttons = [...document.querySelectorAll('.car-route-button')];
  // Galaxy field-test tuning: metres, milliseconds and consecutive independent fixes.
  const NAV = Object.freeze({
    MAX_ACCURACY_M: 50, MAX_FIX_AGE_MS: 15000, STALE_MS: 30000,
    MIN_REQUEST_MS: 10000, REFRESH_MS: 30000, MOVE_M: 100, MIN_MOVE_M: 25,
    OFF_ROUTE_M: 80, OFF_ROUTE_FIXES: 3, ACCURACY_MULTIPLIER: 2,
    FIX_INTERVAL_MS: 2000, CONFIRM_GAP_MS: 15000, MAX_SPEED_MPS: 70,
    ARRIVAL_M: 40, ARRIVAL_ACCURACY_M: 25, ARRIVAL_FIXES: 3,
    RETRY_MAX_MS: 120000,
  });
  let selected = null, busy = false, map = null, routeLayer = null, currentMarker = null;
  let routeCoordinates = [], latest = null, lastSuccess = null;
  let generation = 0, activeRequest = null, tracking = false, watchId = null, staleTimer = null;
  let following = false, lastFix = null, lastReceivedAt = 0;
  let lastAttempt = null, retryAt = 0, failures = 0;
  let offCount = 0, arrivalCount = 0, lastConfirmTime = null;
  let savedView = null, pendingMap = null;
  const mapVisible = () => !document.getElementById('transport-gps')?.hidden;
  const errorMessages = {
    disabled: '길찾기 서버가 비활성 상태입니다. HTTPS API 서버 실행 설정을 확인해 주세요.',
    missing_key: '서버에 카카오 길찾기 키가 설정되지 않았습니다.',
    authentication_failed: '카카오 길찾기 인증을 확인해 주세요.',
    permission_denied: '카카오 길찾기 API 사용 권한을 확인해 주세요.',
    quota_exceeded: '길찾기 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.',
    no_route: '이 위치에서 해당 응급실까지 자동차 경로를 찾지 못했습니다.',
    timeout: '길찾기 응답 시간이 초과됐습니다. 다시 시도해 주세요.',
  };
  const gpsMessage = code => ({
    1: '위치 권한이 거부됐습니다. 브라우저에서 위치 권한을 허용하고 다시 시도해 주세요.',
    2: 'GPS 위치를 확인할 수 없습니다. 위치 서비스를 확인해 주세요.',
    3: 'GPS 위치 확인 시간이 초과됐습니다. 다시 시도해 주세요.',
  }[code] || 'GPS 위치 확인에 실패했습니다.');
  const gpsStatus = message => { byId('car-route-gps').textContent = `GPS 상태: ${message}`; };
  const point = position => ({lat: position.coords.latitude, lon: position.coords.longitude,
    accuracy: position.coords.accuracy, timestamp: position.timestamp});
  const validPoint = p => Number.isFinite(p.lat) && Number.isFinite(p.lon) &&
    Math.abs(p.lat) <= 90 && Math.abs(p.lon) <= 180;

  function updateControls() {
    byId('car-route-start').disabled = tracking || busy || result.hidden;
    byId('car-route-stop').disabled = !tracking;
    byId('car-route-refresh').disabled = busy || tracking;
    byId('car-route-follow').disabled = !tracking;
    byId('car-route-follow').setAttribute('aria-pressed', String(following));
    // Destination buttons remain usable: switching cancels the previous session.
    panel.toggleAttribute('aria-busy', busy);
    if (busy) panel.setAttribute('aria-busy', 'true');
  }

  function resetConfirmations() { offCount = 0; arrivalCount = 0; lastConfirmTime = null; }

  function stopNavigation(message) {
    ++generation;
    tracking = false;
    if (watchId !== null) navigator.geolocation.clearWatch(watchId);
    watchId = null;
    clearInterval(staleTimer);
    staleTimer = null;
    if (activeRequest) activeRequest.abort();
    activeRequest = null;
    busy = false;
    following = false;
    resetConfirmations();
    updateControls();
    if (message) gpsStatus(message);
  }

  function getGPS() {
    if (!window.isSecureContext) return Promise.reject(new Error('현재 GPS를 사용하려면 신뢰되는 HTTPS로 접속해 주세요.'));
    if (!navigator.geolocation) return Promise.reject(new Error('이 브라우저에서는 GPS 위치를 사용할 수 없습니다.'));
    return new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(resolve,
      error => reject(new Error(gpsMessage(error.code))),
      {enableHighAccuracy: true, timeout: 10000, maximumAge: 0}));
  }

  function distance(a, b) {
    const rad = Math.PI / 180;
    const h = Math.sin((b.lat - a.lat) * rad / 2) ** 2 +
      Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin((b.lon - a.lon) * rad / 2) ** 2;
    return 6371000 * 2 * Math.asin(Math.sqrt(Math.min(1, h)));
  }

  function distanceFromRoute(p) {
    // Local metric projection; measure distance to segments, not just vertices.
    const sx = 111195 * Math.cos(p.lat * Math.PI / 180), sy = 111195;
    let nearest = Infinity;
    for (let i = 1; i < routeCoordinates.length; i++) {
      const a = routeCoordinates[i - 1], b = routeCoordinates[i];
      const ax = (a[0] - p.lon) * sx, ay = (a[1] - p.lat) * sy;
      const dx = (b[0] - a[0]) * sx, dy = (b[1] - a[1]) * sy;
      const length = dx * dx + dy * dy;
      const t = length ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / length)) : 0;
      nearest = Math.min(nearest, Math.hypot(ax + t * dx, ay + t * dy));
    }
    return nearest;
  }

  function moveMarker(origin) {
    if (mapVisible()) {
      if (currentMarker) currentMarker.setLatLng([origin.lat, origin.lon]);
      if (map && following) map.panTo([origin.lat, origin.lon], {animate: false});
    }
    const accuracy = Number.isFinite(origin.accuracy) ? ` · GPS 정확도 약 ${Math.round(origin.accuracy)}m` : '';
    byId('car-route-origin').textContent = `${tracking ? '현재 위치' : '출발'} ${origin.lat.toFixed(5)}, ${origin.lon.toFixed(5)}${accuracy}`;
    const query = new URLSearchParams({sp: `${origin.lat},${origin.lon}`, ep: `${selected.lat},${selected.lon}`, by: 'CAR'});
    byId('car-route-external').href = `https://map.kakao.com/?${query}`;
  }

  function drawMap(coordinates, origin, destination, preserveView = false) {
    if (!mapVisible()) { pendingMap = {coordinates, origin, destination}; return; }
    const mapStatus = byId('car-route-map-status');
    mapStatus.textContent = '';
    if (!window.L) {
      mapStatus.textContent = '지도를 불러오지 못했습니다. 거리·시간을 확인하거나 카카오맵에서 경로를 여세요.';
      return;
    }
    try {
      if (!map) {
        map = L.map('car-route-map', {scrollWheelZoom: false});
        // Only an actual user drag disables following; taps, zoom and panTo do not.
        map.on('dragstart', () => { following = false; updateControls(); });
        // Send only the site's origin as Referer, never the patient/GPS page query.
        const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
          maxZoom: 19, referrerPolicy: 'origin',
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        }).addTo(map);
        tiles.on('tileerror', () => {
          mapStatus.textContent = '배경지도를 불러오지 못했습니다. 표시된 선은 카카오 자동차 경로입니다. 카카오맵에서도 확인할 수 있습니다.';
        });
      }
      if (routeLayer) routeLayer.remove();
      routeLayer = L.featureGroup().addTo(map);
      const latLngs = coordinates.map(([lon, lat]) => [lat, lon]);
      if (latLngs.length >= 2) L.polyline(latLngs, {color: '#2265b2', weight: 6, opacity: 0.9}).addTo(routeLayer);
      else mapStatus.textContent = '도로 경로 좌표가 없어 출발지와 목적지만 표시합니다. 카카오맵에서 경로를 확인해 주세요.';
      currentMarker = L.circleMarker([origin.lat, origin.lon], {radius: 8, color: '#fff', weight: 3, fillColor: '#2265b2', fillOpacity: 1})
        .bindTooltip('현재 GPS 위치').addTo(routeLayer);
      L.circleMarker([destination.lat, destination.lon], {radius: 8, color: '#fff', weight: 3, fillColor: '#dc3545', fillOpacity: 1})
        .bindTooltip(document.createTextNode(destination.name)).addTo(routeLayer);
      map.invalidateSize();
      // Never reset a user's map view during a navigation refresh/reroute.
      if (!tracking && !preserveView) map.fitBounds(routeLayer.getBounds(), {padding: [24, 24], maxZoom: 16});
    } catch (_) {
      mapStatus.textContent = '지도 표시를 완료하지 못했습니다. 카카오맵에서 경로를 확인해 주세요.';
    }
  }

  async function requestRoute(origin, replaceRoute, token) {
    const destination = selected;
    const controller = new AbortController();
    activeRequest = controller;
    busy = true;
    lastAttempt = Date.now();
    updateControls();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch('/api/routes/car', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, cache: 'no-store',
        signal: controller.signal,
        body: JSON.stringify({origin: {lat: origin.lat, lon: origin.lon},
          destination: {lat: destination.lat, lon: destination.lon}}),
      });
      const data = await response.json();
      if (token !== generation) return;
      if (!response.ok || data.code !== 'ok') throw new Error(errorMessages[data.code] || '자동차 경로 조회에 실패했습니다. 잠시 후 다시 시도해 주세요.');
      if (![data.distance_m, data.duration_sec].every(value => Number.isFinite(value) && value >= 0)) {
        throw new Error('길찾기 응답 형식을 확인할 수 없습니다. 다시 시도해 주세요.');
      }
      failures = 0;
      retryAt = 0;
      lastSuccess = {...origin, at: Date.now()};
      const provider = byId('car-route-provider');
      if (provider) {
        provider.hidden = data.provider !== 'synthetic';
        provider.textContent = data.provider === 'synthetic' ? '가상 직선·시간 추정 테스트입니다. 실제 도로 경로나 길안내로 사용하지 마세요.' : '';
      }
      byId('car-route-destination').textContent = `현재 GPS → ${destination.name}`;
      byId('car-route-distance').textContent = `${(data.distance_m / 1000).toFixed(1)} km`;
      const minutes = Math.ceil(data.duration_sec / 60);
      byId('car-route-duration').textContent = minutes >= 60 ? `${Math.floor(minutes / 60)}시간 ${minutes % 60}분` : `약 ${minutes}분`;
      const time = new Date().toLocaleTimeString('ko-KR', {hour: '2-digit', minute: '2-digit', second: '2-digit'});
      byId('car-route-updated').textContent = `거리·시간 마지막 계산: ${time}${data.cached ? ' · 최근 30초 내 조회 결과' : ''}`;
      result.hidden = false;
      if (replaceRoute) {
        routeCoordinates = (data.coordinates || []).filter(c => Array.isArray(c) && c.length === 2 && validPoint({lon: c[0], lat: c[1]}));
        drawMap(routeCoordinates, latest || origin, destination);
        offCount = 0;
      }
      moveMarker(latest || origin);
      status.textContent = `${tracking ? '실시간 안내 중' : '경로 조회 완료'} · ${time}`;
    } catch (error) {
      if (token !== generation) return;
      failures++;
      retryAt = Date.now() + Math.min(NAV.RETRY_MAX_MS, NAV.MIN_REQUEST_MS * 2 ** Math.min(failures, 4));
      const message = error.name === 'AbortError' ? '길찾기 응답 시간이 초과됐습니다. 다시 시도해 주세요.'
        : error instanceof TypeError ? '네트워크 연결을 확인하고 다시 시도해 주세요.' : error.message;
      status.textContent = message + (tracking ? ' 기존 경로와 마지막 계산값을 유지합니다. 이동 시 간격을 두고 재시도합니다.' : '');
    } finally {
      clearTimeout(timeout);
      if (token === generation) {
        busy = false;
        activeRequest = null;
        updateControls();
      }
    }
  }

  async function showRoute(destination) {
    stopNavigation('안내 대기');
    const token = generation;
    selected = destination;
    pendingMap = null;
    savedView = null;
    latest = null;
    lastSuccess = null;
    routeCoordinates = [];
    lastAttempt = null;
    failures = 0;
    retryAt = 0;
    busy = true;
    result.hidden = true;
    updateControls();
    status.textContent = '현재 GPS 위치를 확인하고 있습니다…';
    panel.scrollIntoView({behavior: 'smooth', block: 'start'});
    byId('car-route-heading').focus({preventScroll: true});
    try {
      const position = await getGPS();
      if (token !== generation) return;
      latest = point(position);
      if (!validPoint(latest)) throw new Error('유효한 GPS 좌표를 받지 못했습니다. 다시 시도해 주세요.');
      status.textContent = `${destination.name}까지 자동차 경로를 계산하고 있습니다…`;
      await requestRoute(latest, true, token);
    } catch (error) {
      if (token === generation) status.textContent = error.message;
    } finally {
      if (token === generation) { busy = false; updateControls(); }
    }
  }

  function handleFix(position, token) {
    if (!tracking || token !== generation) return;
    const p = point(position), now = Date.now();
    if (!validPoint(p) || !Number.isFinite(p.timestamp) || now - p.timestamp > NAV.MAX_FIX_AGE_MS ||
        p.timestamp > now + 1000 || !Number.isFinite(p.accuracy) || p.accuracy < 0 || p.accuracy > NAV.MAX_ACCURACY_M) {
      resetConfirmations();
      gpsStatus('정확도 부족 또는 오래된 위치 · 경로 갱신 대기');
      return;
    }
    if (lastFix && p.timestamp <= lastFix.timestamp) return;
    if (lastFix && distance(p, lastFix) > NAV.MAX_SPEED_MPS * (p.timestamp - lastFix.timestamp) / 1000 + p.accuracy + lastFix.accuracy) {
      resetConfirmations();
      gpsStatus('일시적인 위치 튐 감지 · 다음 위치 수신 대기');
      return;
    }
    lastFix = p;
    latest = p;
    lastReceivedAt = now;
    gpsStatus(`추적 중 · 정확도 약 ${Math.round(p.accuracy)}m · ${new Date(p.timestamp).toLocaleTimeString('ko-KR')}`);
    moveMarker(p);
    const nearDestination = p.accuracy <= NAV.ARRIVAL_ACCURACY_M && distance(p, selected) <= NAV.ARRIVAL_M;
    const offRoute = routeCoordinates.length >= 2 && distanceFromRoute(p) > Math.max(NAV.OFF_ROUTE_M, p.accuracy * NAV.ACCURACY_MULTIPLIER);
    // A contradictory fix breaks the sequence even inside the counting interval.
    if (!nearDestination) arrivalCount = 0;
    if (!offRoute) offCount = 0;
    if (lastConfirmTime === null || p.timestamp - lastConfirmTime >= NAV.FIX_INTERVAL_MS) {
      if (lastConfirmTime !== null && p.timestamp - lastConfirmTime > NAV.CONFIRM_GAP_MS) resetConfirmations();
      lastConfirmTime = p.timestamp;
      arrivalCount = nearDestination ? arrivalCount + 1 : 0;
      offCount = offRoute ? offCount + 1 : 0;
    }
    if (arrivalCount >= NAV.ARRIVAL_FIXES) {
      stopNavigation('목적지 근처 도착 · 안내 종료');
      status.textContent = `${selected.name} 근처에 도착했습니다. 응급실 입구와 실제 위치를 확인해 주세요.`;
      return;
    }
    if (arrivalCount > 0) gpsStatus(`목적지 근처 · 도착 확인 중 (${arrivalCount}/${NAV.ARRIVAL_FIXES}) · 정확도 약 ${Math.round(p.accuracy)}m`);
    if (busy || now < retryAt || (lastAttempt !== null && now - lastAttempt < NAV.MIN_REQUEST_MS)) return;
    const moved = lastSuccess ? distance(p, lastSuccess) : Infinity;
    const meaningfulMove = Math.max(NAV.MIN_MOVE_M, p.accuracy + (lastSuccess?.accuracy || 0));
    const reroute = offCount >= NAV.OFF_ROUTE_FIXES;
    if (reroute || moved >= NAV.MOVE_M || (moved >= meaningfulMove && (lastAttempt === null || now - lastAttempt >= NAV.REFRESH_MS))) {
      status.textContent = reroute ? '경로 이탈 확인 · 경로를 재탐색하고 있습니다…' : '현재 위치 기준 남은 거리·시간을 갱신하고 있습니다…';
      void requestRoute(p, reroute || routeCoordinates.length < 2, token);
    }
  }

  function startNavigation() {
    if (tracking || busy || !selected || result.hidden) return;
    if (!window.isSecureContext || !navigator.geolocation?.watchPosition) {
      gpsStatus('신뢰되는 HTTPS와 GPS를 지원하는 브라우저에서 실행해 주세요.');
      return;
    }
    stopNavigation();
    const token = generation;
    tracking = true;
    following = true;
    lastFix = null;
    lastReceivedAt = Date.now();
    gpsStatus('정확한 현재 위치 수신 대기');
    status.textContent = '실시간 안내 시작 · GPS 위치를 기다리고 있습니다…';
    updateControls();
    try {
      watchId = navigator.geolocation.watchPosition(position => handleFix(position, token), error => {
        if (!tracking || token !== generation) return;
        resetConfirmations();
        if (error.code === 1) {
          stopNavigation(gpsMessage(error.code));
          status.textContent = gpsMessage(error.code);
        }
        else gpsStatus(`${gpsMessage(error.code)} 다음 위치 수신을 기다립니다.`);
      }, {enableHighAccuracy: true, maximumAge: 0, timeout: 15000});
      staleTimer = setInterval(() => {
        if (tracking && Date.now() - lastReceivedAt >= NAV.STALE_MS) {
          resetConfirmations();
          gpsStatus('위치 수신 지연 · 현재 위치와 거리·시간이 오래되었을 수 있습니다.');
        }
      }, 5000);
    } catch (_) { stopNavigation('GPS 추적을 시작하지 못했습니다. 위치 권한을 확인하고 다시 시도해 주세요.'); }
  }

  buttons.forEach(button => button.addEventListener('click', () => showRoute({
    name: button.dataset.name, lat: Number(button.dataset.lat), lon: Number(button.dataset.lon),
  })));
  document.addEventListener('quick119:route-select', event => {
    const p = event.detail;
    if (!p || !validPoint(p) || typeof p.name !== 'string') return;
    if (p.hospital_id && selected?.hospital_id === p.hospital_id) return;
    void showRoute(p);
  });
  document.addEventListener('quick119:before-view', event => {
    if (event.detail.view === 'chat' && map && mapVisible()) {
      savedView = {center: map.getCenter(), zoom: map.getZoom()};
    }
  });
  function restoreMap() {
    if (!mapVisible() || document.hidden) return;
    requestAnimationFrame(() => {
      if (!mapVisible() || document.hidden) return;
      const view = savedView || (map ? {center: map.getCenter(), zoom: map.getZoom()} : null);
      if (pendingMap) {
        const {coordinates, origin, destination} = pendingMap;
        drawMap(coordinates, latest || origin, destination, !!map);
        pendingMap = null;
      }
      if (map) {
        map.invalidateSize({pan: false, animate: false});
        if (view) map.setView(following && latest ? [latest.lat, latest.lon] : view.center, view.zoom, {animate: false});
        if (latest) moveMarker(latest);
      }
      savedView = null;
    });
  }
  document.addEventListener('quick119:view', event => { if (event.detail.view === 'gps') restoreMap(); });
  if (window.ResizeObserver) new ResizeObserver(() => { if (mapVisible()) restoreMap(); }).observe(byId('car-route-map'));
  byId('car-route-refresh').addEventListener('click', () => { if (selected && !tracking) void showRoute(selected); });
  byId('car-route-start').addEventListener('click', startNavigation);
  byId('car-route-stop').addEventListener('click', () => {
    stopNavigation('안내 종료');
    status.textContent = '실시간 안내를 종료했습니다. 마지막 경로와 계산값을 표시합니다.';
  });
  byId('car-route-follow').addEventListener('click', () => {
    if (!tracking) return;
    following = true;
    if (latest) moveMarker(latest);
    updateControls();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && (tracking || busy)) {
      stopNavigation('화면 숨김으로 안내 중단 · 복귀 후 안내를 다시 시작해 주세요.');
      status.textContent = '화면이 꺼지거나 앱이 백그라운드로 전환되어 안내를 중단했습니다.';
    }
    if (!document.hidden) restoreMap();
  });
  window.addEventListener('pagehide', () => stopNavigation('페이지 이탈로 안내 종료 · 다시 시작해 주세요.'));
  window.addEventListener('pageshow', restoreMap);
  updateControls();
})();
