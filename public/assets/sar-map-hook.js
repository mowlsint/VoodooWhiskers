(() => {
  "use strict";
  if (!window.L || typeof window.L.map !== "function" || window.__voodooSarMapHookInstalled) return;
  window.__voodooSarMapHookInstalled = true;
  const originalMap = window.L.map;
  window.L.map = function (...args) {
    const map = originalMap.apply(this, args);
    window.__voodooLeafletMap = map;
    window.dispatchEvent(new CustomEvent("voodoo-map-ready", { detail: { map } }));
    return map;
  };
})();
