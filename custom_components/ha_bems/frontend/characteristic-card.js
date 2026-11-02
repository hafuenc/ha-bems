/* Read-only numeric curves from BEMS diagnostic entities. No editor or libraries. */
class BEMSCharacteristicCard extends HTMLElement {
  constructor() { super(); this.attachShadow({mode: "open"}); }
  setConfig(config) {
    if (!Array.isArray(config.entities) || !config.entities.length || config.entities.length > 8) {
      throw new Error("Select 1–8 BEMS characteristic entities");
    }
    this._config = {...config, entities: config.entities.map(x => typeof x === "string" ? {entity:x} : {...x})};
    if (this._config.entities.some(x => typeof x.entity !== "string" || !/^sensor\.[a-z0-9_]+$/.test(x.entity))) {
      throw new Error("Invalid characteristic sensor");
    }
    this._signature = null;
    if (this._hass) this.render();
  }
  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    const signature = this._config.entities.map(x => hass.states[x.entity]);
    if (this._signature && signature.every((x,i) => x === this._signature[i])) return;
    this._signature = signature;
    this.render();
  }
  getCardSize() { return 5; }
  render() {
    const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    const finite = x => typeof x === "number" && Number.isFinite(x);
    const colors = ["#d97706", "#2563eb", "#059669", "#9333ea"];
    const series = this._config.entities.map((item,i) => {
      const state = this._hass.states[item.entity];
      const a = state?.attributes || {};
      return {name:item.name || a.friendly_name || item.entity, color:item.color || colors[i%colors.length],
        attributes:a, points:Array.isArray(a.curve_points) ? a.curve_points.filter(p => finite(p.temperature_c) && finite(p.value)) : []};
    });
    const points = series.flatMap(s => s.points);
    if (!points.length) {
      this.shadowRoot.innerHTML = `<ha-card><div style="padding:20px">${esc(this._config.title || "BEMS characteristic")} · Waiting for characteristic data</div></ha-card>`;
      return;
    }
    const xMin = Math.min(...points.map(p => p.temperature_c)), xMax = Math.max(...points.map(p => p.temperature_c));
    const values = points.map(p => p.value).concat(series.flatMap(s => (s.attributes.reference_points || []).map(p => p.value))).filter(finite);
    const yMin = Math.min(0,...values), yTop = Math.max(1,...values);
    const yMax = yTop + (yTop-yMin)*.08;
    const W=900,H=330,L=64,R=22,T=18,B=50;
    const x = v => L+(v-xMin)/Math.max(1e-9,xMax-xMin)*(W-L-R);
    const y = v => H-B-(v-yMin)/Math.max(1e-9,yMax-yMin)*(H-T-B);
    const fmt = v => Number(v.toFixed(2)).toLocaleString(this._hass.locale?.language || "en");
    let graph = "";
    for(let i=0;i<=5;i++) {
      const yy=yMin+(yMax-yMin)*i/5;
      graph+=`<line class="grid" x1="${L}" x2="${W-R}" y1="${y(yy)}" y2="${y(yy)}"/><text x="${L-9}" y="${y(yy)+4}" text-anchor="end">${fmt(yy)}</text>`;
    }
    const firstTick=Math.ceil(xMin/10)*10;
    for(let xx=firstTick;xx<=xMax;xx+=10) {
      graph+=`<line class="grid" x1="${x(xx)}" x2="${x(xx)}" y1="${T}" y2="${H-B}"/><text x="${x(xx)}" y="${H-B+21}" text-anchor="middle">${fmt(xx)}</text>`;
    }
    for(const s of series) {
      let segment=[], kind=null;
      const flush=()=>{
        if(segment.length>1) graph+=`<polyline fill="none" stroke="${esc(kind==='fallback'?'#dc2626':s.color)}" stroke-width="2.5" ${kind==='extrapolation' || kind==='fallback'?'stroke-dasharray="7 5"':''} points="${segment.map(p=>`${x(p.temperature_c)},${y(p.value)}`).join(' ')}"/>`;
      };
      for(let i=0;i<s.points.length;i++) {
        const p=s.points[i], prev=s.points[i-1];
        const nextKind=p.fallback_active?'fallback':p.region;
        if(kind!==nextKind) {flush();segment=prev?[prev,p]:[p];kind=nextKind;} else segment.push(p);
      }
      flush();
      for(const p of s.attributes.reference_points || []) {
        if(finite(p.temperature_c) && finite(p.value)) graph+=`<circle cx="${x(p.temperature_c)}" cy="${y(p.value)}" r="4" fill="var(--card-background-color,white)" stroke="${esc(s.color)}" stroke-width="2"><title>${esc(s.name)}: ${fmt(p.temperature_c)} °C / ${fmt(p.value)}</title></circle>`;
      }
      const a=s.attributes;
      if(finite(a.current_temperature_c) && finite(a.current_value) && a.current_temperature_c>=xMin && a.current_temperature_c<=xMax) {
        graph+=`<circle cx="${x(a.current_temperature_c)}" cy="${y(a.current_value)}" r="6" fill="${esc(s.color)}" stroke="var(--primary-text-color,black)" stroke-width="2"><title>${esc(s.name)} · current: ${fmt(a.current_temperature_c)} °C / ${fmt(a.current_value)}</title></circle>`;
      }
    }
    const unit=this._config.y_label || (series[0].attributes.output_unit==='h/24h'?'Required runtime (h/24h)':'COP');
    graph+=`<text x="${(L+W-R)/2}" y="${H-7}" text-anchor="middle">Outdoor temperature (°C)</text><text transform="translate(17 ${(T+H-B)/2}) rotate(-90)" text-anchor="middle">${esc(unit)}</text>`;
    const legend=series.map(s=>{
      const a=s.attributes;
      const current=finite(a.current_value)?`${fmt(a.current_value)}${a.output_unit==='h/24h'?' h/24h':''}${finite(a.current_temperature_c)?' at '+fmt(a.current_temperature_c)+' °C':''}`:'current input unavailable';
      return `<span><i style="background:${esc(s.color)}"></i>${esc(s.name)} · ${esc(current)}${a.fallback_active?' · fallback':''}</span>`;
    }).join('');
    this.shadowRoot.innerHTML=`<style>
      :host{display:block}ha-card{overflow:hidden}.title{padding:18px 18px 4px;font-size:18px;color:var(--primary-text-color)}
      svg{width:100%;height:auto;display:block}svg text{font:12px sans-serif;fill:var(--secondary-text-color,#666)}
      .grid{stroke:var(--divider-color,#ddd);stroke-dasharray:3 4}.legend{padding:0 18px;display:flex;flex-wrap:wrap;gap:8px 20px;font-size:13px;color:var(--primary-text-color)}
      .legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}.note{padding:12px 18px 16px;font-size:12px;color:var(--secondary-text-color)}
    </style><ha-card><div class="title">${esc(this._config.title || "BEMS characteristic")}</div>
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(this._config.title || unit)}">${graph}</svg>
      <div class="legend">${legend}</div><div class="note">Hollow points: reference values · solid: interpolation · dashed: extrapolation · filled points: current values. Runtime limits and heating cutoff are included; red segments indicate fallback COP.</div></ha-card>`;
  }
}
if(!customElements.get("bems-characteristic-card"))customElements.define("bems-characteristic-card",BEMSCharacteristicCard);
window.customCards=window.customCards || [];
if(!window.customCards.some(c=>c.type==='bems-characteristic-card'))window.customCards.push({type:'bems-characteristic-card',name:'BEMS characteristic',description:'Read-only COP and runtime curves from BEMS diagnostic sensors.'});
