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
  // Returning from the map (display:none -> shown) keeps the old position, so unread hospital messages stay unread;
  // a log squeezed to 0px while the keyboard opens is still shown, so it stays pinned to the bottom.
  const log = byId('messages');
  if (log && window.ResizeObserver) {
    let pinned = true, shown = false;
    log.addEventListener('scroll', () => { pinned = log.scrollHeight - log.scrollTop - log.clientHeight < 48; }, {passive: true});
    new ResizeObserver(() => {
      const wasShown = shown;
      shown = log.getClientRects().length > 0;
      if (wasShown && shown && pinned && byId('mark-read')?.hidden !== false) log.scrollTop = log.scrollHeight;
    }).observe(log);
  }
  // Typing mode: while the composer has focus and the soft keyboard takes a large share of the screen, CSS folds the
  // brand bar, the header rows and the dock extras (data-kb="open"). Android shrinks the layout itself
  // (interactive-widget=resizes-content); iOS Safari does not, so the visible height is copied into --tw-vvh.
  const input = byId('message'), form = byId('message-form'), vv = window.visualViewport;
  if (input && form && typeof window.innerHeight === 'number') {
    const page = document.documentElement, composer = input.closest('.tw-composer');
    const embedded = !root.closest('.app-shell--workspace');
    let width = window.innerWidth, tallest = window.innerHeight, blurTimer = null, revealQueued = false;
    let openHeight = 0, openTop = 0, alignWorkspace = false;
    const zoomed = () => !!vv && vv.scale > 1.05;
    function revealComposer() {
      revealQueued = false;
      const align = alignWorkspace;
      alignWorkspace = false;
      if (root.dataset.kb !== 'open' || document.activeElement !== input || zoomed()) return;
      // Safari can pan the visual viewport independently of layout coordinates. The embedded chat
      // also has a long results page above it, so scroll only the distance needed to expose the controls.
      const top = vv?.offsetTop || 0, bottom = top + (vv?.height ?? window.innerHeight);
      let box = composer.getBoundingClientRect();
      if (align && embedded) {
        const bounds = root.getBoundingClientRect();
        // The OS may have centered the old input before compact layout. Include the message log
        // when the whole workspace fits. In a crowded/taller layout the composer takes priority.
        if (bounds.height <= bottom - top + 1 && box.top >= bounds.top - 1 && box.bottom <= bounds.bottom + 1) box = bounds;
      }
      if (box.bottom > bottom + 1) window.scrollBy(0, Math.ceil(box.bottom - bottom));
      else if (box.top < top - 1) window.scrollBy(0, Math.floor(box.top - top));
    }
    function measure() {
      const typing = document.activeElement === input, wasOpen = root.dataset.kb === 'open';
      const height = vv?.height ?? window.innerHeight, nextWidth = window.innerWidth;
      const widthChanged = nextWidth !== width;
      if (!zoomed() && height > 0) {
        if (widthChanged) {
          // Rotation can report the new height with the keyboard already subtracted.
          // When the axes have swapped, the old width approximates the new unobscured height.
          if (wasOpen && Math.abs(nextWidth - tallest) < Math.abs(nextWidth - width)) tallest = width;
          else if (!wasOpen) tallest = height;
          width = nextWidth;
        }
        tallest = Math.max(tallest, height);
        const drop = tallest - height;
        if (typing && (wasOpen ? drop >= 80 : drop > 150)) {
          root.dataset.kb = 'open';
          const resized = !wasOpen || widthChanged || openHeight !== height;
          const top = vv?.offsetTop || 0;
          if (!resized && top === openTop) return; // Ordinary document scrolling is up to the user.
          if (resized) page.style.setProperty('--tw-vvh', Math.round(height) + 'px');
          alignWorkspace ||= resized;
          openHeight = height;
          openTop = top;
          if (!revealQueued) {
            revealQueued = true;
            requestAnimationFrame(revealComposer);
          }
          return;
        }
      } else if (typing) return; // Ignore pinch zoom and transient empty rotation frames.
      if (wasOpen) {
        delete root.dataset.kb;
        page.style.removeProperty('--tw-vvh');
      }
    }
    window.addEventListener('resize', measure);
    vv?.addEventListener('resize', measure);
    vv?.addEventListener('scroll', measure);
    form.addEventListener('focusin', () => { clearTimeout(blurTimer); measure(); });
    // A short grace period: focus that leaves and comes straight back does not unfold and refold the header.
    form.addEventListener('focusout', () => { clearTimeout(blurTimer); blurTimer = setTimeout(measure, 150); });
    measure();
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
