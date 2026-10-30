/* Source entities remain owned by their integrations. This panel only navigates. */
export function inputState(input, states) {
  const source = states[input.source_entity];
  const status = states[input.status_entity];
  return { source, status, label: !input.enabled ? "Disabled" : status?.state ?? "Unavailable" };
}

export function entityPath(entityId) {
  return `/ha-bems-forecasts?${new URLSearchParams({ "more-info-entity-id": entityId })}`;
}

const TYPES = {
  pv_forecast_import: "PV generation",
  load_forecast_import: "Building load",
  electricity_import_price_forecast: "Electricity import price",
  electricity_export_price_forecast: "Electricity export price",
  outdoor_temperature_forecast: "Outdoor temperature",
};

class ForecastOverview extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; height:100%; overflow:auto; color:var(--primary-text-color,#212121); background:var(--primary-background-color,#fafafa); font-family:var(--paper-font-body1_-_font-family,Roboto,Arial,sans-serif); }
        header { display:flex; align-items:center; gap:16px; padding:16px 24px; background:var(--app-header-background-color,var(--card-background-color,#fff)); border-bottom:1px solid var(--divider-color,#ddd); }
        h1 { font-size:20px; font-weight:500; margin:0; flex:1; }
        a { color:var(--primary-color,#03a9f4); text-decoration:none; overflow-wrap:anywhere; }
        a:hover { text-decoration:underline; }
        button { font:inherit; border:1px solid var(--divider-color,#ddd); color:var(--primary-color,#03a9f4); background:var(--card-background-color,#fff); border-radius:8px; padding:9px 14px; cursor:pointer; }
        button:disabled { opacity:.5; cursor:wait; }
        main { max-width:1100px; margin:auto; padding:24px; }
        .intro,.muted { color:var(--secondary-text-color,#666); }
        .intro { margin:0 0 24px; line-height:1.5; }
        #message { margin-bottom:16px; }
        #cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,450px),1fr)); gap:16px; }
        article { background:var(--card-background-color,#fff); border:1px solid var(--divider-color,#ddd); border-radius:12px; padding:20px; }
        .heading { display:flex; align-items:start; justify-content:space-between; gap:12px; }
        h2 { font-size:18px; font-weight:500; margin:0 0 6px; }
        .type { font-size:14px; margin-bottom:20px; }
        .badge { border-radius:16px; padding:5px 10px; font-size:13px; background:var(--secondary-background-color,#eee); white-space:nowrap; }
        .ready { color:var(--success-color,#22863a); }
        .invalid { color:var(--error-color,#c62828); }
        dl { margin:0; display:grid; grid-template-columns:90px minmax(0,1fr); gap:14px 16px; font-size:14px; line-height:1.5; }
        dt { color:var(--secondary-text-color,#666); } dd { margin:0; min-width:0; }
        .entity-id { font-size:12px; color:var(--secondary-text-color,#666); overflow-wrap:anywhere; }
        .reason { margin:16px 0 0; font-size:14px; overflow-wrap:anywhere; }
        @media(max-width:600px) { header { padding:12px 16px; gap:12px; } main { padding:16px; } h1 { font-size:18px; } dl { grid-template-columns:70px minmax(0,1fr); } }
      </style>
      <header><a id="back" aria-label="Back to forecast importer">←</a><h1>BEMS forecast importer</h1><button id="refresh">Refresh</button></header>
      <main><p class="intro">Open a forecast source to view its current value and forecast attributes. Input status shows whether BEMS can use the forecast.</p><div id="message" role="status"></div><div id="cards"></div></main>`;
    this.shadowRoot.getElementById("refresh").onclick = () => this.load();
  }

  set hass(value) {
    this._hass = value;
    if (!this._loaded && !this._loading && this.isConnected) this.load();
    else if (this._loaded) this.render();
  }

  connectedCallback() {
    if (this._hass) this.load();
    this._timer = setInterval(() => this.load(), 30000);
  }

  disconnectedCallback() { clearInterval(this._timer); }

  async load() {
    if (!this._hass || this._loading) return;
    this._loading = true;
    this.shadowRoot.getElementById("refresh").disabled = true;
    try {
      const result = await this._hass.callWS({ type: "ha_bems/forecast_sources" });
      this._inputs = result.inputs;
      this._device = result.importer_device_id;
      this._loaded = true;
      this._error = null;
    } catch (error) {
      this._error = `Could not load forecast inputs. ${error.message || "Use Refresh to try again."}`;
    } finally {
      this._loading = false;
      this.shadowRoot.getElementById("refresh").disabled = false;
      this.render();
    }
  }

  link(entityId, label) {
    const a = document.createElement("a");
    a.textContent = label;
    a.href = entityPath(entityId);
    a.onclick = event => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
    };
    return a;
  }

  date(value) {
    if (!value || !Number.isFinite(new Date(value).getTime())) return "Unknown";
    return new Intl.DateTimeFormat(this._hass.locale?.language || "en", {
      dateStyle: "medium", timeStyle: "short", timeZone: this._hass.config?.time_zone,
    }).format(new Date(value));
  }

  render() {
    const root = this.shadowRoot;
    root.getElementById("back").href = this._device ? `/config/devices/device/${encodeURIComponent(this._device)}` : "/config/integrations/integration/ha_bems";
    root.getElementById("message").textContent = this._error || (!this._loaded ? "Loading forecast inputs…" : !this._inputs.length ? "No forecast inputs configured. Add inputs through HA-BEMS Configure." : "");
    const cards = root.getElementById("cards");
    cards.replaceChildren();
    for (const input of this._inputs || []) {
      const { source, status, label } = inputState(input, this._hass.states);
      const card = document.createElement("article");
      // Only fixed markup is parsed; all entity names and values use textContent.
      card.innerHTML = `<div class="heading"><h2></h2><span class="badge"></span></div><div class="type muted"></div><dl></dl>`;
      card.querySelector("h2").textContent = input.name;
      const badge = card.querySelector(".badge");
      badge.textContent = label;
      if (label === "ready") badge.classList.add("ready");
      else if (input.enabled) badge.classList.add("invalid");
      card.querySelector(".type").textContent = input.input_type === "pv_forecast_import" && ["ac", "dc"].includes(input.pv_power_type) ? `PV ${input.pv_power_type.toUpperCase()} generation` : TYPES[input.input_type] || input.input_type;
      const dl = card.querySelector("dl");
      const add = (label, content) => {
        const dt = document.createElement("dt"); dt.textContent = label;
        const dd = document.createElement("dd");
        if (typeof content === "string") dd.textContent = content; else dd.append(content);
        dl.append(dt, dd); return dd;
      };
      const sourceCell = add("Source", this.link(input.source_entity, source?.attributes.friendly_name || input.source_entity));
      if (source?.attributes.friendly_name) {
        const id = document.createElement("div"); id.className = "entity-id"; id.textContent = input.source_entity; sourceCell.append(id);
      }
      add("Value", source ? (this._hass.formatEntityState?.(source) ?? `${source.state} ${source.attributes.unit_of_measurement || ""}`.trim()) : "Source entity missing");
      add("BEMS status", input.status_entity ? this.link(input.status_entity, label) : label);
      const attrs = status?.attributes || {};
      add("Coverage", !input.enabled ? "Input disabled" : status?.state === "ready" && attrs.input_forecast_start && attrs.input_forecast_end ? `${this.date(attrs.input_forecast_start)} – ${this.date(attrs.input_forecast_end)}` : "No validated forecast");
      if (input.enabled && attrs.reason) {
        const reason = document.createElement("p"); reason.className = "reason"; reason.textContent = attrs.reason; card.append(reason);
      }
      cards.append(card);
    }
  }
}

customElements.define("ha-bems-forecast-overview", ForecastOverview);
