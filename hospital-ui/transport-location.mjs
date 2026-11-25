const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const hid = params.get('hid') || '';
const pid = params.get('pid') || '';
const REFRESH_MS = 30000;
let map = null, points = null, loading = false, fitted = false;

function showError(message) {
  $('error').textContent = message;
  $('error').hidden = !message;
}

function clock(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString('ko-KR', {hour: 'numeric', minute: '2-digit'});
}

function draw(p, d) {
  if (!map) {
    map = L.map('map', {scrollWheelZoom: false});
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, referrerPolicy: 'origin',
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    tiles.on('tileerror', () => showError('지도 불러오기 실패'));
    points = L.featureGroup().addTo(map);
  }
  points.clearLayers();
  L.circleMarker([p.lat, p.lon], {radius: 8, color: '#2265b2', fillOpacity: 1}).addTo(points);
  L.circleMarker([d.lat, d.lon], {radius: 8, color: '#c83e49', fillOpacity: 1}).addTo(points);
  L.polyline([[p.lat, p.lon], [d.lat, d.lon]], {color: '#285e99', dashArray: '5 7'}).addTo(points);
  // Fit once; later refreshes keep the zoom the viewer chose.
  if (!fitted) { map.fitBounds(points.getBounds(), {padding: [45, 45], maxZoom: 15}); fitted = true; }
  map.invalidateSize();
}

async function load() {
  if (loading) return;
  loading = true;
  $('refresh').disabled = true;
  try {
    if (!/^H(?:0[1-9]|10)$/.test(hid) || !/^Q119-TEST-[A-Za-z0-9-]{1,80}$/.test(pid))
      throw new Error('조회 실패');
    const response = await fetch(`/api/hospitals/${encodeURIComponent(hid)}/patients/${encodeURIComponent(pid)}/transport-location`,
      {cache: 'no-store'});
    if (!response.ok) throw new Error('조회 실패');
    const data = await response.json();
    const p = data.position, d = data.destination;
    showError('');
    $('distance').textContent = data.estimate && Number.isFinite(data.estimate.distance_meters)
      ? `${(data.estimate.distance_meters / 1000).toFixed(1)} km` : '—';
    $('eta').textContent = data.state === 'IN_TRANSIT' && Number.isFinite(data.eta_at) ? clock(data.eta_at) : '—';
    if (p && d && Number.isFinite(p.lat) && Number.isFinite(p.lon) &&
        Number.isFinite(d.lat) && Number.isFinite(d.lon)) draw(p, d);
    // 서버에 남은 구급대 위치의 시각을 보인다. 아직 GPS가 오지 않았으면 접수 때 위치라는 것을 밝힌다.
    $('updated').textContent = p && Number.isFinite(p.updated_at) ? `구급대 위치 ${clock(p.updated_at)} 기준`
      : p ? '접수 위치 · 실시간 위치 수신 전' : '구급대 위치 없음';
  } catch (error) {
    console.error('위치 조회 실패:', error);
    showError('조회 실패');
    $('updated').textContent = '불러오지 못했습니다';
  } finally {
    loading = false;
    $('refresh').disabled = false;
  }
}

$('refresh').addEventListener('click', () => { void load(); });
setInterval(() => { if (!document.hidden) void load(); }, REFRESH_MS);
document.addEventListener('visibilitychange', () => { if (!document.hidden) void load(); });
void load();
