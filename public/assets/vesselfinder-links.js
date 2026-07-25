(() => {
  "use strict";

  const digits = (value) => String(value ?? "").replace(/\D/g, "");

  function propertiesForSource(source) {
    const featureProps = source?.feature?.properties || {};
    const vessel = featureProps.vessel || {};
    return {
      imo: featureProps.imo || vessel.imo,
      mmsi: featureProps.mmsi || vessel.mmsi,
    };
  }

  function urlFor(props) {
    const imo = digits(props.imo);
    const mmsi = digits(props.mmsi);
    const identifier = imo.length === 7 ? imo : (mmsi.length === 9 ? mmsi : "");
    return identifier ? `https://www.vesselfinder.com/vessels/details/${encodeURIComponent(identifier)}` : "";
  }

  function appendLink(popup) {
    const element = popup?.getElement?.();
    if (!element || element.querySelector(".vfPopupLink")) return;
    const url = urlFor(propertiesForSource(popup._source));
    if (!url) return;
    const content = element.querySelector(".leaflet-popup-content");
    if (!content) return;
    const wrapper = document.createElement("div");
    wrapper.className = "vfPopupLinkRow";
    const link = document.createElement("a");
    link.className = "vfPopupLink";
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "Open in VesselFinder ↗";
    wrapper.appendChild(link);
    content.appendChild(wrapper);
    popup.update?.();
  }

  function init(map) {
    map.on("popupopen", (event) => appendLink(event.popup));
  }

  if (window.__voodooLeafletMap) init(window.__voodooLeafletMap);
  else window.addEventListener("voodoo-map-ready", (event) => init(event.detail.map), { once: true });
})();
