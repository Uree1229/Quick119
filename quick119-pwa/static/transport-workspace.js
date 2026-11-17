/* Presentation only: switching panels never starts or stops navigation/polling. */
(() => {
  const root = document.getElementById('transport-workspace');
  if (!root) return;
  const byId = id => document.getElementById('transport-' + id);
  let view = 'chat', focusTimer = null;
  root.dataset.view = view;
  function show(next) {
    if (next === view) return;
    document.dispatchEvent(new CustomEvent('quick119:before-view', {detail: {view: next}}));
    view = next;
    root.dataset.view = view;
    byId('gps').hidden = view !== 'gps';
    byId('chat').hidden = view !== 'chat';
    // The switch names the destination; the title names the current screen.
    byId('toggle-label').textContent = view === 'gps' ? '채팅' : '지도';
    byId('title').textContent = view === 'gps' ? '지도 · 병원 선택' : '접수 채팅';
    byId('toggle').setAttribute('aria-expanded', String(view === 'chat'));
    document.dispatchEvent(new CustomEvent('quick119:view', {detail: {view}}));
    byId('title').focus({preventScroll: true});
  }
  byId('toggle').addEventListener('click', () => show(view === 'gps' ? 'chat' : 'gps'));
  byId('open-map')?.addEventListener('click', () => show('gps'));
  document.addEventListener('quick119:show-view', event => {
    if (event.detail?.view === 'gps' || event.detail?.view === 'chat') show(event.detail.view);
  });
  // Keep the latest message in view when the dock or keyboard resizes a visible log (unless the user scrolled up).
  // Returning from the map (hidden -> visible) keeps the old position, so unread hospital messages stay unread.
  const log = byId('messages');
  if (log && window.ResizeObserver) {
    let pinned = true, shownHeight = 0;
    log.addEventListener('scroll', () => { pinned = log.scrollHeight - log.scrollTop - log.clientHeight < 48; }, {passive: true});
    new ResizeObserver(entries => {
      const height = entries[entries.length - 1].contentRect.height, wasShown = shownHeight > 0;
      shownHeight = height;
      if (wasShown && height > 0 && pinned && byId('mark-read')?.hidden !== false) log.scrollTop = log.scrollHeight;
    }).observe(log);
  }
  // A newly selected route is shown at the top of the map sheet (the route card is the sheet's first item).
  document.addEventListener('quick119:route-select', () => { byId('sheet')?.scrollTo?.({top: 0}); });
  // A tapped map pin highlights its candidate card; it never inquires or routes by itself.
  document.addEventListener('quick119:candidate-focus', event => {
    const card = document.getElementById('transport-cand-' + event.detail?.id);
    if (!card) return;
    const calm = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    card.scrollIntoView({behavior: calm ? 'auto' : 'smooth', block: 'nearest'});
    card.dataset.focus = 'true';
    clearTimeout(focusTimer);
    focusTimer = setTimeout(() => { delete card.dataset.focus; }, 1800);
  });
})();
