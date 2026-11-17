/* Presentation only: switching panels never starts or stops navigation/polling. */
(() => {
  const root = document.getElementById('transport-workspace');
  if (!root) return;
  const byId = id => document.getElementById('transport-' + id);
  let view = 'chat';
  function show(next) {
    if (next === view) return;
    document.dispatchEvent(new CustomEvent('quick119:before-view', {detail: {view: next}}));
    view = next;
    byId('gps').hidden = view !== 'gps';
    byId('chat').hidden = view !== 'chat';
    byId('toggle-label').textContent = view === 'gps' ? '접수 채팅' : 'GPS · 병원 선택';
    byId('title').textContent = view === 'gps' ? 'GPS · 병원 선택' : '접수 채팅';
    byId('toggle').setAttribute('aria-expanded', String(view === 'chat'));
    document.dispatchEvent(new CustomEvent('quick119:view', {detail: {view}}));
    byId('title').focus({preventScroll: true});
  }
  byId('toggle').addEventListener('click', () => show(view === 'gps' ? 'chat' : 'gps'));
  document.addEventListener('quick119:show-view', event => {
    if (event.detail?.view === 'gps' || event.detail?.view === 'chat') show(event.detail.view);
  });
})();
