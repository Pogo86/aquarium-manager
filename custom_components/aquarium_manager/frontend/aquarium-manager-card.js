class AquariumManagerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._built = false;
    this._modalItemId = null;
    this._modalMode = null;
    this._activeTab = "overview";
    this._speciesSearchResult = null;
    this._waterTestTimers = {};
    this._waterTimerTicker = null;
  }

  static getStubConfig() {
    return { tank_name: "Main Aquarium", entity_prefix: "main_aquarium" };
  }

  setConfig(config) {
    if (!config) throw new Error("Aquarium Manager card configuration is required");
    const tankName = config.tank_name || "Main Aquarium";
    const prefix = config.entity_prefix || tankName.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
    this._config = { tank_name: tankName, entity_prefix: prefix, entities: config.entities || {} };
    this._built = false;
    if (this._hass) {
      this._build();
      this._update();
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    if (!this._built) this._build();
    this._update();
  }

  disconnectedCallback() {
    if (this._waterTimerTicker) {
      window.clearInterval(this._waterTimerTicker);
      this._waterTimerTicker = null;
    }
  }

  getCardSize() { return 11; }

  _entity(key, domain, suffix) {
    return this._config.entities?.[key] || `${domain}.${this._config.entity_prefix}_${suffix}`;
  }

  _sensorEntity(key) {
    const map = {
      volume: ["sensor", "calculated_volume"],
      dimensions: ["sensor", "dimensions"],
      temperature: ["sensor", "temperature"],
      water_status: ["sensor", "water_status"],
      stocking: ["sensor", "stocking"],
      community: ["sensor", "community"],
      compatibility: ["sensor", "compatible_fish"],
      library: ["sensor", "fish_library"],
      heater_automation: ["sensor", "backup_heater_automation"],
      lighting_automation: ["sensor", "lighting_automation"],
      filter: ["sensor", "filter"],
      gh_delta: ["sensor", "gh_above_source_water"],
      kh_delta: ["sensor", "kh_above_source_water"],
      nitrate_delta: ["sensor", "nitrate_above_source_water"],
    };
    const [domain, suffix] = map[key];
    return this._entity(key, domain, suffix);
  }

  _switchEntity(key) {
    const map = {
      backup_heater: ["switch", "backup_heater"],
      lights_off_today: ["switch", "lights_off_today"],
    };
    const [domain, suffix] = map[key];
    return this._entity(key, domain, suffix);
  }

  _selectEntity(key) {
    const map = {
      backup_heater_mode: ["select", "backup_heater_mode"],
    };
    const [domain, suffix] = map[key];
    return this._entity(key, domain, suffix);
  }

  _numberEntity(key) {
    const map = {
      ammonia: ["number", "ammonia"], nitrite: ["number", "nitrite"], nitrate: ["number", "nitrate"],
      ph: ["number", "ph"], gh: ["number", "gh"], kh: ["number", "kh"],
      source_nitrate: ["number", "source_water_nitrate"], source_ph: ["number", "source_water_ph"],
      source_gh: ["number", "source_water_gh"], source_kh: ["number", "source_water_kh"],
    };
    const [domain, suffix] = map[key];
    return this._entity(key, domain, suffix);
  }

  _state(entityId) { return this._hass?.states?.[entityId] || null; }

  _display(entityId) {
    const state = this._state(entityId);
    if (!state || ["unknown", "unavailable"].includes(state.state)) return "—";
    const unit = state.attributes?.unit_of_measurement || "";
    return `${state.state}${unit ? ` ${unit}` : ""}`;
  }

  _build() {
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block;width:100%;max-width:100%;min-width:0;overflow:hidden} *{box-sizing:border-box} ha-card{width:100%;max-width:100%;min-width:0;overflow:hidden;container-type:inline-size;color:var(--primary-text-color);background:var(--ha-card-background,var(--card-background-color))}
        .hero{padding:20px;background:linear-gradient(135deg,color-mix(in srgb,var(--primary-color) 18%,transparent),transparent 62%);border-bottom:1px solid var(--divider-color)}
        .hero-top,.title-wrap,.section-title,.stock-meta,.modal-actions{display:flex;align-items:center}.hero-top{justify-content:space-between;gap:14px}.title-wrap{gap:12px}
        .tank-icon{width:44px;height:44px;display:grid;place-items:center;border-radius:13px;background:color-mix(in srgb,var(--primary-color) 15%,transparent)}
        .tank-icon ha-icon{color:var(--primary-color);--mdc-icon-size:27px} h1{font-size:21px;margin:0 0 3px}.subline{font-size:12px;color:var(--secondary-text-color)}
        .status{font-size:11px;font-weight:700;text-transform:uppercase;padding:6px 9px;border-radius:999px;background:var(--secondary-background-color)}
        .status.clear,.status.good{color:var(--success-color,#43a047);background:color-mix(in srgb,var(--success-color,#43a047) 16%,transparent)}
        .status.attention,.status.incomplete{color:var(--warning-color,#f9a825);background:color-mix(in srgb,var(--warning-color,#f9a825) 16%,transparent)}
        .status.danger,.status.conflict{color:var(--error-color);background:color-mix(in srgb,var(--error-color) 16%,transparent)}
        .summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:16px}.summary-item{padding:10px;border:1px solid var(--divider-color);border-radius:11px;background:color-mix(in srgb,var(--card-background-color) 80%,transparent)}
        .summary-label{font-size:10px;text-transform:uppercase;color:var(--secondary-text-color);margin-bottom:4px}.summary-value{font-size:15px;font-weight:650}
        .content{padding:18px 20px 22px;min-width:0;max-width:100%;overflow:hidden}.section{min-width:0;max-width:100%}.section+.section{margin-top:24px}.section-title{gap:7px;font-size:14px;font-weight:650;margin-bottom:11px;min-width:0;flex-wrap:wrap}.section-title ha-icon{--mdc-icon-size:18px;color:var(--secondary-text-color)}
        .community-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.range-card{border:1px solid var(--divider-color);border-radius:11px;padding:11px}.range-label{font-size:10px;color:var(--secondary-text-color);text-transform:uppercase}.range-value{font-size:16px;font-weight:650;margin-top:4px}.range-note{font-size:10px;color:var(--secondary-text-color);margin-top:3px}.range-card.conflict{border-color:var(--error-color)}
        .warnings{display:flex;flex-direction:column;gap:6px;margin-top:10px}.warning-line{display:flex;gap:8px;align-items:flex-start;padding:8px 10px;border-radius:9px;font-size:12px;line-height:1.35;background:color-mix(in srgb,var(--warning-color,#f9a825) 10%,transparent)}
        .warning-line.info{background:var(--secondary-background-color);color:var(--secondary-text-color)}.warning-line.danger{background:color-mix(in srgb,var(--error-color) 10%,transparent)}.warning-line ha-icon{--mdc-icon-size:16px;margin-top:1px}
        details.stock-panel{border:1px solid var(--divider-color);border-radius:12px;overflow:hidden} details.stock-panel summary{list-style:none;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 13px;cursor:pointer;background:var(--secondary-background-color)} details.stock-panel summary::-webkit-details-marker{display:none}
        .stock-summary-left{display:flex;align-items:center;gap:9px;font-weight:650}.stock-summary-left ha-icon{--mdc-icon-size:19px}.stock-meta{gap:7px;color:var(--secondary-text-color);font-size:11px}.chevron{transition:transform .18s}details[open] .chevron{transform:rotate(180deg)}
        .stock-body{padding:7px}.library-bar{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 8px 10px;color:var(--secondary-text-color);font-size:10px}.library-info{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.library-refresh{height:27px;padding:0 9px;font-size:10px;border-radius:8px}.stock-row{display:grid;grid-template-columns:minmax(0,1fr) auto auto;align-items:center;gap:10px;padding:9px 8px;border-radius:9px;cursor:pointer}.stock-row:hover{background:var(--secondary-background-color)}.stock-name{min-width:0}.stock-name strong{display:block;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.scientific{font-size:10px;color:var(--secondary-text-color);font-style:italic;margin-top:2px}.qty-pill,.warn-pill{font-size:11px;font-weight:700;border-radius:999px;padding:4px 7px;background:var(--secondary-background-color)}.warn-pill{color:var(--warning-color,#f9a825)}
        button{border:1px solid var(--divider-color);background:var(--secondary-background-color);color:var(--primary-text-color);border-radius:8px;padding:6px 9px;cursor:pointer;font:inherit;font-size:11px}.edit-btn{font-weight:650}.add-btn{margin:7px 8px 8px;width:calc(100% - 16px);padding:8px;border-style:dashed;color:var(--primary-color);font-weight:650;background:transparent}
        .empty{padding:16px;text-align:center;font-size:12px;color:var(--secondary-text-color)}
        .parameter-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.source-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.delta-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.parameter,.delta{border:1px solid var(--divider-color);border-radius:10px;padding:10px;background:var(--secondary-background-color)}.delta{background:transparent}.param-head{display:flex;justify-content:space-between;gap:8px;margin-bottom:5px}.param-name,.delta-label{font-size:11px;color:var(--secondary-text-color)}.param-unit{font-size:9px;color:var(--secondary-text-color)}.delta-value{font-size:15px;font-weight:650;margin-top:4px}
        input,textarea,select{box-sizing:border-box;width:100%;max-width:100%;min-width:0;font:inherit;color:var(--primary-text-color);background:transparent;border:1px solid var(--divider-color);border-radius:8px;padding:8px;outline:none}input:focus,textarea:focus,select:focus{border-color:var(--primary-color)}button{max-width:100%}.parameter input{border:0;border-bottom:1px solid var(--divider-color);border-radius:0;padding:3px 0;font-size:18px;font-weight:650}.source-grid .parameter input{font-size:16px}
        .equipment-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px;border:1px solid var(--divider-color);border-radius:11px}
        .filter-panel{border:1px solid var(--divider-color);border-radius:11px;padding:12px}
        .filter-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
        .filter-title{font-size:14px;font-weight:700}.filter-sub{font-size:11px;color:var(--secondary-text-color);margin-top:3px}
        .filter-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px;margin-top:10px}
        .filter-metric{border:1px solid var(--divider-color);border-radius:9px;padding:8px;min-width:0}
        .filter-metric-label{font-size:9px;text-transform:uppercase;color:var(--secondary-text-color)}
        .filter-metric-value{font-size:13px;font-weight:650;margin-top:3px;overflow-wrap:anywhere}
        .filter-stages{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:10px}
        .filter-stage{border:1px solid var(--divider-color);border-radius:9px;padding:9px;min-width:0}
        .filter-stage-title{font-size:11px;font-weight:700;margin-bottom:5px}.filter-media{font-size:10px;color:var(--secondary-text-color);line-height:1.5;overflow-wrap:anywhere}
        .filter-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.filter-actions button{border:1px solid var(--divider-color);background:var(--secondary-background-color);color:var(--primary-text-color);border-radius:9px;padding:8px 10px;cursor:pointer}
        .filter-editor-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.filter-editor-field label{display:block;font-size:10px;color:var(--secondary-text-color);margin-bottom:4px}.filter-editor-stage{margin-top:12px}.filter-editor-stage textarea{min-height:90px;font-size:12px}
.equipment-main{min-width:0}.equipment-name{font-size:14px;font-weight:650}.equipment-sub{font-size:11px;color:var(--secondary-text-color);margin-top:3px}.heater-auto{margin-top:7px;font-size:11px;display:flex;gap:7px;align-items:center;flex-wrap:wrap}.heater-pill{border:1px solid var(--divider-color);border-radius:999px;padding:3px 7px;background:var(--secondary-background-color)}.equipment-toggle{border:1px solid var(--divider-color);background:var(--secondary-background-color);color:var(--primary-text-color);border-radius:9px;padding:8px 12px;cursor:pointer;font-weight:650}.equipment-toggle.on{color:var(--success-color,#43a047);border-color:color-mix(in srgb,var(--success-color,#43a047) 50%,var(--divider-color))}.heater-mode-toggle{display:grid;grid-template-columns:1fr 1fr;min-width:150px;border:1px solid var(--divider-color);border-radius:10px;padding:3px;background:var(--secondary-background-color);gap:3px}.heater-mode-btn{border:0;background:transparent;color:var(--secondary-text-color);border-radius:7px;min-height:38px;padding:7px 13px;font-weight:700;cursor:pointer}.heater-mode-btn.active{background:var(--primary-color);color:var(--text-primary-color,#fff)}.heater-mode-btn:disabled{opacity:.5;cursor:default}.lighting-panel{margin-top:10px;border:1px solid var(--divider-color);border-radius:11px;padding:12px}.lighting-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.lighting-state{font-weight:700;text-transform:capitalize}.lighting-reason{font-size:11px;color:var(--secondary-text-color);margin-top:3px}.lighting-devices{font-size:11px;color:var(--secondary-text-color);margin-top:8px;display:flex;gap:8px;flex-wrap:wrap}.device-dot{display:inline-flex;align-items:center;gap:4px;padding:3px 7px;border-radius:999px;background:var(--secondary-background-color)}.device-dot.on{color:var(--success-color,#43a047)}.schedule-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin-top:10px}.schedule-item{border:1px solid var(--divider-color);border-radius:9px;padding:8px}.schedule-label{font-size:9px;text-transform:uppercase;color:var(--secondary-text-color)}.schedule-value{font-size:13px;font-weight:650;margin-top:3px}.lighting-actions{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}.lighting-actions button{border:1px solid var(--divider-color);background:var(--secondary-background-color);color:var(--primary-text-color);border-radius:9px;padding:8px 10px;cursor:pointer}.lighting-actions button.active{color:var(--error-color);border-color:color-mix(in srgb,var(--error-color) 45%,var(--divider-color))}.lighting-editor{margin-top:12px;padding-top:12px;border-top:1px solid var(--divider-color)}.lighting-editor-title{font-size:12px;font-weight:700;margin-bottom:8px}.lighting-editor-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.lighting-enabled{display:flex;align-items:center;gap:8px;margin-bottom:9px;font-size:11px}.lighting-enabled input{width:auto}.lighting-field label{display:block;font-size:10px;color:var(--secondary-text-color);margin:0 0 4px}.lighting-field input{font-size:13px;padding:8px}.lighting-preview{margin-top:9px;padding:9px;border-radius:9px;background:var(--secondary-background-color);font-size:10px;line-height:1.5;color:var(--secondary-text-color)}.lighting-save-row{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:9px}.lighting-save-message{font-size:10px;color:var(--secondary-text-color)}.lighting-save{background:var(--primary-color);color:var(--text-primary-color,#fff);border:0;font-weight:700;padding:8px 12px}.reading-submit{border:1px solid var(--divider-color);border-radius:12px;padding:13px}.reading-submit-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.reading-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:12px}.reading-submit-btn{border:0;border-radius:9px;background:var(--primary-color);color:var(--text-primary-color,#fff);padding:9px 14px;font-weight:700;cursor:pointer}.reading-submit-btn:disabled{opacity:.55;cursor:default}.reading-message{font-size:11px;color:var(--secondary-text-color)}.water-warning{display:none;margin-top:10px;padding:9px 10px;border-radius:9px;font-size:12px;background:color-mix(in srgb,var(--warning-color,#f9a825) 10%,transparent)}.water-warning.show{display:block}
        .species-search{display:flex;gap:8px;align-items:center}.species-search input{flex:1}.species-search button{padding:9px 13px;font-weight:700}.species-result{margin-top:14px}.species-preview{border:1px solid var(--divider-color);border-radius:12px;padding:13px}.species-preview-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:12px}.source-pills{display:flex;gap:6px;flex-wrap:wrap}.source-pill{font-size:10px;font-weight:700;border-radius:999px;padding:4px 7px;background:var(--secondary-background-color)}.source-pill.found{color:var(--success-color,#43a047)}.species-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.species-form .full{grid-column:1/-1}.species-form label{display:block;font-size:10px;color:var(--secondary-text-color);margin-bottom:4px}.species-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:13px}.species-message{font-size:11px;color:var(--secondary-text-color);margin-top:9px}.species-message.error{color:var(--error-color)}
        .modal-backdrop{position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.48);display:none;align-items:center;justify-content:center;padding:16px}.modal-backdrop.open{display:flex}.modal{width:min(620px,100%);max-height:min(780px,92vh);overflow:auto;background:var(--card-background-color);border-radius:16px;box-shadow:0 16px 50px rgba(0,0,0,.35)}
        .modal-head{position:sticky;top:0;background:var(--card-background-color);display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:17px 18px 12px;border-bottom:1px solid var(--divider-color);z-index:1}.modal-title{font-size:18px;font-weight:700}.modal-subtitle{font-size:12px;color:var(--secondary-text-color);font-style:italic;margin-top:3px}.icon-btn{padding:5px;display:grid;place-items:center;background:transparent}.icon-btn ha-icon{--mdc-icon-size:20px}.modal-body{padding:16px 18px 18px}.detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.detail{border:1px solid var(--divider-color);border-radius:10px;padding:10px}.detail-label{font-size:10px;text-transform:uppercase;color:var(--secondary-text-color)}.detail-value{font-size:13px;font-weight:600;margin-top:4px}.modal-section{margin-top:16px}.modal-section-title{font-size:12px;font-weight:700;margin-bottom:8px}.care-note{font-size:12px;line-height:1.4;padding:8px 10px;background:var(--secondary-background-color);border-radius:8px;margin-top:5px}.modal-actions{justify-content:flex-end;gap:8px;margin-top:18px}.primary{background:var(--primary-color);color:var(--text-primary-color,#fff);border-color:var(--primary-color);font-weight:650}.danger-btn{color:var(--error-color);margin-right:auto}
        .form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.form-field label{display:block;font-size:10px;color:var(--secondary-text-color);margin-bottom:4px}.form-field.full{grid-column:1/-1}.pair{display:grid;grid-template-columns:1fr 1fr;gap:7px}.small-help{font-size:10px;color:var(--secondary-text-color);margin-top:5px}.footer{margin-top:18px;padding-top:12px;border-top:1px solid var(--divider-color);font-size:10px;line-height:1.4;color:var(--secondary-text-color)}
        .tabs{display:flex;gap:5px;padding:8px 12px;border-bottom:1px solid var(--divider-color);background:var(--secondary-background-color);overflow-x:auto;overscroll-behavior-x:contain;scrollbar-width:none;-webkit-overflow-scrolling:touch;max-width:100%}.tabs::-webkit-scrollbar{display:none}.tab-btn{flex:0 0 auto;min-height:42px;padding:9px 12px;background:transparent;border-color:transparent;font-size:12px;font-weight:650;color:var(--secondary-text-color);white-space:nowrap}.tab-btn.active{background:var(--card-background-color);color:var(--primary-color);border-color:var(--divider-color);box-shadow:0 1px 2px rgba(0,0,0,.06)}.tab-page{display:none;min-width:0;max-width:100%}.tab-page.active{display:block}
        .section-title-row{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:11px}.section-title-row .section-title{margin-bottom:0}.water-value{border:1px solid var(--divider-color);border-radius:10px;padding:10px;background:var(--secondary-background-color);min-width:0}.water-value-main{font-size:18px;font-weight:650;margin-top:5px;overflow-wrap:anywhere}.water-value-label{font-size:11px;color:var(--secondary-text-color)}.water-update-btn{background:var(--primary-color);color:var(--text-primary-color,#fff);border:0;font-weight:700;padding:8px 11px;white-space:nowrap}.water-last-test{font-size:11px;color:var(--secondary-text-color);margin-top:9px}.water-test-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.water-test-field{min-width:0}.water-test-field>label{display:block;font-size:10px;color:var(--secondary-text-color);margin-bottom:4px}.water-test-input-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px;align-items:stretch}.water-test-input-row.no-timer{grid-template-columns:1fr}.test-timer-btn{min-width:98px;font-weight:700;color:var(--primary-color);white-space:nowrap}.test-timer-btn.running{color:var(--warning-color,#f9a825);border-color:color-mix(in srgb,var(--warning-color,#f9a825) 50%,var(--divider-color))}.water-test-note{font-size:10px;line-height:1.4;color:var(--secondary-text-color);margin-bottom:12px}.water-test-message{font-size:11px;color:var(--secondary-text-color);min-height:16px}
        @container (min-width:780px){
          .tab-page.active{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px;align-items:start}
          .tab-page.active>.section{margin-top:0;min-width:0}
          .tab-page.active>.footer{grid-column:1/-1;margin-top:0}
          .tab-page.active .community-grid,.tab-page.active .assessment-grid,.tab-page.active .source-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
          #tab_water>.section:nth-of-type(3){grid-column:1/-1}
          #tab_compat.active,#tab_library.active{display:block}
          #tab_compat .compat-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}
          #tab_library .species-preview{max-width:none}
        }
        .compat-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.compat-count{font-size:11px;color:var(--secondary-text-color);white-space:nowrap}.compat-list{display:flex;flex-direction:column;gap:7px}.compat-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:center;border:1px solid var(--divider-color);border-radius:11px;padding:10px 11px;cursor:pointer}.compat-row:hover{background:var(--secondary-background-color)}.compat-main{min-width:0}.compat-main strong{display:block;font-size:13px}.compat-sub{font-size:10px;color:var(--secondary-text-color);margin-top:3px;line-height:1.35}.compat-side{display:flex;align-items:center;gap:7px}.match-pill,.add-count{font-size:10px;font-weight:700;padding:4px 7px;border-radius:999px;background:var(--secondary-background-color);white-space:nowrap}.match-pill.high{color:var(--success-color,#43a047)}.match-pill.medium{color:var(--warning-color,#f9a825)}.match-pill.limited{color:var(--secondary-text-color)}.compat-add{font-weight:650;color:var(--primary-color)}
        .group-list{display:flex;flex-direction:column;gap:7px}.group-card{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:center;padding:10px 11px;border:1px solid var(--divider-color);border-radius:11px}.group-title{font-size:13px;font-weight:650}.group-sub{font-size:10px;color:var(--secondary-text-color);margin-top:3px}.add-count{color:var(--warning-color,#f9a825);font-size:11px}.bioload-note{margin-top:10px;padding:8px 10px;border-radius:8px;background:var(--secondary-background-color);font-size:10px;line-height:1.4;color:var(--secondary-text-color)}
        .assessment-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.assessment-card{border:1px solid var(--divider-color);border-radius:11px;padding:11px;min-width:0}.assessment-label{font-size:10px;text-transform:uppercase;color:var(--secondary-text-color)}.assessment-value{font-size:14px;font-weight:650;margin-top:4px;overflow-wrap:anywhere}.assessment-note{font-size:9px;line-height:1.3;color:var(--secondary-text-color);margin-top:3px}
        @media(max-width:700px){
          .hero,.content{padding-left:12px;padding-right:12px}.hero{padding-top:15px;padding-bottom:15px}.hero-top{gap:10px;flex-wrap:wrap}.title-wrap{min-width:0;flex:1}.title-wrap>div:last-child{min-width:0}h1{font-size:19px;overflow-wrap:anywhere}.status{flex:0 0 auto}
          .tabs{padding:7px 10px}.tab-btn{min-height:44px;padding:9px 12px;font-size:12px}
          .summary,.community-grid,.assessment-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.summary-value,.range-value,.assessment-value{overflow-wrap:anywhere}
          .parameter-grid,.source-grid,.reading-submit-grid,.lighting-editor-grid,.species-form,.detail-grid,.form-grid{grid-template-columns:1fr 1fr}.delta-grid{grid-template-columns:1fr}.form-field.full,.species-form .full{grid-column:1/-1}
          .lighting-top,.compat-head,.species-preview-head,.reading-actions,.lighting-save-row{flex-wrap:wrap}.lighting-save-message{flex:1 1 180px}.reading-message{flex:1 1 180px}
          .modal-backdrop{padding:8px;align-items:flex-end}.modal{width:100%;max-width:100%;max-height:94vh;border-radius:16px 16px 8px 8px}.modal-head,.modal-body{padding-left:13px;padding-right:13px}
        }
        @media(max-width:520px){
          .content{padding-top:14px;padding-bottom:18px}.section+.section{margin-top:18px}.section-title{font-size:13px}
          .summary,.community-grid,.assessment-grid,.parameter-grid,.source-grid,.reading-submit-grid,.lighting-editor-grid,.species-form,.detail-grid,.form-grid,.pair{grid-template-columns:1fr}.form-field.full,.species-form .full{grid-column:auto}
          .schedule-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
          .species-search{display:grid;grid-template-columns:1fr}.species-search button{width:100%;min-height:44px}.species-search input{font-size:16px;min-height:44px}
          .compat-row,.group-card{grid-template-columns:1fr}.compat-side{justify-content:space-between;flex-wrap:wrap}.compat-add{min-height:40px}.group-card .add-count{justify-self:start}
          .stock-row{grid-template-columns:minmax(0,1fr) auto}.stock-row .stock-meta{justify-self:end}.stock-row .edit-btn{grid-column:1/-1;width:100%;min-height:40px}.stock-name strong{white-space:normal;overflow:visible;text-overflow:clip;overflow-wrap:anywhere}
          details.stock-panel summary{align-items:flex-start}.stock-meta{flex-wrap:wrap;justify-content:flex-end;text-align:right}.library-bar{align-items:flex-start;flex-wrap:wrap}.library-info{white-space:normal;overflow-wrap:anywhere;flex:1 1 180px}.library-refresh{min-height:40px}
          .equipment-row{align-items:flex-start;flex-wrap:wrap}.equipment-toggle,.heater-mode-toggle{width:100%;min-height:44px}.lighting-actions{display:grid;grid-template-columns:1fr}.lighting-actions button,.lighting-save,.reading-submit-btn{width:100%;min-height:44px}
          .reading-actions,.lighting-save-row{display:grid;grid-template-columns:1fr}.reading-submit-btn,.lighting-save{grid-row:auto}.reading-message,.lighting-save-message{min-width:0}
          .section-title-row{align-items:flex-start;flex-wrap:wrap}.water-update-btn{width:100%;min-height:44px}.water-test-grid{grid-template-columns:1fr}.water-test-input-row{grid-template-columns:minmax(0,1fr) auto}.test-timer-btn{min-height:44px;min-width:104px}
          input,textarea,select{font-size:16px}.lighting-field input{font-size:16px}.parameter input{font-size:18px}
          .species-actions,.modal-actions{display:grid;grid-template-columns:1fr;width:100%}.species-actions button,.modal-actions button{width:100%;min-height:44px}.danger-btn{margin-right:0}
          .warning-line{overflow-wrap:anywhere}.small-help,.footer,.equipment-sub,.lighting-reason,.compat-sub,.group-sub,.bioload-note{overflow-wrap:anywhere}
          .filter-metrics,.filter-stages,.filter-editor-grid{grid-template-columns:1fr}.filter-actions{display:grid;grid-template-columns:1fr}.filter-actions button{width:100%;min-height:44px}
        }
        @media(max-width:360px){.summary,.community-grid,.assessment-grid,.schedule-grid{grid-template-columns:1fr}.hero-top{align-items:flex-start}.tank-icon{width:40px;height:40px}.tabs{padding-left:8px;padding-right:8px}.content{padding-left:10px;padding-right:10px}}
      </style>

      <ha-card>
        <div class="hero">
          <div class="hero-top">
            <div class="title-wrap"><div class="tank-icon"><ha-icon icon="mdi:fishbowl-outline"></ha-icon></div><div><h1>${this._escape(this._config.tank_name)}</h1><div class="subline">Aquarium Manager</div></div></div>
            <div id="water_status" class="status">—</div>
          </div>
          <div class="summary">
            <div class="summary-item"><div class="summary-label">Temperature</div><div id="temperature" class="summary-value">—</div></div>
            <div class="summary-item"><div class="summary-label">Volume</div><div id="volume" class="summary-value">—</div></div>
            <div class="summary-item"><div class="summary-label">Dimensions</div><div id="dimensions" class="summary-value">—</div></div>
            <div class="summary-item"><div class="summary-label">Livestock</div><div id="livestock_total" class="summary-value">—</div></div>
          </div>
        </div>

        <div class="tabs" role="tablist" aria-label="Aquarium Manager sections">
          <button id="tab_btn_overview" class="tab-btn active" role="tab" aria-selected="true">Overview</button>
          <button id="tab_btn_water" class="tab-btn" role="tab" aria-selected="false">Water</button>
          <button id="tab_btn_stocking" class="tab-btn" role="tab" aria-selected="false">Stocking</button>
          <button id="tab_btn_equipment" class="tab-btn" role="tab" aria-selected="false">Equipment</button>
          <button id="tab_btn_compat" class="tab-btn" role="tab" aria-selected="false">Compatible fish</button>
          <button id="tab_btn_library" class="tab-btn" role="tab" aria-selected="false">Species Library</button>
        </div>

        <div class="content">
          <div id="tab_overview" class="tab-page active" role="tabpanel">
            <section class="section">
              <div class="section-title"><ha-icon icon="mdi:scale-balance"></ha-icon>Stocking assessment <span id="stocking_assessment_status" class="status">—</span></div>
              <div id="stocking_assessment_grid" class="assessment-grid"></div>
              <div id="stocking_assessment_issues" class="warnings"></div>
              <div id="stocking_assessment_note" class="bioload-note"></div>
            </section>
            <section class="section">
              <div class="section-title"><ha-icon icon="mdi:chart-bell-curve"></ha-icon>Community ranges <span id="community_status" class="status">—</span></div>
              <div id="community_grid" class="community-grid"></div>
              <div id="community_warnings" class="warnings"></div>
            </section>
            <div class="footer">Overview combines the current stocking assessment and shared community ranges. Use the other tabs for editing and detailed controls.</div>
          </div>

          <div id="tab_water" class="tab-page" role="tabpanel">
            <section class="section">
              <div class="section-title-row"><div class="section-title"><ha-icon icon="mdi:test-tube"></ha-icon>Current aquarium water</div><button id="open_water_readings" class="water-update-btn">Update readings</button></div>
              <div class="parameter-grid">
                ${this._waterValueBox("ammonia","NH₃ / NH₄","mg/L")}${this._waterValueBox("nitrite","NO₂","mg/L")}${this._waterValueBox("nitrate","NO₃","mg/L")}${this._waterValueBox("ph","pH","")}${this._waterValueBox("gh","GH","dGH")}${this._waterValueBox("kh","KH","dKH")}
              </div>
              <div id="last_water_test" class="water-last-test">No submitted test recorded</div>
              <div id="water_warning" class="water-warning"></div>
            </section>

            <section class="section"><div class="section-title"><ha-icon icon="mdi:home-water"></ha-icon>Source water</div><div class="source-grid">
              ${this._numberBox("source_nitrate","NO₃","mg/L","0.5")}${this._numberBox("source_ph","pH","","0.1")}${this._numberBox("source_gh","GH","dGH","1")}${this._numberBox("source_kh","KH","dKH","1")}
            </div></section>

            <section class="section"><div class="section-title"><ha-icon icon="mdi:compare-horizontal"></ha-icon>Change from source water</div><div class="delta-grid">
              <div class="delta"><div class="delta-label">GH increase</div><div id="gh_delta" class="delta-value">—</div></div><div class="delta"><div class="delta-label">KH increase</div><div id="kh_delta" class="delta-value">—</div></div><div class="delta"><div class="delta-label">NO₃ increase</div><div id="nitrate_delta" class="delta-value">—</div></div>
            </div></section>
          </div>

          <div id="tab_stocking" class="tab-page" role="tabpanel">
            <section class="section">
              <details id="stock_panel" class="stock-panel" open>
                <summary><div class="stock-summary-left"><ha-icon icon="mdi:fish"></ha-icon>Current stocking</div><div class="stock-meta"><span id="stock_summary">0 fish · 0 groups</span><ha-icon class="chevron" icon="mdi:chevron-down"></ha-icon></div></summary>
                <div class="stock-body"><div class="library-bar"><span id="library_info" class="library-info">Fish library —</span><button id="refresh_library" class="library-refresh">Refresh</button></div><div id="stock_list"></div><button id="add_fish" class="add-btn">＋ Add fish</button></div>
              </details>
            </section>
            <section class="section">
              <div class="section-title"><ha-icon icon="mdi:account-multiple-plus-outline"></ha-icon>Group size suggestions</div>
              <div id="group_suggestions" class="group-list"></div>
            </section>
            <div class="footer">Group suggestions use minimum-group data from the active fish library and will flag tank-size constraints before suggesting additions.</div>
          </div>

          <div id="tab_equipment" class="tab-page" role="tabpanel">
            <section class="section"><div class="section-title"><ha-icon icon="mdi:radiator"></ha-icon>Backup heater</div>
              <div id="backup_heater_row" class="equipment-row"><div class="equipment-main"><div class="equipment-name">Backup heater</div><div id="backup_heater_sub" class="equipment-sub">Not configured</div><div id="backup_heater_details" class="heater-auto"></div></div><div class="heater-mode-toggle" aria-label="Backup heater mode"><button id="backup_heater_mode_auto" type="button" class="heater-mode-btn" disabled>Auto</button><button id="backup_heater_mode_off" type="button" class="heater-mode-btn" disabled>Off</button></div></div>
            </section>
            <section class="section"><div class="section-title"><ha-icon icon="mdi:air-filter"></ha-icon>Filter</div>
              <div id="filter_panel" class="filter-panel">
                <div class="filter-top"><div><div id="filter_name" class="filter-title">Fluval 307</div><div id="filter_sub" class="filter-sub">Loading filter data…</div></div></div>
                <div id="filter_metrics" class="filter-metrics"></div>
                <div id="filter_stages" class="filter-stages"></div>
                <div class="filter-actions"><button id="edit_filter" type="button">Edit filter & media</button><button id="mark_filter_cleaned" type="button">Mark filter cleaned</button></div>
              </div>
            </section>
            <section class="section"><div class="section-title"><ha-icon icon="mdi:lightbulb-on-outline"></ha-icon>Lighting & CO₂</div>
              <div id="lighting_panel" class="lighting-panel" style="margin-top:0">
                <div class="lighting-top"><div><div class="equipment-name">Lighting & CO₂ automation</div><div id="lighting_state" class="lighting-state">Not configured</div><div id="lighting_reason" class="lighting-reason"></div></div></div>
                <div id="lighting_devices" class="lighting-devices"></div>
                <div id="lighting_schedule" class="schedule-grid"></div>
                <div class="lighting-editor">
                  <div class="lighting-editor-title">Schedule settings</div>
                  <label class="lighting-enabled"><input id="lighting_input_enabled" type="checkbox"> Automation enabled</label>
                  <div class="lighting-editor-grid">
                    <div class="lighting-field"><label>Lights on</label><input id="lighting_input_on" type="time" step="60"></div>
                    <div class="lighting-field"><label>Main duration (hours)</label><input id="lighting_input_duration" type="number" min="4" max="10" step="0.5"></div>
                    <div class="lighting-field"><label>Half light at end (min)</label><input id="lighting_input_half" type="number" min="0" max="360" step="5"></div>
                    <div class="lighting-field"><label>Blue afterwards (min)</label><input id="lighting_input_blue" type="number" min="0" max="360" step="5"></div>
                    <div class="lighting-field"><label>CO₂ before lights (min)</label><input id="lighting_input_co2_lead" type="number" min="0" max="360" step="5"></div>
                    <div class="lighting-field"><label>CO₂ off before main end (min)</label><input id="lighting_input_co2_off" type="number" min="0" max="360" step="5"></div>
                  </div>
                  <div id="lighting_preview" class="lighting-preview">Configure the equipment to edit the schedule.</div>
                  <div class="lighting-save-row"><span id="lighting_save_message" class="lighting-save-message"></span><button id="lighting_save" class="lighting-save">Save schedule</button></div>
                </div>
                <div class="lighting-actions"><button id="lights_off_today">Lights off today</button><button id="resync_lighting">Resync light modes</button></div>
                <div class="equipment-sub" style="margin-top:8px">Mode cycling assumes a long power-off starts each lamp in Full mode; Resync forces a known OFF → Full start before reapplying the schedule.</div>
              </div>
            </section>
          </div>

          <div id="tab_compat" class="tab-page" role="tabpanel">
            <section class="section">
              <div class="compat-head">
                <div><div class="section-title"><ha-icon icon="mdi:fish-plus"></ha-icon>Compatible fish</div><div class="small-help">Fish already recorded in this aquarium are excluded. Recommendations use the library's known tank dimensions, water ranges and current readings. Temperament concerns are shown as cautions rather than hidden.</div></div>
                <div id="compat_count" class="compat-count">—</div>
              </div>
              <div id="compatible_list" class="compat-list"></div>
            </section>
            <div class="footer">Relative bioload is a relative planning index based on adult length cubed and quantity. It is useful for comparing additions, but it is not a safe-stock percentage.</div>
          </div>

          <div id="tab_library" class="tab-page" role="tabpanel">
            <section class="section">
              <div class="section-title"><ha-icon icon="mdi:database-plus-outline"></ha-icon>Add species to library</div>
              <div class="small-help">Search by common name or scientific name. Aquarium Manager checks SeriouslyFish and FishBase, lets you review/edit the values, then commits the approved record to your GitHub fish_library.json.</div>
              <div class="species-search"><input id="species_search_query" type="text" placeholder="e.g. Neon Tetra or Paracheirodon innesi"><button id="species_search_btn" class="primary">Search</button></div>
              <div id="species_search_message" class="species-message"></div>
              <div id="species_search_result" class="species-result"></div>
            </section>
          </div>
        </div>
      </ha-card>

      <div id="modal_backdrop" class="modal-backdrop"><div id="modal" class="modal"></div></div>
    `;

    this.shadowRoot.querySelectorAll("input[data-key]").forEach(input => {
      input.addEventListener("change", e => this._saveNumber(e));
      input.addEventListener("keydown", e => { if (e.key === "Enter") e.currentTarget.blur(); });
    });
    this.shadowRoot.getElementById("add_fish").addEventListener("click", e => { e.preventDefault(); e.stopPropagation(); this._openEdit(null); });
    this.shadowRoot.getElementById("refresh_library").addEventListener("click", e => { e.preventDefault(); e.stopPropagation(); this._refreshLibrary(); });
    this.shadowRoot.getElementById("backup_heater_mode_auto").addEventListener("click", () => this._setBackupHeaterMode("Auto"));
    this.shadowRoot.getElementById("backup_heater_mode_off").addEventListener("click", () => this._setBackupHeaterMode("Off"));
    this.shadowRoot.getElementById("edit_filter").addEventListener("click", () => this._openFilterEditor());
    this.shadowRoot.getElementById("mark_filter_cleaned").addEventListener("click", () => this._markFilterCleaned());
    this.shadowRoot.getElementById("lights_off_today").addEventListener("click", () => this._toggleLightsOffToday());
    this.shadowRoot.getElementById("resync_lighting").addEventListener("click", () => this._resyncLighting());
    this.shadowRoot.getElementById("lighting_save").addEventListener("click", () => this._saveLightingSchedule());
    for (const id of ["lighting_input_enabled","lighting_input_on","lighting_input_duration","lighting_input_half","lighting_input_blue","lighting_input_co2_lead","lighting_input_co2_off"]) {
      this.shadowRoot.getElementById(id)?.addEventListener("input", e => { e.currentTarget.dataset.dirty = "1"; this._renderLightingPreview(); });
    }
    this.shadowRoot.getElementById("open_water_readings").addEventListener("click", () => this._openWaterReadings());
    for (const tab of ["overview","water","stocking","equipment","compat","library"]) {
      this.shadowRoot.getElementById(`tab_btn_${tab}`)?.addEventListener("click", () => this._setTab(tab));
    }
    this.shadowRoot.getElementById("species_search_btn").addEventListener("click", () => this._searchSpecies());
    this.shadowRoot.getElementById("species_search_query").addEventListener("keydown", e => { if (e.key === "Enter") this._searchSpecies(); });
    this.shadowRoot.getElementById("modal_backdrop").addEventListener("click", e => { if (e.target.id === "modal_backdrop") this._closeModal(); });
    this._built = true;
  }

  _numberBox(key,label,unit,step){return `<div class="parameter"><div class="param-head"><span class="param-name">${label}</span><span class="param-unit">${unit}</span></div><input data-key="${key}" type="number" inputmode="decimal" step="${step}" placeholder="—"></div>`;}
  _waterValueBox(key,label,unit){return `<div class="water-value"><div class="water-value-label">${label}${unit ? ` · ${unit}` : ""}</div><div id="water_value_${key}" class="water-value-main">—</div></div>`;}

  _update() {
    if (!this._built || !this._hass) return;
    this._setText("temperature", this._display(this._sensorEntity("temperature")));
    this._setText("volume", this._display(this._sensorEntity("volume")));
    this._setText("dimensions", this._display(this._sensorEntity("dimensions")));
    this._setText("gh_delta", this._signedDisplay(this._sensorEntity("gh_delta")));
    this._setText("kh_delta", this._signedDisplay(this._sensorEntity("kh_delta")));
    this._setText("nitrate_delta", this._signedDisplay(this._sensorEntity("nitrate_delta")));

    for (const key of ["ammonia","nitrite","nitrate","ph","gh","kh"]) {
      this._setText(`water_value_${key}`, this._display(this._numberEntity(key)));
    }

    const active = this.shadowRoot.activeElement;
    this.shadowRoot.querySelectorAll("input[data-key]").forEach(input => {
      if (input === active) return;
      const state = this._state(this._numberEntity(input.dataset.key));
      input.value = state && !["unknown","unavailable"].includes(state.state) ? state.state : "";
      input.disabled = !state;
    });

    const water = this._state(this._sensorEntity("water_status"));
    this._setStatus("water_status", water?.state || "unknown");
    this._renderBackupHeater();
    this._renderLighting();
    this._renderFilter();
    this._renderLastWaterTest(water);
    const reasons = water?.attributes?.reasons || [];
    const ww = this.shadowRoot.getElementById("water_warning");
    if (reasons.length) { ww.innerHTML = reasons.map(x => `• ${this._escape(x)}`).join("<br>"); ww.className = "water-warning show"; }
    else { ww.textContent = ""; ww.className = "water-warning"; }

    this._renderStockingAssessment();
    this._renderCommunity();
    this._renderLibrary();
    this._renderStocking();
    this._renderCompatibility();
    this._setTab(this._activeTab);
  }


  _renderFilter() {
    const state = this._state(this._sensorEntity("filter"));
    const name = this.shadowRoot.getElementById("filter_name");
    const sub = this.shadowRoot.getElementById("filter_sub");
    const metrics = this.shadowRoot.getElementById("filter_metrics");
    const stages = this.shadowRoot.getElementById("filter_stages");
    const edit = this.shadowRoot.getElementById("edit_filter");
    const cleaned = this.shadowRoot.getElementById("mark_filter_cleaned");
    if (!name || !sub || !metrics || !stages || !edit || !cleaned) return;
    if (!state || ["unknown","unavailable"].includes(state.state)) {
      name.textContent = "Filter";
      sub.textContent = "Filter data unavailable — restart Home Assistant after upgrading.";
      metrics.innerHTML = "";
      stages.innerHTML = "";
      edit.disabled = true;
      cleaned.disabled = true;
      return;
    }
    const a = state.attributes || {};
    const summary = a.summary || {};
    const filter = a.filter || {};
    name.textContent = state.state || "Filter";
    const turnover = Number.isFinite(Number(summary.turnover_per_hour)) ? Number(summary.turnover_per_hour).toFixed(1) + "×/h" : "—";
    const source = summary.flow_source === "measured" ? "measured flow" : "rated circulation";
    sub.textContent = turnover === "—" ? "Turnover unavailable" : turnover + " using " + source;

    const maintenance = summary.maintenance_status === "overdue"
      ? "Overdue"
      : summary.maintenance_status === "due_soon"
        ? "Due soon"
        : summary.next_maintenance
          ? this._shortDate(summary.next_maintenance)
          : "Not recorded";
    const flow = Number.isFinite(Number(summary.effective_flow_lph)) ? Number(summary.effective_flow_lph).toFixed(0) + " L/h" : "—";
    const rated = summary.rated_tank_min_l != null && summary.rated_tank_max_l != null
      ? Number(summary.rated_tank_min_l).toFixed(0) + "–" + Number(summary.rated_tank_max_l).toFixed(0) + " L"
      : "—";
    metrics.innerHTML = [
      ["Flow", flow],
      ["Turnover", turnover],
      ["Rated tank", rated],
      ["Maintenance", maintenance],
    ].map(([label,value]) => '<div class="filter-metric"><div class="filter-metric-label">' + this._escape(label) + '</div><div class="filter-metric-value">' + this._escape(value) + '</div></div>').join("");

    const rows = Array.isArray(filter.stages) ? filter.stages : [];
    stages.innerHTML = rows.map(stage => {
      const media = Array.isArray(stage.media) ? stage.media : [];
      const body = media.length
        ? media.map(item => this._escape((item.type ? item.type.charAt(0).toUpperCase() + item.type.slice(1) + ": " : "") + (item.name || "Unnamed media"))).join("<br>")
        : "Empty";
      return '<div class="filter-stage"><div class="filter-stage-title">' + this._escape(stage.name || "Stage") + '</div><div class="filter-media">' + body + '</div></div>';
    }).join("");
    edit.disabled = false;
    cleaned.disabled = false;
  }

  _shortDate(value) {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleDateString();
  }

  _filterMediaLines(stage) {
    const media = Array.isArray(stage?.media) ? stage.media : [];
    return media.map(item => [item.type || "other", item.name || "", item.amount || ""].join(" | ")).join("\n");
  }

  _openFilterEditor() {
    const state = this._state(this._sensorEntity("filter"));
    const filter = state?.attributes?.filter;
    if (!filter) return;
    const modal = this.shadowRoot.getElementById("modal");
    const backdrop = this.shadowRoot.getElementById("modal_backdrop");
    const stages = Array.isArray(filter.stages) ? filter.stages : [];
    const stageHtml = stages.map((stage,index) =>
      '<div class="filter-editor-stage"><div class="filter-stage-title">' + this._escape(stage.name || ("Stage " + (index + 1))) + '</div>' +
      '<div class="small-help">One item per line: type | media name | optional amount</div>' +
      '<textarea id="filter_stage_' + index + '">' + this._escape(this._filterMediaLines(stage)) + '</textarea></div>'
    ).join("");
    modal.innerHTML = '<h2 style="margin-top:0">Filter & media</h2>' +
      '<div class="filter-editor-grid">' +
      '<div class="filter-editor-field"><label>Manufacturer</label><input id="filter_manufacturer" value="' + this._escape(filter.manufacturer || "") + '"></div>' +
      '<div class="filter-editor-field"><label>Model</label><input id="filter_model" value="' + this._escape(filter.model || "") + '"></div>' +
      '<div class="filter-editor-field"><label>Rated circulation (L/h)</label><input id="filter_rated_flow" type="number" min="0" step="1" value="' + this._escape(filter.rated_circulation_lph ?? "") + '"></div>' +
      '<div class="filter-editor-field"><label>Measured flow (L/h, optional)</label><input id="filter_measured_flow" type="number" min="0" step="1" value="' + this._escape(filter.measured_flow_lph ?? "") + '"></div>' +
      '<div class="filter-editor-field"><label>Rated tank minimum (L)</label><input id="filter_rated_min" type="number" min="0" step="1" value="' + this._escape(filter.rated_tank_min_l ?? "") + '"></div>' +
      '<div class="filter-editor-field"><label>Rated tank maximum (L)</label><input id="filter_rated_max" type="number" min="0" step="1" value="' + this._escape(filter.rated_tank_max_l ?? "") + '"></div>' +
      '<div class="filter-editor-field"><label>Maintenance interval (days)</label><input id="filter_maintenance_days" type="number" min="1" max="3650" step="1" value="' + this._escape(filter.maintenance_interval_days ?? 30) + '"></div>' +
      '<div class="filter-editor-field"><label>Basket media volume (L)</label><input id="filter_media_volume" type="number" min="0" step="0.1" value="' + this._escape(filter.media_basket_volume_l ?? "") + '"></div>' +
      '</div>' + stageHtml +
      '<div class="modal-actions"><button id="filter_cancel" type="button">Cancel</button><button id="filter_save" type="button" class="primary">Save filter</button></div>';
    backdrop.classList.add("show");
    modal.querySelector("#filter_cancel")?.addEventListener("click", () => this._closeModal());
    modal.querySelector("#filter_save")?.addEventListener("click", () => this._saveFilterEditor(filter, stages));
  }

  _parseFilterMedia(text) {
    return String(text || "").split(/\r?\n/).map(line => line.trim()).filter(Boolean).map(line => {
      const parts = line.split("|").map(x => x.trim());
      const typeRaw = (parts[0] || "other").toLowerCase();
      const type = ["mechanical","biological","chemical","other"].includes(typeRaw) ? typeRaw : "other";
      return {type, name: parts[1] || parts[0] || "Media", amount: parts[2] || "", notes: "", last_serviced: null, replace_interval_days: null};
    });
  }

  async _saveFilterEditor(original, stages) {
    const modal = this.shadowRoot.getElementById("modal");
    const { entryId } = this._stockData();
    if (!modal || !entryId) return;
    const num = id => {
      const raw = modal.querySelector("#" + id)?.value ?? "";
      if (raw === "") return null;
      const value = Number(raw);
      return Number.isFinite(value) ? value : null;
    };
    const updated = JSON.parse(JSON.stringify(original || {}));
    updated.manufacturer = modal.querySelector("#filter_manufacturer")?.value?.trim() || "Fluval";
    updated.model = modal.querySelector("#filter_model")?.value?.trim() || "307";
    updated.rated_circulation_lph = num("filter_rated_flow");
    updated.measured_flow_lph = num("filter_measured_flow");
    updated.rated_tank_min_l = num("filter_rated_min");
    updated.rated_tank_max_l = num("filter_rated_max");
    updated.maintenance_interval_days = num("filter_maintenance_days") || 30;
    updated.media_basket_volume_l = num("filter_media_volume");
    updated.stages = stages.map((stage,index) => ({
      ...stage,
      media: this._parseFilterMedia(modal.querySelector("#filter_stage_" + index)?.value || "")
    }));
    const button = modal.querySelector("#filter_save");
    if (button) button.disabled = true;
    try {
      await this._hass.callService("aquarium_manager","update_filter",{entry_id:entryId,filter:updated}, {}, true, true);
      this._closeModal();
    } catch (err) {
      if (button) button.disabled = false;
      window.alert("Could not save filter: " + (err?.message || err));
    }
  }

  async _markFilterCleaned() {
    const { entryId } = this._stockData();
    if (!entryId) return;
    const button = this.shadowRoot.getElementById("mark_filter_cleaned");
    if (button) button.disabled = true;
    try {
      await this._hass.callService("aquarium_manager","mark_filter_cleaned",{entry_id:entryId}, {}, true, true);
    } finally {
      window.setTimeout(() => { if (button) button.disabled = false; this._update(); }, 200);
    }
  }

  _renderBackupHeater() {
    const heaterEntityId = this._switchEntity("backup_heater");
    const modeEntityId = this._selectEntity("backup_heater_mode");
    const heater = this._state(heaterEntityId);
    const modeState = this._state(modeEntityId);
    const autoButton = this.shadowRoot.getElementById("backup_heater_mode_auto");
    const offButton = this.shadowRoot.getElementById("backup_heater_mode_off");
    const sub = this.shadowRoot.getElementById("backup_heater_sub");
    const details = this.shadowRoot.getElementById("backup_heater_details");
    const automation = this._state(this._sensorEntity("heater_automation"));

    if (!heater) {
      autoButton.disabled = true;
      offButton.disabled = true;
      autoButton.classList.remove("active");
      offButton.classList.remove("active");
      sub.textContent = "Not configured — select a switch in Aquarium Manager → Configure";
      details.innerHTML = "";
      return;
    }

    const heaterUnavailable = ["unknown", "unavailable"].includes(heater.state);
    const heaterOn = heater.state === "on";
    const inferredMode = automation?.attributes?.enabled === true ? "Auto" : "Off";
    const mode = modeState && !["unknown", "unavailable"].includes(modeState.state) ? modeState.state : inferredMode;
    const modeAvailable = !!modeState && !["unknown", "unavailable"].includes(modeState.state);

    autoButton.disabled = !modeAvailable;
    offButton.disabled = !modeAvailable;
    autoButton.classList.toggle("active", mode === "Auto");
    offButton.classList.toggle("active", mode === "Off");
    sub.textContent = heaterUnavailable
      ? "Heater plug unavailable"
      : `${mode} mode · heater plug currently ${heaterOn ? "ON" : "OFF"}`;

    if (!automation) {
      details.innerHTML = modeAvailable ? "" : `<span>Restart Home Assistant after upgrading to use the Auto / Off control.</span>`;
      return;
    }
    const a = automation.attributes || {};
    const label = String(automation.state || "unknown").replaceAll("_", " ");
    const temp = Number.isFinite(Number(a.current_temperature_c)) ? `${Number(a.current_temperature_c).toFixed(1)} °C` : "temperature —";
    const thresholds = Number.isFinite(Number(a.on_below_c)) && Number.isFinite(Number(a.off_at_c)) ? `${a.on_below_c}→${a.off_at_c} °C` : "";
    const reason = a.reason ? this._escape(String(a.reason)) : "";
    details.innerHTML = `<span class="heater-pill">${this._escape(label)}</span><span>${this._escape(temp)}</span>${thresholds ? `<span>${this._escape(thresholds)}</span>` : ""}${reason ? `<span title="${reason}">${reason}</span>` : ""}`;
  }

  async _setBackupHeaterMode(option) {
    const entityId = this._selectEntity("backup_heater_mode");
    const state = this._state(entityId);
    if (!state || state.state === option) return;
    const autoButton = this.shadowRoot.getElementById("backup_heater_mode_auto");
    const offButton = this.shadowRoot.getElementById("backup_heater_mode_off");
    autoButton.disabled = true;
    offButton.disabled = true;
    try {
      await this._hass.callService("select", "select_option", { entity_id: entityId, option });
    } finally {
      window.setTimeout(() => this._update(), 150);
    }
  }

  _shortTime(value) {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  _renderLighting() {
    const state = this._state(this._sensorEntity("lighting_automation"));
    const override = this._state(this._switchEntity("lights_off_today"));
    const status = this.shadowRoot.getElementById("lighting_state");
    const reason = this.shadowRoot.getElementById("lighting_reason");
    const devices = this.shadowRoot.getElementById("lighting_devices");
    const schedule = this.shadowRoot.getElementById("lighting_schedule");
    const offButton = this.shadowRoot.getElementById("lights_off_today");
    const resyncButton = this.shadowRoot.getElementById("resync_lighting");
    if (!status || !reason || !devices || !schedule || !offButton || !resyncButton) return;

    if (!state || state.state === "not_configured") {
      status.textContent = "Not configured";
      reason.textContent = "Select both light plugs and the CO₂ plug in Aquarium Manager → Configure";
      devices.innerHTML = "";
      schedule.innerHTML = "";
      offButton.disabled = true;
      resyncButton.disabled = true;
      return;
    }

    const a = state.attributes || {};
    status.textContent = String(state.state || "unknown").replaceAll("_", " ");
    reason.textContent = a.reason || "";
    const lightStates = a.light_states || {};
    const lightEntries = Object.entries(lightStates);
    const pills = lightEntries.map(([entity, value], i) => `<span class="device-dot ${value==="on"?"on":""}">Light ${i+1}: ${this._escape(value)}</span>`);
    pills.push(`<span class="device-dot ${a.co2_state==="on"?"on":""}">CO₂: ${this._escape(a.co2_state || "—")}</span>`);
    devices.innerHTML = pills.join("");

    const rows = [
      ["CO₂ on", a.co2_on],
      ["Full light", a.lights_on],
      ["Half light", a.half_light_start],
      ["CO₂ off", a.co2_off],
      ["Blue light", a.blue_light_start],
      ["Lights off", a.lights_off],
    ];
    schedule.innerHTML = rows.map(([label, value]) => `<div class="schedule-item"><div class="schedule-label">${label}</div><div class="schedule-value">${this._shortTime(value)}</div></div>`).join("");

    const isOffToday = override?.state === "on" || a.lights_off_today === true;
    offButton.disabled = !override;
    offButton.classList.toggle("active", isOffToday);
    offButton.textContent = isOffToday ? "Resume today" : "Lights off today";
    resyncButton.disabled = a.enabled !== true;

    const enabledInput = this.shadowRoot.getElementById("lighting_input_enabled");
    if (enabledInput && this.shadowRoot.activeElement !== enabledInput && !enabledInput.dataset.dirty) enabledInput.checked = a.enabled === true;
    const values = {
      lighting_input_on: String(a.lights_on_time || "12:00").slice(0,5),
      lighting_input_duration: a.light_duration_hours ?? 8,
      lighting_input_half: a.half_light_duration_min ?? 120,
      lighting_input_blue: a.blue_light_duration_min ?? 30,
      lighting_input_co2_lead: a.co2_lead_min ?? 120,
      lighting_input_co2_off: a.co2_off_before_end_min ?? 60,
    };
    for (const [id, value] of Object.entries(values)) {
      const input = this.shadowRoot.getElementById(id);
      if (input && this.shadowRoot.activeElement !== input && !input.dataset.dirty) input.value = value;
    }
    const save = this.shadowRoot.getElementById("lighting_save");
    if (save) save.disabled = false;
    this._renderLightingPreview();
  }

  _clockMinutes(value) {
    const m = String(value || "").match(/^(\d{1,2}):(\d{2})/);
    if (!m) return null;
    const h = Number(m[1]), min = Number(m[2]);
    if (!Number.isFinite(h) || !Number.isFinite(min) || h < 0 || h > 23 || min < 0 || min > 59) return null;
    return h * 60 + min;
  }

  _fmtMinutes(total) {
    total = ((Math.round(total) % 1440) + 1440) % 1440;
    const h = Math.floor(total / 60), m = total % 60;
    return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}`;
  }

  _lightingFormValues() {
    const get = id => this.shadowRoot.getElementById(id)?.value ?? "";
    return {
      lighting_automation_enabled: this.shadowRoot.getElementById("lighting_input_enabled")?.checked === true,
      lights_on_time: get("lighting_input_on"),
      light_duration_hours: Number(get("lighting_input_duration")),
      half_light_duration_min: Number(get("lighting_input_half")),
      blue_light_duration_min: Number(get("lighting_input_blue")),
      co2_lead_min: Number(get("lighting_input_co2_lead")),
      co2_off_before_end_min: Number(get("lighting_input_co2_off")),
    };
  }

  _renderLightingPreview() {
    const el = this.shadowRoot.getElementById("lighting_preview");
    if (!el) return;
    const v = this._lightingFormValues();
    const start = this._clockMinutes(v.lights_on_time);
    const nums = [v.light_duration_hours,v.half_light_duration_min,v.blue_light_duration_min,v.co2_lead_min,v.co2_off_before_end_min];
    if (start === null || nums.some(n => !Number.isFinite(n) || n < 0) || v.light_duration_hours < 4 || v.light_duration_hours > 10) {
      el.textContent = "Enter valid schedule values to see the calculated timeline.";
      return;
    }
    const mainEnd = start + v.light_duration_hours * 60;
    if (v.half_light_duration_min > v.light_duration_hours * 60 || v.co2_off_before_end_min > v.light_duration_hours * 60) {
      el.textContent = "Half-light and CO₂-off offsets cannot be longer than the main photoperiod.";
      return;
    }
    const half = mainEnd - v.half_light_duration_min;
    const blueEnd = mainEnd + v.blue_light_duration_min;
    const co2On = start - v.co2_lead_min;
    const co2Off = mainEnd - v.co2_off_before_end_min;
    el.textContent = `CO₂ ${this._fmtMinutes(co2On)} · Full ${this._fmtMinutes(start)} · Half ${this._fmtMinutes(half)} · CO₂ off ${this._fmtMinutes(co2Off)} · Blue ${this._fmtMinutes(mainEnd)} · Off ${this._fmtMinutes(blueEnd)}`;
  }

  async _saveLightingSchedule() {
    const { entryId } = this._stockData();
    const button = this.shadowRoot.getElementById("lighting_save");
    const message = this.shadowRoot.getElementById("lighting_save_message");
    if (!entryId || !button) return;
    const data = this._lightingFormValues();
    const start = this._clockMinutes(data.lights_on_time);
    const durationMin = data.light_duration_hours * 60;
    if (start === null || !Number.isFinite(durationMin) || durationMin < 240 || durationMin > 600 ||
        [data.half_light_duration_min,data.blue_light_duration_min,data.co2_lead_min,data.co2_off_before_end_min].some(n => !Number.isFinite(n) || n < 0) ||
        data.half_light_duration_min > durationMin || data.co2_off_before_end_min > durationMin) {
      if (message) message.textContent = "Check the schedule values";
      return;
    }
    button.disabled = true;
    const previous = button.textContent;
    button.textContent = "Saving…";
    if (message) message.textContent = "Applying schedule…";
    try {
      await this._callResponseService("update_lighting_schedule", { entry_id: entryId, ...data });
      button.textContent = "Saved ✓";
      if (message) message.textContent = "Schedule saved and applied";
      for (const id of ["lighting_input_enabled","lighting_input_on","lighting_input_duration","lighting_input_half","lighting_input_blue","lighting_input_co2_lead","lighting_input_co2_off"]) {
        const input = this.shadowRoot.getElementById(id); if (input) delete input.dataset.dirty;
      }
      window.setTimeout(() => { if (button) button.textContent = previous; }, 1800);
    } catch (err) {
      button.textContent = "Failed";
      if (message) message.textContent = err?.message || "Could not save schedule";
      window.setTimeout(() => { if (button) button.textContent = previous; }, 2500);
    } finally {
      button.disabled = false;
    }
  }

  async _toggleLightsOffToday() {
    const entityId = this._switchEntity("lights_off_today");
    const state = this._state(entityId);
    if (!state) return;
    await this._hass.callService(
      "switch",
      state.state === "on" ? "turn_off" : "turn_on",
      { entity_id: entityId }
    );
  }

  async _resyncLighting() {
    const { entryId } = this._stockData();
    const button = this.shadowRoot.getElementById("resync_lighting");
    if (!entryId || !button) return;
    const previous = button.textContent;
    button.disabled = true;
    button.textContent = "Resyncing…";
    try {
      await this._hass.callService("aquarium_manager", "resync_lighting", { entry_id: entryId });
      button.textContent = "Resynced ✓";
      window.setTimeout(() => { if (button) button.textContent = previous; }, 1800);
    } catch (err) {
      button.textContent = "Failed";
      window.setTimeout(() => { if (button) button.textContent = previous; }, 2500);
      throw err;
    } finally {
      button.disabled = false;
    }
  }

  _renderLastWaterTest(waterState) {
    const el = this.shadowRoot.getElementById("last_water_test");
    if (!el) return;
    const stamp = waterState?.attributes?.last_submitted;
    if (!stamp) { el.textContent = "No submitted test recorded"; return; }
    const d = new Date(stamp);
    el.textContent = Number.isNaN(d.getTime()) ? `Last submitted: ${stamp}` : `Last submitted: ${d.toLocaleString()}`;
  }

  _waterReadingField(key, label, unit, step, timerType=null, timerLabel="") {
    const state = this._state(this._numberEntity(key));
    const current = state && !["unknown","unavailable"].includes(state.state) ? `Current ${state.state}${unit ? ` ${unit}` : ""}` : "Enter result";
    const timer = timerType ? `<button type="button" class="test-timer-btn" data-test-timer="${timerType}" data-base-label="${timerLabel}">${timerLabel}</button>` : "";
    return `<div class="water-test-field"><label>${label}${unit ? ` · ${unit}` : ""}</label><div class="water-test-input-row ${timerType ? "" : "no-timer"}"><input data-reading-key="${key}" type="number" inputmode="decimal" step="${step}" placeholder="${this._escape(current)}">${timer}</div></div>`;
  }

  _openWaterReadings() {
    this._modalMode = "water-readings";
    this._showModal(`
      <div class="modal-head"><div><div class="modal-title">Update water readings</div><div class="modal-subtitle">One complete aquarium water-test snapshot</div></div><button class="icon-btn" data-close><ha-icon icon="mdi:close"></ha-icon></button></div>
      <div class="modal-body">
        <div class="water-test-note">Enter all six results, then submit them together. The reagent timers run in Home Assistant, so they keep running even if you close this popup or leave the dashboard.</div>
        <div class="water-test-grid">
          ${this._waterReadingField("ammonia","NH₃ / NH₄","mg/L","0.01","ammonia","Start 10 min")}
          ${this._waterReadingField("nitrite","NO₂","mg/L","0.01","nitrite","Start 2 min")}
          ${this._waterReadingField("nitrate","NO₃","mg/L","0.5","nitrate","Start 10 min")}
          ${this._waterReadingField("ph","pH","","0.1")}
          ${this._waterReadingField("gh","GH","dGH","1")}
          ${this._waterReadingField("kh","KH","dKH","1")}
        </div>
        <div id="water_test_message" class="water-test-message" style="margin-top:12px"></div>
        <div class="modal-actions"><button data-close>Cancel</button><button id="water_submit_readings" class="primary">Submit readings</button></div>
      </div>`);
    const modal = this.shadowRoot.getElementById("modal");
    modal.querySelector("#water_submit_readings")?.addEventListener("click", () => this._submitWaterReadings());
    modal.querySelectorAll("[data-test-timer]").forEach(button => button.addEventListener("click", () => this._startWaterTestTimer(button.dataset.testTimer)));
    this._updateWaterTimerButtons();
  }

  async _startWaterTestTimer(testType) {
    const { entryId } = this._stockData();
    if (!entryId) return;
    const modal = this.shadowRoot.getElementById("modal");
    const button = modal?.querySelector(`[data-test-timer="${testType}"]`);
    const message = modal?.querySelector("#water_test_message");
    if (button) button.disabled = true;
    try {
      const response = await this._callResponseService("start_water_test_timer", { entry_id: entryId, test_type: testType });
      const endsAt = Date.parse(response.ends_at || "");
      if (Number.isFinite(endsAt)) this._waterTestTimers[testType] = { endsAt };
      if (message) message.textContent = `${response.label || "Water-test timer"} started. Alert target: ${response.notification_target || "Home Assistant"}.`;
      this._ensureWaterTimerTicker();
      this._updateWaterTimerButtons();
    } catch (err) {
      if (message) message.textContent = err?.message || "Could not start timer";
    } finally {
      if (button) button.disabled = false;
    }
  }

  _ensureWaterTimerTicker() {
    if (this._waterTimerTicker) return;
    this._waterTimerTicker = window.setInterval(() => this._updateWaterTimerButtons(), 1000);
  }

  _updateWaterTimerButtons() {
    const now = Date.now();
    let active = 0;
    const modal = this.shadowRoot.getElementById("modal");
    for (const [testType, timer] of Object.entries(this._waterTestTimers)) {
      const remaining = Math.max(0, Math.ceil((timer.endsAt - now) / 1000));
      const button = modal?.querySelector(`[data-test-timer="${testType}"]`);
      if (remaining <= 0) {
        delete this._waterTestTimers[testType];
        if (button) { button.textContent = button.dataset.baseLabel || "Start timer"; button.classList.remove("running"); }
        continue;
      }
      active += 1;
      const min = Math.floor(remaining / 60);
      const sec = String(remaining % 60).padStart(2,"0");
      if (button) { button.textContent = `${min}:${sec}`; button.classList.add("running"); }
    }
    if (!active && this._waterTimerTicker) {
      window.clearInterval(this._waterTimerTicker);
      this._waterTimerTicker = null;
    }
  }

  async _submitWaterReadings() {
    const { entryId } = this._stockData();
    const modal = this.shadowRoot.getElementById("modal");
    const button = modal?.querySelector("#water_submit_readings");
    const message = modal?.querySelector("#water_test_message");
    if (!entryId || !button) return;
    const values = {};
    let invalid = false;
    modal.querySelectorAll("input[data-reading-key]").forEach(input => {
      const value = Number(input.value);
      if (input.value.trim() === "" || Number.isNaN(value)) invalid = true;
      values[input.dataset.readingKey] = value;
    });
    if (invalid) {
      if (message) message.textContent = "Enter all six readings before submitting";
      return;
    }
    button.disabled = true;
    const previous = button.textContent;
    button.textContent = "Submitting…";
    try {
      await this._hass.callService("aquarium_manager", "submit_water_readings", { entry_id: entryId, ...values });
      button.textContent = "Submitted ✓";
      if (message) message.textContent = "Water readings submitted";
      window.setTimeout(() => this._closeModal(), 650);
    } catch (err) {
      button.textContent = "Failed";
      if (message) message.textContent = err?.message || "Could not submit readings";
      window.setTimeout(() => { if (button) button.textContent = previous; }, 2500);
      throw err;
    } finally {
      button.disabled = false;
    }
  }

  _setTab(tab) {
    const tabs = ["overview","water","stocking","equipment","compat","library"];
    this._activeTab = tabs.includes(tab) ? tab : "overview";
    for (const key of tabs) {
      const selected = this._activeTab === key;
      this.shadowRoot.getElementById(`tab_${key}`)?.classList.toggle("active", selected);
      const button = this.shadowRoot.getElementById(`tab_btn_${key}`);
      button?.classList.toggle("active", selected);
      button?.setAttribute("aria-selected", selected ? "true" : "false");
    }
  }

  async _callResponseService(service, serviceData) {
    const result = await this._hass.callWS({
      type: "call_service",
      domain: "aquarium_manager",
      service,
      service_data: serviceData,
      return_response: true
    });
    return result?.response || {};
  }

  _numOrNull(value) {
    if (value === "" || value === null || value === undefined) return null;
    const n = Number(value); return Number.isFinite(n) ? n : null;
  }

  _speciesInput(label, path, value, type="text", extra="") {
    const safe = value === null || value === undefined ? "" : this._escape(String(value));
    return `<div><label>${this._escape(label)}</label><input data-species-path="${path}" type="${type}" value="${safe}" ${extra}></div>`;
  }

  _renderSpeciesSearchResult() {
    const holder = this.shadowRoot.getElementById("species_search_result");
    if (!holder) return;
    const result = this._speciesSearchResult;
    if (!result?.record) { holder.innerHTML = ""; return; }
    const r = result.record;
    const sf = result.source_status?.seriouslyfish === "found";
    const fb = result.source_status?.fishbase === "found";
    const ready = !!result.github_write_ready;
    const existing = !!result.already_in_library;
    holder.innerHTML = `<div class="species-preview">
      <div class="species-preview-head"><div><strong>${this._escape(r.common_name || "Unnamed species")}</strong><div class="scientific">${this._escape(r.scientific_name || "")}</div></div><div class="source-pills"><span class="source-pill ${sf?"found":""}">SeriouslyFish ${sf?"✓":"—"}</span><span class="source-pill ${fb?"found":""}">FishBase ${fb?"✓":"—"}</span></div></div>
      <div class="species-form">
        ${this._speciesInput("Common name","common_name",r.common_name)}
        ${this._speciesInput("Scientific name","scientific_name",r.scientific_name)}
        ${this._speciesInput("Adult size (cm)","adult_size_cm",r.adult_size_cm,"number",'step="0.1"')}
        ${this._speciesInput("Minimum group","minimum_group",r.minimum_group,"number",'step="1"')}
        ${this._speciesInput("Temperature min °C","temperature.min",r.temperature?.min,"number",'step="0.1"')}
        ${this._speciesInput("Temperature max °C","temperature.max",r.temperature?.max,"number",'step="0.1"')}
        ${this._speciesInput("pH min","ph.min",r.ph?.min,"number",'step="0.1"')}
        ${this._speciesInput("pH max","ph.max",r.ph?.max,"number",'step="0.1"')}
        ${this._speciesInput("GH min dGH","gh.min",r.gh?.min,"number",'step="0.1"')}
        ${this._speciesInput("GH max dGH","gh.max",r.gh?.max,"number",'step="0.1"')}
        ${this._speciesInput("Tank length cm","minimum_tank.length_cm",r.minimum_tank?.length_cm,"number",'step="1"')}
        ${this._speciesInput("Tank width cm","minimum_tank.width_cm",r.minimum_tank?.width_cm,"number",'step="1"')}
        ${this._speciesInput("Social type","social_type",r.social_type)}
        ${this._speciesInput("Temperament","temperament",r.temperament)}
        ${this._speciesInput("Difficulty","difficulty",r.difficulty)}
        ${this._speciesInput("Diet","diet_type",r.diet_type)}
      </div>
      <div class="species-message">${existing ? "This species already exists; saving will update its record." : "New species record."}${ready ? "" : " GitHub write access is not configured yet."}</div>
      <div class="species-actions"><button id="species_add_btn" class="primary" ${ready?"":"disabled"}>${existing?"Update library":"Add to library"}</button></div>
    </div>`;
    holder.querySelector("#species_add_btn")?.addEventListener("click", () => this._addSearchedSpecies());
  }

  async _searchSpecies() {
    const input = this.shadowRoot.getElementById("species_search_query");
    const button = this.shadowRoot.getElementById("species_search_btn");
    const message = this.shadowRoot.getElementById("species_search_message");
    const { entryId } = this._stockData();
    const query = input?.value?.trim();
    if (!entryId || !query || !button) return;
    button.disabled = true; button.textContent = "Searching…";
    if (message) { message.textContent = "Searching SeriouslyFish and FishBase…"; message.className="species-message"; }
    try {
      this._speciesSearchResult = await this._callResponseService("search_species", { entry_id: entryId, query });
      this._renderSpeciesSearchResult();
      if (message) message.textContent = `Resolved ${this._speciesSearchResult.record?.common_name || query}`;
    } catch (err) {
      this._speciesSearchResult = null; this._renderSpeciesSearchResult();
      if (message) { message.textContent = err?.message || "Species search failed"; message.className="species-message error"; }
    } finally { button.disabled=false; button.textContent="Search"; }
  }

  _applySpeciesEdits(record) {
    const copy = JSON.parse(JSON.stringify(record));
    const get = path => this.shadowRoot.querySelector(`[data-species-path="${path}"]`)?.value ?? "";
    copy.common_name = get("common_name").trim();
    copy.scientific_name = get("scientific_name").trim();
    copy.adult_size_cm = this._numOrNull(get("adult_size_cm"));
    copy.minimum_group = this._numOrNull(get("minimum_group"));
    copy.temperature = { min:this._numOrNull(get("temperature.min")), max:this._numOrNull(get("temperature.max")) };
    copy.ph = { min:this._numOrNull(get("ph.min")), max:this._numOrNull(get("ph.max")) };
    copy.gh = { min:this._numOrNull(get("gh.min")), max:this._numOrNull(get("gh.max")) };
    const tankL=this._numOrNull(get("minimum_tank.length_cm")), tankW=this._numOrNull(get("minimum_tank.width_cm"));
    copy.minimum_tank = (tankL!==null || tankW!==null) ? { length_cm:tankL, width_cm:tankW, height_cm:copy.minimum_tank?.height_cm ?? null } : null;
    copy.social_type=get("social_type").trim(); copy.temperament=get("temperament").trim(); copy.difficulty=get("difficulty").trim(); copy.diet_type=get("diet_type").trim();
    for (const key of ["temperature","ph","gh"]) if (copy[key].min===null || copy[key].max===null) copy[key]=null;
    return copy;
  }

  async _addSearchedSpecies() {
    const button=this.shadowRoot.querySelector("#species_add_btn"); const message=this.shadowRoot.getElementById("species_search_message");
    const { entryId }=this._stockData(); if (!button || !entryId || !this._speciesSearchResult?.record) return;
    button.disabled=true; const old=button.textContent; button.textContent="Saving…";
    try {
      const species=this._applySpeciesEdits(this._speciesSearchResult.record);
      const response=await this._callResponseService("add_species_to_library", {entry_id:entryId, species});
      if (message) { message.className="species-message"; message.textContent=`${response.commit_message || "Library updated"}${response.commit_sha ? ` · ${String(response.commit_sha).slice(0,12)}` : ""}`; }
      button.textContent="Saved ✓";
      this._speciesSearchResult.already_in_library=true;
      window.setTimeout(()=>{ if(button){button.textContent="Update library";button.disabled=false;} },1800);
    } catch(err) {
      if(message){message.className="species-message error";message.textContent=err?.message||"Could not update library";} button.textContent=old; button.disabled=false;
    }
  }

  _renderStockingAssessment() {
    const state = this._state(this._sensorEntity("stocking"));
    const grid = this.shadowRoot.getElementById("stocking_assessment_grid");
    const issuesHolder = this.shadowRoot.getElementById("stocking_assessment_issues");
    const note = this.shadowRoot.getElementById("stocking_assessment_note");
    if (!grid || !issuesHolder || !note) return;

    const assessment = state?.attributes?.stocking_assessment;
    if (!state || !assessment) {
      this._setStatus("stocking_assessment_status", "unknown");
      grid.innerHTML = `<div class="empty">Stocking assessment unavailable. Restart Home Assistant after upgrading Aquarium Manager.</div>`;
      issuesHolder.innerHTML = "";
      note.textContent = "";
      return;
    }

    this._setStatus("stocking_assessment_status", assessment.status || "unknown");
    const load = assessment.relative_bioload || {};
    const points = load.points === null || load.points === undefined ? "—" : `${this._fmt(load.points)} pts`;
    const density = load.points_per_litre === null || load.points_per_litre === undefined ? "—" : `${this._fmt(load.points_per_litre)} pts/L`;
    const coverage = load.coverage_percent === null || load.coverage_percent === undefined ? "—" : `${this._fmt(load.coverage_percent)}%`;
    const projected = load.projected_points_after_safe_group_fixes;
    const projectedNote = projected !== null && projected !== undefined && Number(projected) !== Number(load.points)
      ? `After safe group fixes: ${this._fmt(projected)} pts`
      : "No calibrated maximum";

    grid.innerHTML = `
      <div class="assessment-card"><div class="assessment-label">Assessment</div><div class="assessment-value">${this._escape(assessment.label || "—")}</div><div class="assessment-note">Rule-based constraints, not a stocking percentage</div></div>
      <div class="assessment-card"><div class="assessment-label">Relative bioload</div><div class="assessment-value">${points}</div><div class="assessment-note">${density} · ${projectedNote}</div></div>
      <div class="assessment-card"><div class="assessment-label">Main constraint</div><div class="assessment-value">${this._escape(assessment.main_constraint || "—")}</div><div class="assessment-note">Tank, water and group requirements</div></div>
      <div class="assessment-card"><div class="assessment-label">Bioload data coverage</div><div class="assessment-value">${coverage}</div><div class="assessment-note">Adult-size data for ${Number(load.known_individuals || 0)} of ${Number(load.known_individuals || 0) + Number(load.unknown_individuals || 0)} fish</div></div>`;

    const issues = Array.isArray(assessment.issues) ? assessment.issues : [];
    issuesHolder.innerHTML = issues.map(item => `<div class="warning-line ${this._escape(item.severity || "warning")}"><ha-icon icon="${item.severity === "danger" ? "mdi:alert-octagon-outline" : item.severity === "info" ? "mdi:information-outline" : "mdi:alert-outline"}"></ha-icon><span>${this._escape(item.message || "")}</span></div>`).join("");

    const filtration = assessment.filtration_status === "not_assessed" ? "Filtration is not assessed yet. " : "";
    note.textContent = `${assessment.bioload_note || ""} ${filtration}${assessment.filtration_note || ""}`.trim();
  }

  _renderCompatibility() {
    const holder = this.shadowRoot.getElementById("compatible_list");
    const groupHolder = this.shadowRoot.getElementById("group_suggestions");
    const count = this.shadowRoot.getElementById("compat_count");
    if (!holder || !groupHolder || !count) return;

    const { state, compatible, groups, total, conflict } = this._compatData();

    if (!state) {
      count.textContent = "Unavailable";
      holder.innerHTML = `<div class="empty">Compatible-fish sensor not found. Restart Home Assistant after upgrading Aquarium Manager.</div>`;
      groupHolder.innerHTML = `<div class="empty">Group suggestions unavailable.</div>`;
      return;
    }

    count.textContent = `${total} match${total === 1 ? "" : "es"}`;

    if (groups.length) {
      groupHolder.innerHTML = groups.map(item => {
        const extra = item.estimated_added_bioload_points !== null && item.estimated_added_bioload_points !== undefined
          ? ` · about +${this._fmt(item.estimated_added_bioload_points)} relative pts`
          : "";
        const blocked = item.safe_to_add === false;
        const advice = blocked
          ? `${Number(item.quantity)||0} kept · recorded minimum ${Number(item.minimum_group)||0} · do not add yet: ${this._escape(item.blocking_reason || "tank-size requirement not met")}`
          : `${Number(item.quantity)||0} kept · recorded minimum ${Number(item.minimum_group)||0}${extra}`;
        const badge = blocked ? `Needs +${Number(item.add_count)||0}, tank first` : `Add ${Number(item.add_count)||0}`;
        return `<div class="group-card"><div><div class="group-title">${this._escape(item.name)}</div><div class="group-sub">${advice}</div></div><div class="add-count">${badge}</div></div>`;
      }).join("");
    } else {
      groupHolder.innerHTML = `<div class="empty">All profiled fish with a recorded minimum group size currently meet it.</div>`;
    }

    if (conflict) {
      holder.innerHTML = `<div class="warning-line danger"><ha-icon icon="mdi:alert-octagon-outline"></ha-icon><span>The current stocked community has at least one conflicting care range. Resolve that conflict before treating new-fish recommendations as reliable.</span></div>`;
      return;
    }

    if (!compatible.length) {
      holder.innerHTML = `<div class="empty">No unstocked library fish currently pass the known tank-size and water-range checks.</div>`;
      return;
    }

    holder.innerHTML = compatible.map(item => {
      const group = Number(item.suggested_group || 1);
      const load = item.estimated_group_bioload_points !== null && item.estimated_group_bioload_points !== undefined
        ? ` · +${this._fmt(item.estimated_group_bioload_points)} relative pts`
        : "";
      const cautions = (Array.isArray(item.cautions) ? item.cautions.length : 0) + (Array.isArray(item.care_notes) ? item.care_notes.length : 0);
      const extra = cautions ? ` · ${cautions} caution${cautions === 1 ? "" : "s"}` : "";
      return `<div class="compat-row" data-profile="${this._escape(item.profile_id)}"><div class="compat-main"><strong>${this._escape(item.name || "Unnamed")}</strong><div class="scientific">${this._escape(item.scientific_name || "")}</div><div class="compat-sub">Suggested group ${group}${load}${extra}</div></div><div class="compat-side"><span class="match-pill ${this._escape(item.confidence || "limited")}">${this._escape((item.confidence || "limited") + " match")}</span><button class="compat-add" data-add>Add ×${group}</button></div></div>`;
    }).join("");

    holder.querySelectorAll(".compat-row").forEach(row => {
      const profileId = row.dataset.profile;
      row.addEventListener("click", () => this._openCandidate(profileId));
      row.querySelector("[data-add]")?.addEventListener("click", e => {
        e.stopPropagation();
        this._addCompatible(profileId);
      });
    });
  }

  async _addCompatible(profileId) {
    const { compatible } = this._compatData();
    const item = compatible.find(x => x.profile_id === profileId);
    const { entryId } = this._stockData();
    if (!item || !entryId) return;
    const quantity = Math.max(1, Number(item.suggested_group || 1));
    await this._hass.callService("aquarium_manager", "add_stock", {
      entry_id: entryId,
      name: item.name,
      quantity,
      notes: "Added from compatible-fish recommendations"
    });
  }

  _openCandidate(profileId) {
    const { compatible } = this._compatData();
    const item = compatible.find(x => x.profile_id === profileId);
    if (!item) return;
    const matches = Array.isArray(item.matches) ? item.matches : [];
    const assessmentCautions = Array.isArray(item.cautions) ? item.cautions : [];
    const careNotes = Array.isArray(item.care_notes) ? item.care_notes : [];
    const combinedCautions = [...assessmentCautions, ...careNotes];
    const source = item.source_url ? `<div class="modal-section"><div class="modal-section-title">Profile source</div><div class="care-note">${this._escape(item.source_label || "Species profile")} — ${this._escape(item.source_url)}</div></div>` : "";
    const group = Math.max(1, Number(item.suggested_group || 1));
    this._showModal(`
      <div class="modal-head"><div><div class="modal-title">${this._escape(item.name)}</div><div class="modal-subtitle">${this._escape(item.scientific_name || "No scientific name")}</div></div><button class="icon-btn" data-close><ha-icon icon="mdi:close"></ha-icon></button></div>
      <div class="modal-body">
        <div class="detail-grid">
          ${this._detail("Suggested group", group)}
          ${this._detail("Relative bioload if added", item.estimated_group_bioload_points === null || item.estimated_group_bioload_points === undefined ? "Unknown" : `${this._fmt(item.estimated_group_bioload_points)} pts`)}
          ${this._detail("Adult size", this._unit(item.adult_size_cm,"cm"))}
          ${this._detail("Temperature", this._range(item.temperature_min,item.temperature_max,"°C"))}
          ${this._detail("pH", this._range(item.ph_min,item.ph_max,""))}
          ${this._detail("GH", this._range(item.gh_min,item.gh_max,"dGH"))}
          ${this._detail("KH", this._range(item.kh_min,item.kh_max,"dKH"))}
          ${this._detail("Minimum group", item.min_group_size ?? "Not set")}
          ${this._detail("Social", item.social_type || "Not set")}
          ${this._detail("Min tank", this._tankRange(item))}
          ${this._detail("Temperament", item.temperament || "Not set")}
          ${this._detail("Difficulty", item.difficulty || "Not set")}
          ${this._detail("Diet", item.diet_type || "Not set")}
        </div>
        ${matches.length ? `<div class="modal-section"><div class="modal-section-title">Why it matches</div>${matches.map(x => `<div class="care-note">${this._escape(x)}</div>`).join("")}</div>` : ""}
        ${combinedCautions.length ? `<div class="modal-section"><div class="modal-section-title">Cautions / care notes</div>${combinedCautions.map(x => `<div class="warning-line warning"><ha-icon icon="mdi:alert-outline"></ha-icon><span>${this._escape(x)}</span></div>`).join("")}</div>` : ""}
        <div class="bioload-note">Relative bioload is an adult-size comparison index; it has no calibrated safe maximum.</div>
        ${source}
        <div class="modal-actions"><button data-close>Close</button><button class="primary" data-add-candidate>Add ×${group}</button></div>
      </div>`);
    this.shadowRoot.querySelector("[data-add-candidate]")?.addEventListener("click", async () => {
      await this._addCompatible(profileId);
      this._closeModal();
    });
  }

  _setStatus(id, value) {
    const el = this.shadowRoot.getElementById(id); if (!el) return;
    el.textContent = value === "unknown" ? "—" : value;
    el.className = `status ${value}`;
  }

  _renderCommunity() {
    const state = this._state(this._sensorEntity("community"));
    this._setStatus("community_status", state?.state || "unknown");
    const ranges = state?.attributes?.ranges || {};
    const keys = [["temperature","Temperature"],["ph","pH"],["gh","GH"],["kh","KH"]];
    const grid = this.shadowRoot.getElementById("community_grid");
    grid.innerHTML = keys.map(([key,label]) => {
      const r = ranges[key] || {};
      let value = "—";
      if (r.conflict) value = "No overlap";
      else if (r.min !== null && r.min !== undefined && r.max !== null && r.max !== undefined) value = `${this._fmt(r.min)}–${this._fmt(r.max)}${r.unit ? ` ${r.unit}` : ""}`;
      const excluded = Array.isArray(r.excluded) && r.excluded.length ? `${r.excluded.length} excluded` : "All profiled fish";
      return `<div class="range-card ${r.conflict ? "conflict" : ""}"><div class="range-label">${label}</div><div class="range-value">${value}</div><div class="range-note">${excluded}</div></div>`;
    }).join("");

    const warnings = state?.attributes?.warnings || [];
    const holder = this.shadowRoot.getElementById("community_warnings");
    holder.innerHTML = warnings.map(w => `<div class="warning-line ${this._escape(w.severity || "warning")}"><ha-icon icon="${w.severity === "danger" ? "mdi:alert-octagon-outline" : w.severity === "info" ? "mdi:information-outline" : "mdi:alert-outline"}"></ha-icon><span>${this._escape(w.message || "")}</span></div>`).join("");
  }

  _renderLibrary() {
    const state = this._state(this._sensorEntity("library"));
    const info = this.shadowRoot.getElementById("library_info");
    const button = this.shadowRoot.getElementById("refresh_library");
    if (!info || !button) return;

    if (!state) {
      info.textContent = "Fish library status unavailable";
      button.style.display = "none";
      return;
    }

    const attrs = state.attributes || {};
    const count = Number(state.state) || 0;
    const source = attrs.source === "remote" ? "GitHub / remote" : attrs.source === "cache" ? "cached GitHub / remote" : "bundled";
    const updated = attrs.updated ? ` · library ${attrs.updated}` : "";
    let refreshed = "";
    if (attrs.last_refresh) {
      const d = new Date(attrs.last_refresh);
      if (!Number.isNaN(d.getTime())) {
        refreshed = ` · fetched ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
      }
    }
    const fingerprint = attrs.content_sha256 ? ` · ${String(attrs.content_sha256).slice(0, 8)}` : "";
    const ambiguousCount = Number(attrs.ambiguous_name_count || 0);
    const ambiguous = ambiguousCount ? ` · ${ambiguousCount} ambiguous name${ambiguousCount === 1 ? "" : "s"}` : "";
    const error = attrs.last_error ? ` · refresh error` : "";
    info.textContent = `${count} species · ${source}${updated}${refreshed}${fingerprint}${ambiguous}${error}`;
    const details = [
      attrs.last_error,
      attrs.url,
      attrs.remote_etag ? `ETag: ${attrs.remote_etag}` : "",
      attrs.remote_last_modified ? `Last-Modified: ${attrs.remote_last_modified}` : "",
      attrs.content_sha256 ? `SHA-256: ${attrs.content_sha256}` : "",
      ambiguousCount ? `Ambiguous names: ${(attrs.ambiguous_names || []).join(", ")}` : ""
    ].filter(Boolean);
    info.title = details.join("\n") || "Bundled fish library";
    button.style.display = attrs.url ? "block" : "none";
  }

  async _refreshLibrary() {
    const { entryId } = this._stockData();
    const button = this.shadowRoot.getElementById("refresh_library");
    if (!entryId || !button) return;
    button.disabled = true;
    const previous = button.textContent;
    button.textContent = "Refreshing…";
    try {
      await this._hass.callService("aquarium_manager", "refresh_species_library", { entry_id: entryId });
      button.textContent = "Updated ✓";
      window.setTimeout(() => {
        if (button) button.textContent = previous;
      }, 1800);
    } catch (err) {
      button.textContent = "Failed";
      window.setTimeout(() => {
        if (button) button.textContent = previous;
      }, 2500);
      throw err;
    } finally {
      button.disabled = false;
    }
  }

  _renderStocking() {
    const state = this._state(this._sensorEntity("stocking"));
    const list = this.shadowRoot.getElementById("stock_list");
    if (!state) { list.innerHTML = `<div class="empty">Stocking sensor not found. Upgrade the Aquarium Manager integration to v0.4.</div>`; return; }
    const stock = Array.isArray(state.attributes?.stocking) ? state.attributes.stocking : [];
    const total = Number(state.state) || 0;
    const bioload = state.attributes?.relative_bioload_points_total ?? state.attributes?.estimated_bioload_points_total;
    const density = state.attributes?.relative_bioload_points_per_l;
    this._setText("livestock_total", `${total} fish`);
    const loadText = bioload !== null && bioload !== undefined ? ` · ${this._fmt(bioload)} relative pts${density !== null && density !== undefined ? ` · ${this._fmt(density)} pts/L` : ""}` : "";
    this._setText("stock_summary", `${total} fish · ${stock.length} ${stock.length === 1 ? "group" : "groups"}${loadText}`);
    if (!stock.length) { list.innerHTML = `<div class="empty">No livestock recorded yet.</div>`; return; }
    list.innerHTML = stock.map(item => {
      const warnings = (item.warnings || []).filter(w => w.severity !== "info");
      return `<div class="stock-row" data-id="${this._escape(item.id)}"><div class="stock-name"><strong>${this._escape(item.name || "Unnamed")}</strong>${item.scientific_name ? `<div class="scientific">${this._escape(item.scientific_name)}</div>` : ""}</div><div class="stock-meta"><span class="qty-pill">×${Number(item.quantity)||0}</span>${warnings.length ? `<span class="warn-pill">⚠ ${warnings.length}</span>` : ""}</div><button class="edit-btn">Edit</button></div>`;
    }).join("");
    list.querySelectorAll(".stock-row").forEach(row => {
      const id = row.dataset.id;
      row.addEventListener("click", () => this._openDetails(id));
      row.querySelector(".edit-btn").addEventListener("click", e => { e.stopPropagation(); this._openEdit(id); });
    });
  }

  _compatData() {
    const state = this._state(this._sensorEntity("compatibility"));
    const attrs = state?.attributes || {};
    const compatible = Array.isArray(attrs.compatible) ? attrs.compatible : [];
    const groups = Array.isArray(attrs.group_recommendations) ? attrs.group_recommendations : [];
    const rawTotal = attrs.compatible_count ?? state?.state ?? compatible.length;
    const parsedTotal = Number(rawTotal);
    const total = Number.isFinite(parsedTotal) ? parsedTotal : compatible.length;
    return {
      state,
      compatible,
      groups,
      total,
      conflict: attrs.community_conflict === true,
    };
  }

  _stockData() {
    const state = this._state(this._sensorEntity("stocking"));
    return { state, stock: state?.attributes?.stocking || [], library: state?.attributes?.species_library || [], entryId: state?.attributes?.config_entry_id || null };
  }

  _openDetails(id) {
    const { stock } = this._stockData();
    const item = stock.find(x => x.id === id); if (!item) return;
    this._modalItemId = id; this._modalMode = "details";
    const warnings = item.warnings || [];
    const cautions = item.cautions || [];
    const source = item.source_url ? `<div class="modal-section"><div class="modal-section-title">Profile source</div><div class="care-note">${this._escape(item.source_label || "Species profile")} — ${this._escape(item.source_url)}</div></div>` : "";
    this._showModal(`
      <div class="modal-head"><div><div class="modal-title">${this._escape(item.name)}</div><div class="modal-subtitle">${this._escape(item.scientific_name || "No scientific name")}</div></div><button class="icon-btn" data-close><ha-icon icon="mdi:close"></ha-icon></button></div>
      <div class="modal-body">
        <div class="detail-grid">
          ${this._detail("Quantity", item.quantity)}${this._detail("Adult size", this._unit(item.adult_size_cm,"cm"))}
          ${this._detail("Relative bioload", item.bioload_points_group === null || item.bioload_points_group === undefined ? "Unknown" : `${this._fmt(item.bioload_points_group)} pts group`)}
          ${this._detail("Relative load / fish", item.bioload_points_each === null || item.bioload_points_each === undefined ? "Unknown" : `${this._fmt(item.bioload_points_each)} pts`)}
          ${this._detail("Temperature", this._range(item.temperature_min,item.temperature_max,"°C"))}${this._detail("pH", this._range(item.ph_min,item.ph_max,""))}
          ${this._detail("GH", this._range(item.gh_min,item.gh_max,"dGH"))}${this._detail("KH", this._range(item.kh_min,item.kh_max,"dKH"))}
          ${this._detail("Minimum group", item.min_group_size ?? "Not set")}${this._detail("Social", item.social_type || "Not set")}
          ${this._detail("Min tank", this._tankRange(item))}${this._detail("Swimming zone", item.swimming_zone || "Not set")}
          ${this._detail("Temperament", item.temperament || "Not set")}
          ${this._detail("Difficulty", item.difficulty || "Not set")}${this._detail("Diet", item.diet_type || "Not set")}
        </div>
        <div class="bioload-note">Relative bioload is a relative comparison score based on adult size and quantity, not a stocking-limit percentage.</div>
        ${warnings.length ? `<div class="modal-section"><div class="modal-section-title">Warnings</div>${warnings.map(w => `<div class="warning-line ${this._escape(w.severity || "warning")}"><ha-icon icon="mdi:alert-outline"></ha-icon><span>${this._escape(w.message)}</span></div>`).join("")}</div>` : ""}
        ${cautions.length ? `<div class="modal-section"><div class="modal-section-title">Care notes</div>${cautions.map(x => `<div class="care-note">${this._escape(x)}</div>`).join("")}</div>` : ""}
        ${item.notes ? `<div class="modal-section"><div class="modal-section-title">Your notes</div><div class="care-note">${this._escape(item.notes)}</div></div>` : ""}
        ${source}
        <div class="modal-actions"><button data-close>Close</button><button class="primary" data-edit>Edit fish</button></div>
      </div>`);
    this.shadowRoot.querySelector("[data-edit]").addEventListener("click", () => this._openEdit(id));
  }

  _openEdit(id) {
    const { stock, library } = this._stockData();
    const item = id ? stock.find(x => x.id === id) : null;
    this._modalItemId = id; this._modalMode = "edit";
    const v = (key) => item?.[key] ?? "";
    const libraryOptions = library.map(x => `<option value="${this._escape(x.name)}">${this._escape(x.scientific_name || "")}</option>`).join("");
    const advanced = item ? `
      <div class="form-field"><label>Scientific name</label><input data-field="scientific_name" value="${this._escape(v("scientific_name"))}"></div>
      <div class="form-field"><label>Adult size (cm)</label><input data-field="adult_size_cm" type="number" step="0.1" value="${this._escape(v("adult_size_cm"))}"></div>
      ${this._rangeInputs("Temperature °C","temperature_min","temperature_max",v)}
      ${this._rangeInputs("pH","ph_min","ph_max",v)}
      ${this._rangeInputs("GH dGH","gh_min","gh_max",v)}
      ${this._rangeInputs("KH dKH","kh_min","kh_max",v)}
      <div class="form-field"><label>Minimum group size</label><input data-field="min_group_size" type="number" step="1" min="1" value="${this._escape(v("min_group_size"))}"></div>
      <div class="form-field"><label>Social type</label><input data-field="social_type" value="${this._escape(v("social_type"))}"></div>
      <div class="form-field"><label>Minimum tank length (cm)</label><input data-field="min_tank_length_cm" type="number" step="1" value="${this._escape(v("min_tank_length_cm"))}"></div>
      <div class="form-field"><label>Minimum tank width (cm)</label><input data-field="min_tank_width_cm" type="number" step="1" value="${this._escape(v("min_tank_width_cm"))}"></div>
      <div class="form-field"><label>Minimum tank height (cm)</label><input data-field="min_tank_height_cm" type="number" step="1" value="${this._escape(v("min_tank_height_cm"))}"></div>
      <div class="form-field"><label>Swimming zone</label><input data-field="swimming_zone" value="${this._escape(v("swimming_zone"))}"></div>
      <div class="form-field full"><label>Temperament</label><input data-field="temperament" value="${this._escape(v("temperament"))}"></div>` : `<div class="form-field full"><div class="small-help">Known species are automatically filled from the active fish library after you add them. You can then edit individual values if required.</div></div>`;

    this._showModal(`
      <div class="modal-head"><div><div class="modal-title">${item ? "Edit fish" : "Add fish"}</div><div class="modal-subtitle">${item ? this._escape(item.name) : "Add a stocking group"}</div></div><button class="icon-btn" data-close><ha-icon icon="mdi:close"></ha-icon></button></div>
      <div class="modal-body"><datalist id="species_library">${libraryOptions}</datalist><div class="form-grid">
        <div class="form-field"><label>Species / name</label><input data-field="name" list="species_library" value="${this._escape(v("name"))}"></div>
        <div class="form-field"><label>Quantity</label><input data-field="quantity" type="number" min="1" step="1" value="${this._escape(item ? v("quantity") : 1)}"></div>
        ${advanced}
        <div class="form-field full"><label>Notes</label><textarea data-field="notes" rows="3">${this._escape(v("notes"))}</textarea></div>
      </div><div class="modal-actions">${item ? `<button class="danger-btn" data-delete>Delete</button>` : ""}<button data-close>Cancel</button><button class="primary" data-save>${item ? "Save changes" : "Add fish"}</button></div></div>`);
    this.shadowRoot.querySelector("[data-save]").addEventListener("click", () => this._saveStockForm(item));
    if (item) this.shadowRoot.querySelector("[data-delete]").addEventListener("click", () => this._removeStock(item));
  }

  _showModal(html) {
    const modal = this.shadowRoot.getElementById("modal"); modal.innerHTML = html;
    this.shadowRoot.getElementById("modal_backdrop").classList.add("open");
    modal.querySelectorAll("[data-close]").forEach(x => x.addEventListener("click", () => this._closeModal()));
  }

  _closeModal(){this.shadowRoot.getElementById("modal_backdrop").classList.remove("open");this._modalItemId=null;this._modalMode=null;}

  async _saveStockForm(existing) {
    const { entryId } = this._stockData(); if (!entryId) return;
    const modal = this.shadowRoot.getElementById("modal");
    const get = key => modal.querySelector(`[data-field="${key}"]`)?.value ?? "";
    const name = get("name").trim(); const quantity = Number(get("quantity"));
    if (!name || !Number.isInteger(quantity) || quantity < 1) return;
    if (!existing) {
      await this._hass.callService("aquarium_manager","add_stock",{entry_id:entryId,name,quantity,notes:get("notes").trim()});
      this._closeModal(); return;
    }
    const numeric = ["adult_size_cm","temperature_min","temperature_max","ph_min","ph_max","gh_min","gh_max","kh_min","kh_max","min_group_size","min_tank_length_cm","min_tank_width_cm","min_tank_height_cm"];
    const payload = {entry_id:entryId,stock_id:existing.id,name,quantity,scientific_name:get("scientific_name").trim(),social_type:get("social_type").trim(),temperament:get("temperament").trim(),swimming_zone:get("swimming_zone").trim(),notes:get("notes").trim()};
    numeric.forEach(key => { const raw=get(key).trim(); payload[key]=raw===""?null:Number(raw); });
    await this._hass.callService("aquarium_manager","update_stock",payload);
    this._closeModal();
  }

  async _removeStock(item){const {entryId}=this._stockData();if(!entryId)return;if(!window.confirm(`Remove ${item.name} from the aquarium?`))return;await this._hass.callService("aquarium_manager","remove_stock",{entry_id:entryId,stock_id:item.id});this._closeModal();}

  async _saveNumber(event){const input=event.currentTarget;const entity_id=this._numberEntity(input.dataset.key);const value=Number(input.value);if(Number.isNaN(value))return;input.disabled=true;try{await this._hass.callService("number","set_value",{entity_id,value});}finally{input.disabled=false;}}

  _detail(label,value){return `<div class="detail"><div class="detail-label">${this._escape(label)}</div><div class="detail-value">${this._escape(value ?? "Not set")}</div></div>`;}
  _range(min,max,unit){if(min===null||min===undefined||max===null||max===undefined)return "Not set";return `${this._fmt(min)}–${this._fmt(max)}${unit?` ${unit}`:""}`;}
  _unit(value,unit){return value===null||value===undefined?"Not set":`${this._fmt(value)} ${unit}`;}
  _tankRange(item){const vals=[item.min_tank_length_cm,item.min_tank_width_cm,item.min_tank_height_cm];const hasDims=vals.some(x=>x!==null&&x!==undefined);const volume=item.min_tank_volume_l;const dimText=hasDims?vals.map(x=>x===null||x===undefined?"—":this._fmt(x)).join(" × ")+" cm":"";const volumeText=volume!==null&&volume!==undefined?`${this._fmt(volume)} L`:"";return [dimText,volumeText].filter(Boolean).join(" · ")||"Not set";}
  _rangeInputs(label,minKey,maxKey,v){return `<div class="form-field full"><label>${label}</label><div class="pair"><input data-field="${minKey}" type="number" step="0.1" placeholder="Min" value="${this._escape(v(minKey))}"><input data-field="${maxKey}" type="number" step="0.1" placeholder="Max" value="${this._escape(v(maxKey))}"></div></div>`;}
  _fmt(value){const n=Number(value);return Number.isFinite(n)?(Number.isInteger(n)?String(n):String(Math.round(n*10)/10)):String(value);}
  _signedDisplay(entityId){const s=this._state(entityId);if(!s||["unknown","unavailable"].includes(s.state))return"—";const n=Number(s.state),u=s.attributes?.unit_of_measurement||"";return`${n>0?"+":""}${s.state}${u?` ${u}`:""}`;}
  _setText(id,value){const el=this.shadowRoot.getElementById(id);if(el)el.textContent=value;}
  _escape(value){return String(value??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#039;");}
}

if (!customElements.get("aquarium-manager-card")) customElements.define("aquarium-manager-card", AquariumManagerCard);
window.customCards = window.customCards || [];
window.customCards.push({type:"aquarium-manager-card",name:"Aquarium Manager",description:"Mobile-friendly tabbed aquarium overview, water, stocking, equipment, compatibility and species library.",preview:false});
console.info("%c AQUARIUM-MANAGER-CARD %c v0.9.0 ","color:white;background:#0277bd;font-weight:bold;","color:#0277bd;background:white;font-weight:bold;");
