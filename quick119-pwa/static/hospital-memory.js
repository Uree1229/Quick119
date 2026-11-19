/* Original hospital modules receive this per-page Storage-compatible RAM adapter. */
(() => {
  const entries = new Map([['handler', '시연 담당자']]);
  window.quick119MemoryStorage = {
    get length() { return entries.size; },
    key: i => [...entries.keys()][i] ?? null,
    getItem: key => entries.get(String(key)) ?? null,
    setItem: (key, value) => entries.set(String(key), String(value)),
    removeItem: key => entries.delete(String(key)),
    clear: () => entries.clear(),
  };
})();
