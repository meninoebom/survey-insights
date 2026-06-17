// Survey Insights web instrument: a read-only consumer of the FastAPI API.
//
// This is the chosen primary interface for a non-technical user. Unlike the CLI
// and TUI (which call the service layer in-process), this client speaks to the
// API over HTTP, so it is the consumer that makes the API the spine. It renders
// only server-computed numbers: it re-orders values to canonical order and draws
// charts, but it never averages, re-bins, or derives a statistic in the browser.
//
// Ported from the Claude Design prototype (Survey Insights.dc.html). The render
// methods (controls, charts, the three-state crosstab) are the prototype's,
// unchanged except that data now arrives from fetch() instead of a baked file.
(function () {
  "use strict";

  const h = React.createElement;
  const PRES = window.SURVEY_PRESENTATION;
  const WASH_INTENSITY = "subtle"; // design-review decision: faint Blues, restraint-forward
  const SENT_PALETTE = "PuOr"; // design-review decision: colorblind-safe diverging
  const enc = encodeURIComponent;
  const CAVEAT =
    "Unweighted figures describe this sample of {N} respondents, not a population " +
    "estimate. Real survey estimates are typically weighted. Cells with fewer than " +
    "{MIN} respondents are flagged low reliability and shown for completeness.";

  // ---------- canonical ordering ----------
  // The API alphabetizes (ORDER BY value); ordinal axes must be re-imposed in the
  // client (Rule: "ordinal order is law"). Both sides are apostrophe-normalized so
  // a curly-vs-straight mismatch cannot drop a value to the end.
  function normKey(s) {
    return String(s).replace(/[‘’]/g, "'").trim();
  }
  function rankMap(key) {
    const arr = (PRES.canonical && PRES.canonical[key]) || [];
    const m = Object.create(null);
    for (let i = 0; i < arr.length; i++) m[normKey(arr[i])] = i;
    return m;
  }
  function rankOf(rm, v) {
    const k = normKey(v);
    return k in rm ? rm[k] : 1e9;
  }
  function sortValues(key, values) {
    const rm = rankMap(key);
    return values
      .slice()
      .sort((a, b) => rankOf(rm, a) - rankOf(rm, b) || String(a).localeCompare(String(b)));
  }
  function sortItems(key, items, get) {
    const rm = rankMap(key);
    return items
      .slice()
      .sort(
        (a, b) => rankOf(rm, get(a)) - rankOf(rm, get(b)) || String(get(a)).localeCompare(String(get(b)))
      );
  }

  // ---------- HTTP ----------
  async function getJSON(url) {
    const r = await fetch(url, { headers: { Accept: "application/json" } });
    if (!r.ok) {
      let msg = "Request failed (" + r.status + ")";
      try {
        const j = await r.json();
        if (j && j.error) msg = j.error;
      } catch (e) {
        /* non-JSON error body */
      }
      throw new Error(msg);
    }
    return r.json();
  }
  function freshCache() {
    return {
      meta: { measures: [], dimensions: [], canonical: PRES.canonical, min_reliable_n: 30, n_total: null },
      overallMean: {},
      overallProp: {},
      distribution: { overall: {}, grouped: {} },
      breakdown: { average: {}, proportion: {} },
      crosstab: {},
    };
  }

  class App extends React.Component {
    // ---------- palettes (verbatim from the prototype) ----------
    BLUES = ["#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#08519c", "#08306b"];
    DIST_BINS = ["#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#3a83c0"]; // 5 ordered steps, rating low->high
    SENT = {
      PuOr: { Negative: "#e08214", Neutral: "#c2bcb3", Positive: "#8073ac" },
      RdBu: { Negative: "#4393c3", Neutral: "#c6c2bb", Positive: "#c0564a" },
    };
    INK = "#1B1B1A";
    INK2 = "#6A6862";
    INK3 = "#9A988F";
    HAIR = "#E7E4DD";
    CANVAS = "#FAFAF8";
    DOT = "#2C6E9B";

    // ---------- one type scale, applied everywhere ----------
    // Seven rungs replace the file's ad-hoc point sizes. Data reads first: the stat
    // rung is the heaviest and is always tabular; the eyebrow rung is the quiet
    // uppercase label that lets a value outrank its caption.
    SCALE = {
      display: { fontSize: 24, fontWeight: 600, lineHeight: 1.25 },
      brand: { fontSize: 16, fontWeight: 600 },
      stat: { fontSize: 15, fontWeight: 700, fontVariantNumeric: "tabular-nums lining-nums" },
      body: { fontSize: 13, fontWeight: 500 },
      meta: { fontSize: 12, fontWeight: 500 },
      micro: { fontSize: 11, fontWeight: 500 },
      eyebrow: { fontSize: 10.5, fontWeight: 600, letterSpacing: "0.09em", textTransform: "uppercase" },
    };

    constructor(props) {
      super(props);
      this.state = {
        measure: "q1_rating",
        split: "education_level",
        andby: "gender",
        agg: "average",
        threshold: 4,
        tick: 0,
        loading: false,
        // Which ingest action is mid-flight, so each button can speak for itself and
        // all can disable together: null | "upload" | "sample".
        ingestBusy: null,
        // Is the live dataset the bundled sample (vs. a user upload)? Drives the
        // confirm prompt that guards an active upload from a one-click replace.
        isSample: true,
        confirmSample: false, // showing the inline "replace your upload?" prompt
        dragging: false,
        uploadSummary: null,
        error: null,
        metaLoaded: false,
      };
      this.cache = freshCache();
      this._pending = new Set();
      this._dragDepth = 0; // dragenter/leave fire per child; count to know when we truly left
      this.onDragEnter = this.onDragEnter.bind(this);
      this.onDragOver = this.onDragOver.bind(this);
      this.onDragLeave = this.onDragLeave.bind(this);
      this.onDrop = this.onDrop.bind(this);
      this.onPickFile = this.onPickFile.bind(this);
      this.onReset = this.onReset.bind(this);
      this.onUseSample = this.onUseSample.bind(this);
      this._fileInput = React.createRef();
    }
    componentDidMount() {
      this.loadMeta();
    }

    // The UI reads its vocabulary (which measures exist, which are numeric, the
    // dimensions, and the reliability threshold) from GET /meta, then joins it
    // with local presentation order and labels. It holds no second copy of the
    // allowlist, so adding a measure server-side surfaces here automatically.
    loadMeta() {
      getJSON("/meta")
        .then((meta) => {
          this.buildMeta(meta);
          // The server reports which dataset is live (sample vs a persisted upload),
          // so a page reload shows the truth instead of assuming the sample.
          this.setState(
            (s) => ({ metaLoaded: true, isSample: meta.source !== "upload", tick: s.tick + 1 }),
            () => this.ensure()
          );
        })
        .catch((e) => this.setState({ error: String((e && e.message) || e) }));
    }
    buildMeta(meta) {
      const orderBy = (id, arr) => {
        const i = arr.indexOf(id);
        return i < 0 ? 1e9 : i;
      };
      const measures = meta.measures
        .slice()
        .sort((a, b) => orderBy(a.id, PRES.measureOrder) - orderBy(b.id, PRES.measureOrder) || a.id.localeCompare(b.id))
        .map((m) => ({ id: m.id, label: m.id, kind: m.numeric ? "numeric" : "categorical", desc: PRES.descriptions[m.id] || "" }));
      const dimensions = meta.dimensions
        .slice()
        .sort((a, b) => orderBy(a, PRES.dimensionOrder) - orderBy(b, PRES.dimensionOrder) || a.localeCompare(b))
        .map((id) => ({ id, label: id, kind: PRES.canonical[id] ? "ordinal" : "nominal" }));
      this.cache.meta = {
        measures,
        dimensions,
        canonical: PRES.canonical,
        min_reliable_n: meta.min_reliable_n,
        n_total: this.cache.meta.n_total,
      };
    }
    resetData() {
      this.cache.overallMean = {};
      this.cache.overallProp = {};
      this.cache.distribution = { overall: {}, grouped: {} };
      this.cache.breakdown = { average: {}, proportion: {} };
      this.cache.crosstab = {};
      this.cache.meta.n_total = null;
    }
    minN() {
      return (this.cache.meta && this.cache.meta.min_reliable_n) || 30;
    }

    // ---------- helpers ----------
    D() {
      return this.cache;
    }
    s() {
      return this.state;
    }
    isNumeric(m) {
      // Read the numeric flag from /meta (joined into `kind`), never a hardcoded
      // measure name, so the UI can't disagree with the server allowlist.
      return this.measureMeta(m)?.kind === "numeric";
    }
    measureMeta(id) {
      return this.D().meta.measures.find((x) => x.id === id);
    }
    dimLabel(id) {
      return id;
    }
    fmt1(v) {
      return v == null ? "n/a" : Number(v).toFixed(1);
    }
    pct(v) {
      return v == null ? "n/a" : Math.round(v * 100) + "%";
    }
    sentColors() {
      return this.SENT[SENT_PALETTE] || this.SENT.PuOr;
    }

    _hexRgb(h) {
      h = h.replace("#", "");
      return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
    }
    _rgbCss(a) {
      return "rgb(" + a.map((x) => Math.round(x)).join(",") + ")";
    }
    _mix(a, b, t) {
      return a.map((x, i) => x + (b[i] - x) * t);
    }
    bluesAt(t) {
      t = Math.max(0, Math.min(1, t));
      const n = this.BLUES.length - 1;
      const i = Math.min(n - 1, Math.floor(t * n));
      const f = t * n - i;
      return this._mix(this._hexRgb(this.BLUES[i]), this._hexRgb(this.BLUES[i + 1]), f);
    }
    lum(rgb) {
      const f = (c) => {
        c /= 255;
        return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
      };
      return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
    }
    wash(mean) {
      const intensity = WASH_INTENSITY;
      if (intensity === "none") return { bg: "#FFFFFF", text: this.INK };
      const t = (mean - 1) / 4;
      let rgb = this.bluesAt(t);
      if (intensity === "subtle") {
        // A subtle wash is always pale, so ink text reads at every mean. White on
        // the high-mean tint measures about 2.1:1 and fails AA; never flip to it
        // here. The luminance flip is reserved for a bolder, future intensity, and
        // even then only past the point where white clears 4.5:1.
        rgb = this._mix([255, 255, 255], rgb, 0.42);
        return { bg: this._rgbCss(rgb), text: this.INK };
      }
      const text = this.lum(rgb) < 0.45 ? "#FFFFFF" : this.INK;
      return { bg: this._rgbCss(rgb), text };
    }

    // ---------- control setters (re-query the view after each change) ----------
    setMeasure(m) {
      this.setState({ measure: m }, () => this.ensure());
    }
    setSplit(v) {
      const st = { split: v };
      if (v === "none") st.andby = "none";
      if (v === this.state.andby) st.andby = "none";
      this.setState(st, () => this.ensure());
    }
    setAndby(v) {
      this.setState({ andby: v }, () => this.ensure());
    }
    setAgg(a) {
      this.setState({ agg: a }, () => this.ensure());
    }
    setThreshold(t) {
      this.setState({ threshold: Math.max(1, Math.min(5, t)) }, () => this.ensure());
    }

    viewType() {
      const { measure, split, andby } = this.state;
      if (!this.isNumeric(measure)) return split === "none" ? "dist_overall" : "dist_grouped";
      if (split === "none") return "dist_overall";
      if (andby === "none") return "breakdown";
      return "crosstab";
    }

    // ---------- data layer (fetch + canonical re-sort into the prototype's shape) ----------
    setDistOverall(m, r) {
      const distribution = sortItems(m, r.distribution, (d) => d.response_value);
      this.cache.distribution.overall[m] = { measure: m, n: r.n, distribution };
      if (r.n != null) this.cache.meta.n_total = r.n;
    }
    setDistGrouped(m, dim, r) {
      const groups = sortItems(dim, r.groups, (g) => g.group_value).map((g) => ({
        group_value: g.group_value,
        n: g.n,
        distribution: sortItems(m, g.distribution, (d) => d.response_value),
      }));
      (this.cache.distribution.grouped[m] = this.cache.distribution.grouped[m] || {})[dim] = { groups };
    }
    setBreakdownAvg(m, dim, r) {
      const breakdown = sortItems(dim, r.breakdown, (c) => c.group_value);
      (this.cache.breakdown.average[m] = this.cache.breakdown.average[m] || {})[dim] = { breakdown };
      if (r.overall) {
        this.cache.overallMean[m] = r.overall.value;
        if (r.overall.n != null) this.cache.meta.n_total = r.overall.n;
      }
    }
    setBreakdownProp(m, dim, t, r) {
      const breakdown = sortItems(dim, r.breakdown, (c) => c.group_value);
      const byM = (this.cache.breakdown.proportion[m] = this.cache.breakdown.proportion[m] || {});
      (byM[dim] = byM[dim] || {})[t] = { breakdown };
      if (r.overall) {
        (this.cache.overallProp[m] = this.cache.overallProp[m] || {})[t] = r.overall.value;
        if (r.overall.n != null) this.cache.meta.n_total = r.overall.n;
      }
    }
    setCross(m, row, col, r) {
      const row_values = sortValues(row, r.row_values);
      const col_values = sortValues(col, r.col_values);
      (this.cache.crosstab[m] = this.cache.crosstab[m] || {})[row + "|" + col] = {
        row_values,
        col_values,
        cells: r.cells,
      };
    }

    _has(obj, ...keys) {
      let cur = obj;
      for (const k of keys) {
        if (cur == null || !(k in cur)) return false;
        cur = cur[k];
      }
      return cur != null;
    }
    dataReady() {
      const { measure: m, split, andby, agg, threshold: t } = this.state;
      const C = this.cache;
      switch (this.viewType()) {
        case "dist_overall":
          return this._has(C.distribution.overall, m);
        case "dist_grouped":
          return this._has(C.distribution.grouped, m, split);
        case "breakdown":
          return agg === "proportion"
            ? this._has(C.breakdown.proportion, m, split, t)
            : this._has(C.breakdown.average, m, split);
        case "crosstab":
          return (
            this._has(C.crosstab, m, split + "|" + andby) &&
            this._has(C.breakdown.average, m, split) &&
            this._has(C.breakdown.average, m, andby) &&
            m in C.overallMean
          );
        default:
          return false;
      }
    }
    ensure() {
      const { measure: m, split, andby, agg, threshold: t } = this.state;
      const C = this.cache;
      const jobs = [];
      const want = (key, present, run) => {
        if (present || this._pending.has(key)) return;
        this._pending.add(key);
        jobs.push(
          run().finally(() => this._pending.delete(key))
        );
      };
      switch (this.viewType()) {
        case "dist_overall":
          want("do:" + m, this._has(C.distribution.overall, m), async () =>
            this.setDistOverall(m, await getJSON("/distribution?measure=" + enc(m)))
          );
          break;
        case "dist_grouped":
          want("dg:" + m + ":" + split, this._has(C.distribution.grouped, m, split), async () =>
            this.setDistGrouped(m, split, await getJSON("/distribution?measure=" + enc(m) + "&by=" + enc(split)))
          );
          break;
        case "breakdown":
          if (agg === "proportion") {
            want("bp:" + m + ":" + split + ":" + t, this._has(C.breakdown.proportion, m, split, t), async () =>
              this.setBreakdownProp(
                m,
                split,
                t,
                await getJSON("/breakdown?measure=" + enc(m) + "&by=" + enc(split) + "&agg=proportion&threshold=" + t)
              )
            );
          } else {
            want("ba:" + m + ":" + split, this._has(C.breakdown.average, m, split), async () =>
              this.setBreakdownAvg(m, split, await getJSON("/breakdown?measure=" + enc(m) + "&by=" + enc(split) + "&agg=average"))
            );
          }
          break;
        case "crosstab":
          want("ct:" + m + ":" + split + "|" + andby, this._has(C.crosstab, m, split + "|" + andby), async () =>
            this.setCross(m, split, andby, await getJSON("/crosstab?measure=" + enc(m) + "&row=" + enc(split) + "&col=" + enc(andby)))
          );
          want("ba:" + m + ":" + split, this._has(C.breakdown.average, m, split), async () =>
            this.setBreakdownAvg(m, split, await getJSON("/breakdown?measure=" + enc(m) + "&by=" + enc(split) + "&agg=average"))
          );
          want("ba:" + m + ":" + andby, this._has(C.breakdown.average, m, andby), async () =>
            this.setBreakdownAvg(m, andby, await getJSON("/breakdown?measure=" + enc(m) + "&by=" + enc(andby) + "&agg=average"))
          );
          break;
      }
      if (!jobs.length) return;
      this.setState({ loading: true, error: null });
      Promise.all(jobs)
        .then(() => this.setState((s) => ({ loading: false, tick: s.tick + 1 })))
        .catch((e) =>
          this.setState((s) => ({ loading: false, error: String((e && e.message) || e), tick: s.tick + 1 }))
        );
    }
    busy() {
      // True while an ingest action is mid-flight. One guard so a second action
      // cannot start (and every action button can disable) at once.
      return this.state.ingestBusy != null;
    }

    // ---------- ingest (drag-and-drop, browse, reset) ----------
    // All three rebuild the whole dataset server-side, then clear the client cache
    // and re-fetch the current view (the same reload the refresh button uses). The
    // browser sends file *contents*; the server decides where to store them.
    _reloadView(extra) {
      this.resetData();
      this._pending = new Set();
      this.setState((s) => ({ ...extra, tick: s.tick + 1 }), () => this.ensure());
    }
    async _readError(r) {
      // Surface the API's own message (the error contract) when present.
      try {
        const j = await r.json();
        if (j && (j.error || j.detail)) return j.error || j.detail;
      } catch (e) {
        /* non-JSON error body */
      }
      return "Ingest failed (" + r.status + ")";
    }
    doUpload(file) {
      if (!file || this.busy()) return;
      this.setState({ ingestBusy: "upload", dragging: false, confirmSample: false, error: null, uploadSummary: null });
      this._dragDepth = 0;
      // POST the file as the raw body (no multipart), matching the /ingest contract.
      fetch("/ingest", { method: "POST", body: file })
        .then(async (r) => {
          if (!r.ok) throw new Error(await this._readError(r));
          return r.json();
        })
        // The live dataset is now an upload, so a later "Use sample data" must confirm.
        .then((summary) => this._reloadView({ ingestBusy: null, isSample: false, uploadSummary: summary }))
        .catch((e) => this.setState({ ingestBusy: null, error: String((e && e.message) || e) }));
    }
    onUseSample() {
      if (this.busy()) return;
      // Only an active upload is at risk; if the sample is already live, swap silently.
      if (!this.state.isSample && !this.state.confirmSample) {
        this.setState({ confirmSample: true });
        return;
      }
      this.setState({ ingestBusy: "sample", confirmSample: false, error: null, uploadSummary: null });
      fetch("/ingest/sample", { method: "POST" })
        .then(async (r) => {
          if (!r.ok) throw new Error(await this._readError(r));
          return r.json();
        })
        .then((summary) => this._reloadView({ ingestBusy: null, isSample: true, uploadSummary: summary }))
        .catch((e) => this.setState({ ingestBusy: null, error: String((e && e.message) || e) }));
    }
    // Back-compat alias: the older name some call sites used.
    onReset() {
      this.onUseSample();
    }
    onPickFile(e) {
      const file = e.target.files && e.target.files[0];
      if (file) this.doUpload(file);
      e.target.value = ""; // allow re-selecting the same file
    }
    onDragEnter(e) {
      if (!this._hasFiles(e)) return;
      e.preventDefault();
      this._dragDepth += 1;
      if (!this.state.dragging) this.setState({ dragging: true });
    }
    onDragOver(e) {
      if (!this._hasFiles(e)) return;
      e.preventDefault(); // required so the drop event fires
    }
    onDragLeave(e) {
      if (!this._hasFiles(e)) return;
      this._dragDepth = Math.max(0, this._dragDepth - 1);
      if (this._dragDepth === 0 && this.state.dragging) this.setState({ dragging: false });
    }
    onDrop(e) {
      e.preventDefault();
      this._dragDepth = 0;
      const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (file) this.doUpload(file);
      else this.setState({ dragging: false });
    }
    _hasFiles(e) {
      const dt = e.dataTransfer;
      return !!dt && Array.prototype.indexOf.call(dt.types || [], "Files") !== -1;
    }

    // ---------- rail controls ----------
    // A passive read-out of where the current selections land: Distribution, then
    // Breakdown, then Cross-tab. The active stage is inked and bold; it is not a
    // control (no click), so it never competes with the real selectors below.
    viewIndicator() {
      const vt = this.viewType();
      const active = vt === "crosstab" ? "Cross-tab" : vt === "breakdown" ? "Breakdown" : "Distribution";
      const steps = ["Distribution", "Breakdown", "Cross-tab"];
      const kids = [];
      steps.forEach((label, i) => {
        const on = label === active;
        if (i > 0) kids.push(h("span", { key: "s" + i, "aria-hidden": "true", style: { color: this.INK3, fontSize: 11 } }, "→"));
        kids.push(
          h("span", { key: label, style: { ...this.SCALE.micro, fontWeight: on ? 700 : 500, color: on ? this.INK : this.INK3 } }, label)
        );
      });
      return h(
        "div",
        {
          "aria-label": "Current view: " + active,
          style: { display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 20, paddingBottom: 16, borderBottom: "1px solid #EFEDE7" },
        },
        kids
      );
    }
    optRow(label, desc, selected, onClick, disabled) {
      return h(
        "button",
        {
          key: label,
          onClick: disabled ? null : onClick,
          disabled: !!disabled,
          style: {
            display: "flex",
            alignItems: "flex-start",
            gap: 10,
            width: "100%",
            textAlign: "left",
            background: selected ? "#EEF4FB" : "transparent",
            border: "1px solid " + (selected ? "#CADEF1" : "transparent"),
            borderRadius: 8,
            padding: "8px 10px",
            marginBottom: 2,
            cursor: disabled ? "default" : "pointer",
            opacity: disabled ? 0.4 : 1,
            fontFamily: "inherit",
            transition: "background .12s,border-color .12s",
          },
        },
        h("span", {
          style: {
            flex: "0 0 auto",
            width: 13,
            height: 13,
            borderRadius: "50%",
            marginTop: 3,
            border: "1.5px solid " + (selected ? "#2171b5" : "#C4C0B7"),
            background: "#fff",
            position: "relative",
            boxShadow: selected ? "inset 0 0 0 2.5px #2171b5" : "none",
          },
        }),
        h(
          "span",
          { style: { display: "flex", flexDirection: "column", gap: 1, minWidth: 0 } },
          h(
            "span",
            {
              style: {
                fontSize: 13,
                fontWeight: selected ? 600 : 500,
                color: this.INK,
                letterSpacing: "-0.005em",
              },
            },
            label
          ),
          desc ? h("span", { style: { ...this.SCALE.micro, color: this.INK2, lineHeight: 1.35 } }, desc) : null
        )
      );
    }
    measureControl() {
      const cur = this.state.measure;
      return h(
        "div",
        {},
        this.D().meta.measures.map((m) => this.optRow(m.label, m.desc, cur === m.id, () => this.setMeasure(m.id)))
      );
    }
    splitControl() {
      const cur = this.state.split;
      const rows = [this.optRow("none", "overall distribution", cur === "none", () => this.setSplit("none"))];
      this.D().meta.dimensions.forEach((d) => rows.push(this.optRow(d.label, d.kind, cur === d.id, () => this.setSplit(d.id))));
      return h("div", {}, rows);
    }
    andbyControl() {
      const cur = this.state.andby;
      const split = this.state.split;
      const numeric = this.isNumeric(this.state.measure);
      if (!numeric) {
        return h(
          "div",
          {
            style: {
              fontSize: 12,
              color: this.INK2,
              lineHeight: 1.5,
              background: "#FBF6F1",
              border: "1px solid #EFE3D7",
              borderRadius: 8,
              padding: "10px 12px",
            },
          },
          "The cross-tab computes a mean, which does not apply to sentiment. Choose a numeric measure to compare two dimensions."
        );
      }
      if (split === "none") {
        return h(
          "div",
          { style: { fontSize: 12, color: this.INK3, lineHeight: 1.5, padding: "2px 10px" } },
          "Select a Split-by dimension first."
        );
      }
      const rows = [this.optRow("none", "no second dimension", cur === "none", () => this.setAndby("none"))];
      this.D().meta.dimensions.forEach((d) => {
        const dis = d.id === split;
        rows.push(this.optRow(d.label, dis ? "(already the split)" : d.kind, cur === d.id, () => this.setAndby(d.id), dis));
      });
      return h("div", {}, rows);
    }
    segmented(options, value, onPick) {
      return h(
        "div",
        { style: { display: "flex", background: "#F1EFE9", border: "1px solid #E4E0D8", borderRadius: 8, padding: 3, gap: 3 } },
        options.map((o) => {
          const sel = o.value === value;
          return h(
            "button",
            {
              key: o.value,
              onClick: o.disabled ? null : () => onPick(o.value),
              disabled: !!o.disabled,
              title: o.title || null,
              style: {
                flex: 1,
                padding: "7px 8px",
                fontSize: 12.5,
                fontWeight: sel ? 600 : 500,
                fontFamily: "inherit",
                color: o.disabled ? "#B7B3AA" : sel ? "#0E3D6E" : this.INK2,
                background: sel ? "#FFFFFF" : "transparent",
                border: "1px solid " + (sel ? "#D5DEE8" : "transparent"),
                borderRadius: 6,
                cursor: o.disabled ? "not-allowed" : "pointer",
                boxShadow: sel ? "0 1px 2px rgba(20,40,70,.07)" : "none",
              },
            },
            o.label
          );
        })
      );
    }
    // Canvas-level aggregation control. Aggregation changes only a breakdown, so
    // the control lives beside the breakdown chart, not in the rail. It renders
    // exactly when it acts: it is absent in the distribution views (where, in the
    // rail, it used to sit and do nothing) and in the cross-tab (where the mean is
    // forced). In a cross-tab a quiet note stands in for it and says how to get
    // Proportion back. Sentiment is never numeric, so it never reaches a breakdown
    // and never shows this control; its caption already notes it is categorical.
    aggToolbar() {
      const vt = this.viewType();
      if (vt === "crosstab") {
        return h(
          "div",
          { style: { ...this.SCALE.micro, color: this.INK3, lineHeight: 1.45, marginTop: 16, maxWidth: 760 } },
          "Showing the mean. Proportion returns when you remove the second dimension."
        );
      }
      if (vt !== "breakdown") return null;
      const label = h("span", { style: { ...this.SCALE.eyebrow, letterSpacing: "0.08em", color: this.INK3, flex: "0 0 auto" } }, "Aggregation");
      const aggOpts = [
        { value: "average", label: "Average" },
        { value: "proportion", label: "Proportion" },
      ];
      const toggle = h("div", { style: { flex: "0 0 auto", minWidth: 208 } }, this.segmented(aggOpts, this.state.agg, (v) => this.setAgg(v)));
      const tail =
        this.state.agg === "proportion"
          ? this.thresholdControl()
          : h("span", { style: { ...this.SCALE.micro, color: this.INK3, lineHeight: 1.4 } }, "Mean rating on the 1 to 5 scale.");
      return h(
        "div",
        {
          style: {
            marginTop: 16,
            maxWidth: 760,
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            gap: 14,
            background: "#FFFFFF",
            border: "1px solid " + this.HAIR,
            borderRadius: 10,
            padding: "12px 14px",
          },
        },
        label,
        toggle,
        tail
      );
    }
    // The threshold stepper, laid out inline so it sits in the aggregation toolbar
    // beside the Average/Proportion toggle. Only rendered for the proportion view.
    thresholdControl() {
      const t = this.state.threshold;
      const btn = (label, fn, dis) =>
        h(
          "button",
          {
            onClick: dis ? null : fn,
            disabled: dis,
            style: {
              width: 30,
              height: 30,
              borderRadius: 7,
              border: "1px solid #D9D5CC",
              background: dis ? "#F4F2EC" : "#fff",
              color: dis ? "#C3BFB6" : this.INK,
              fontSize: 17,
              lineHeight: 1,
              cursor: dis ? "default" : "pointer",
              fontFamily: "inherit",
            },
          },
          label
        );
      const atFloor = t <= 2;
      return h(
        "div",
        { style: { display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8 } },
        h("span", { style: { ...this.SCALE.micro, color: this.INK2 } }, "Threshold"),
        btn("−", () => this.setThreshold(t - 1), atFloor),
        h(
          "span",
          { className: "tnum", style: { width: 28, textAlign: "center", fontSize: 16, fontWeight: 600, fontFamily: "IBM Plex Mono, monospace" } },
          t
        ),
        btn("+", () => this.setThreshold(t + 1), t >= 5),
        h("span", { style: { ...this.SCALE.micro, color: this.INK3, lineHeight: 1.4 } }, "Share rating ≥ " + t + " out of 5"),
        // The floor is 2 by design (a "≥ 1" share is always 100%). Say so when the
        // minus is disabled, so a stuck control reads as deliberate, not broken.
        atFloor
          ? h("span", { style: { ...this.SCALE.micro, color: this.INK3, lineHeight: 1.4 } }, "· 1 would include every rating.")
          : null
      );
    }

    // ---------- shared chart atoms ----------
    caption(text) {
      return h("div", { style: { fontSize: 12, color: this.INK2, lineHeight: 1.55, marginTop: 16, maxWidth: 760 } }, text);
    }
    legendRow(items) {
      return h(
        "div",
        { style: { display: "flex", flexWrap: "wrap", gap: "10px 20px", alignItems: "center", marginTop: 18 } },
        items.map((it, i) =>
          h("div", { key: i, style: { display: "flex", alignItems: "center", gap: 8 } }, it.swatch, h("span", { style: { fontSize: 12, color: this.INK2 } }, it.label))
        )
      );
    }
    barChart(dist, colorFor, axisLabel, ariaLabel) {
      const W = 760,
        H = 360,
        mL = 46,
        mR = 20,
        mT = 34,
        mB = 58;
      const counts = dist.map((d) => d.count);
      const maxC = Math.max(1, ...counts);
      const ticks = this._niceTicks(maxC, 4);
      const yMax = ticks[ticks.length - 1];
      const pW = W - mL - mR,
        pH = H - mT - mB;
      const n = dist.length;
      const band = pW / n;
      const bw = Math.min(82, band * 0.62);
      const y = (v) => mT + pH - (v / yMax) * pH;
      const els = [];
      ticks.forEach((tk) => {
        els.push(h("line", { key: "g" + tk, x1: mL, x2: W - mR, y1: y(tk), y2: y(tk), stroke: tk === 0 ? "#C9C5BC" : "#EEEBE4", strokeWidth: 1 }));
        els.push(h("text", { key: "gt" + tk, x: mL - 9, y: y(tk) + 3.5, textAnchor: "end", fontSize: 11, fill: this.INK3, className: "tnum" }, tk));
      });
      // A rotated count-axis title, so the y numbers read as respondents, not bare ticks.
      els.push(h("text", { key: "yt", x: 13, y: mT + pH / 2, fontSize: 11, fill: this.INK3, textAnchor: "middle", transform: "rotate(-90 13 " + (mT + pH / 2) + ")" }, "respondents"));
      dist.forEach((d, i) => {
        const cx = mL + band * i + band / 2;
        const bx = cx - bw / 2;
        const by = y(d.count);
        const bh = mT + pH - by;
        els.push(h("rect", { key: "b" + i, x: bx, y: by, width: bw, height: Math.max(0, bh), fill: colorFor(d.response_value, i), stroke: "#FFFFFF", strokeWidth: 0.75, rx: 1.5 }));
        els.push(h("text", { key: "c" + i, x: cx, y: by - 7, textAnchor: "middle", fontSize: 12.5, fontWeight: 600, fill: this.INK, className: "tnum" }, d.count));
        els.push(h("text", { key: "x" + i, x: cx, y: H - mB + 18, textAnchor: "middle", fontSize: 12, fill: this.INK2 }, d.response_value));
      });
      els.push(h("text", { key: "ax", x: mL, y: H - 10, fontSize: 11.5, fill: this.INK3 }, axisLabel));
      return h("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", style: { display: "block", maxWidth: 760, fontFamily: "Libre Franklin, sans-serif" }, role: "img", "aria-label": ariaLabel || axisLabel }, els);
    }
    _niceTicks(max, count) {
      const raw = max / count;
      const pow = Math.pow(10, Math.floor(Math.log10(raw)));
      const cands = [1, 2, 2.5, 5, 10].map((x) => x * pow);
      let step = cands.find((c) => c >= raw) || cands[cands.length - 1];
      const top = Math.ceil(max / step) * step;
      const out = [];
      for (let v = 0; v <= top + 1e-9; v += step) out.push(Math.round(v * 100) / 100);
      return out;
    }

    render_dist_overall() {
      const m = this.state.measure;
      const dist = this.D().distribution.overall[m];
      // The spoken label restates the drawn bars (rating: count), assembled from the
      // same dist values the chart renders. Nothing here is recomputed.
      const barsText = dist.distribution.map((d) => d.response_value + ": " + d.count).join(", ");
      if (this.isNumeric(m)) {
        const aria =
          "Distribution of " + m + " across " + dist.n + " respondents on a 1 to 5 rating scale. Respondent counts. " + barsText + ".";
        const chart = this.barChart(dist.distribution, (v, i) => this.DIST_BINS[i] || this.DIST_BINS[4], "Rating (1 = lowest to 5 = highest)", aria);
        const legend = this.legendRow(
          this.DIST_BINS.map((c, i) => ({
            swatch: h("span", { style: { width: 15, height: 13, background: c, borderRadius: 2, border: "1px solid rgba(0,0,0,.08)", display: "inline-block" } }),
            label: String(i + 1),
          }))
        );
        return h(
          "div",
          {},
          chart,
          h("div", { style: { fontSize: 11.5, color: this.INK3, marginTop: 4 } }, "Rating scale, low to high"),
          legend,
          this.caption(
            "Counts of each rating across the 1 to 5 scale (n = " +
              dist.n +
              "). Bars rise from a true zero; height is the respondent count. Color reinforces scale position only."
          )
        );
      }
      const sc = this.sentColors();
      const aria = "Distribution of sentiment across " + dist.n + " respondents. Respondent counts. " + barsText + ".";
      const chart = this.barChart(dist.distribution, (v) => sc[v] || "#ccc", "Sentiment", aria);
      const legend = this.legendRow(
        ["Negative", "Neutral", "Positive"].map((k) => ({
          swatch: h("span", { style: { width: 15, height: 13, background: sc[k], borderRadius: 2, border: "1px solid rgba(0,0,0,.08)", display: "inline-block" } }),
          label: k,
        }))
      );
      return h(
        "div",
        {},
        chart,
        legend,
        this.caption(
          "Distribution of sentiment across " +
            dist.n +
            " respondents, on a diverging, colorblind-safe palette. Sentiment is categorical; no average is computed."
        )
      );
    }

    render_dist_grouped() {
      const m = this.state.measure;
      const split = this.state.split;
      const g = this.D().distribution.grouped[m][split];
      const isNum = this.isNumeric(m);
      const sc = this.sentColors();
      let maxC = 1;
      g.groups.forEach((gr) => gr.distribution.forEach((d) => {
        if (d.count > maxC) maxC = d.count;
      }));
      const panels = g.groups.map((gr, gi) => {
        const W = 224,
          H = 150,
          mL = 8,
          mR = 8,
          mT = 30,
          mB = 24;
        const pW = W - mL - mR,
          pH = H - mT - mB;
        const nb = gr.distribution.length;
        const band = pW / nb;
        const bw = Math.min(34, band * 0.6);
        const y = (v) => mT + pH - (v / maxC) * pH;
        const els = [];
        // One faint mid gridline at half the SHARED maxC (never a per-panel max, so
        // bar heights stay comparable across panels), to anchor the eye mid-axis.
        const mid = maxC / 2;
        els.push(h("line", { key: "mid", x1: mL, x2: W - mR, y1: y(mid), y2: y(mid), stroke: "#F0EDE6", strokeWidth: 1 }));
        els.push(h("line", { key: "base", x1: mL, x2: W - mR, y1: mT + pH, y2: mT + pH, stroke: "#D8D4CB", strokeWidth: 1 }));
        // Full sentiment words (no three-letter slice that confused Negative/Neutral);
        // shrink the tick font for the longer labels so they still fit the panel.
        const tickFont = isNum ? 10 : 8.5;
        if (gr.n === 0) {
          els.push(h("text", { key: "e", x: W / 2, y: mT + pH / 2, textAnchor: "middle", fontSize: 11.5, fill: this.INK3 }, "n = 0 · no respondents"));
        } else {
          gr.distribution.forEach((d, i) => {
            const cx = mL + band * i + band / 2;
            const by = y(d.count);
            const bh = mT + pH - by;
            const col = isNum ? this.DIST_BINS[i] || this.DIST_BINS[4] : sc[d.response_value] || "#ccc";
            els.push(h("rect", { key: "b" + i, x: cx - bw / 2, y: by, width: bw, height: Math.max(0, bh), fill: col, rx: 1.5, stroke: "#fff", strokeWidth: 0.5 }));
            if (d.count > 0) els.push(h("text", { key: "c" + i, x: cx, y: by - 4, textAnchor: "middle", fontSize: 10.5, fontWeight: 600, fill: this.INK2, className: "tnum" }, d.count));
            els.push(h("text", { key: "x" + i, x: cx, y: H - 9, textAnchor: "middle", fontSize: tickFont, fill: this.INK3 }, d.response_value));
          });
        }
        // The spoken label restates this panel's drawn bars; values come from the
        // same distribution the bars use, never recomputed.
        const panelAria =
          gr.group_value + ", " + gr.n + " respondents. " +
          (gr.n === 0 ? "No respondents." : gr.distribution.map((d) => d.response_value + ": " + d.count).join(", ") + ".");
        return h(
          "div",
          { key: gi, style: { border: "1px solid " + this.HAIR, borderRadius: 9, background: "#fff", padding: "10px 10px 4px" } },
          h(
            "div",
            { style: { display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 2 } },
            h("span", { style: { fontSize: 12.5, fontWeight: 600, color: this.INK } }, gr.group_value),
            h("span", { className: "tnum", style: { fontSize: 11, color: this.INK3 } }, "n = " + gr.n)
          ),
          h("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", style: { display: "block", fontFamily: "Libre Franklin, sans-serif" }, role: "img", "aria-label": panelAria }, els)
        );
      });
      const legend = isNum
        ? this.legendRow(
            this.DIST_BINS.map((c, i) => ({
              swatch: h("span", { style: { width: 15, height: 13, background: c, borderRadius: 2, border: "1px solid rgba(0,0,0,.08)", display: "inline-block" } }),
              label: String(i + 1),
            }))
          )
        : this.legendRow(
            ["Negative", "Neutral", "Positive"].map((k) => ({
              swatch: h("span", { style: { width: 15, height: 13, background: sc[k], borderRadius: 2, border: "1px solid rgba(0,0,0,.08)", display: "inline-block" } }),
              label: k,
            }))
          );
      return h(
        "div",
        {},
        h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))", gap: 14 } }, panels),
        legend,
        this.caption(
          "One panel per " +
            split +
            " group, all sharing the same count axis so the distribution shape is comparable across groups. Each panel prints its own n. Panels with no respondents are shown as empty frames, never as a zero-height distribution."
        )
      );
    }

    render_breakdown() {
      const m = this.state.measure;
      const split = this.state.split;
      const agg = this.state.agg;
      if (agg === "proportion") return this.render_breakdown_prop();
      const bd = this.D().breakdown.average[m][split];
      const overallMean = this.D().overallMean[m];
      const rows = [{ group_value: "Overall", value: overallMean, n: this.D().meta.n_total, _anchor: true }, ...bd.breakdown];
      const W = 780,
        mL = 164,
        mR = 12,
        plotR = 596,
        top = 46,
        rowH = 44,
        bottom = 14;
      const H = top + rows.length * rowH + bottom;
      const x = (v) => mL + ((v - 1) / 4) * (plotR - mL);
      // Dot area is anchored to a FIXED reference, not the moving n_total, so a
      // 30-respondent dot is the same diameter in every breakdown and dots stay
      // comparable across views. The floor keeps a present small group visible.
      const nRef = this.minN() * 4;
      const rFor = (n) => (n <= 0 ? 0 : Math.max(2.4, 15 * Math.sqrt(n / nRef)));
      const els = [];
      // Rating ticks at top and a repeat of the axis label/ticks at the bottom, so a
      // reader near either edge of a tall plot can read the scale without scrolling.
      for (let t = 1; t <= 5; t++) {
        els.push(h("line", { key: "g" + t, x1: x(t), x2: x(t), y1: top - 8, y2: H - bottom, stroke: "#EEEBE4", strokeWidth: 1 }));
        els.push(h("text", { key: "gt" + t, x: x(t), y: top - 14, textAnchor: "middle", fontSize: 11, fill: this.INK3, className: "tnum" }, t));
        els.push(h("text", { key: "gb" + t, x: x(t), y: H - 2, textAnchor: "middle", fontSize: 11, fill: this.INK3, className: "tnum" }, t));
      }
      els.push(h("text", { key: "axl", x: mL, y: 18, fontSize: 11.5, fill: this.INK3 }, "Mean rating (1 to 5 scale)"));
      els.push(h("line", { key: "ref", x1: x(overallMean), x2: x(overallMean), y1: top - 4, y2: H - bottom, stroke: "#B8B4AB", strokeWidth: 1, strokeDasharray: "3 3" }));
      els.push(h("text", { key: "reft", x: x(overallMean), y: top - 28, textAnchor: "middle", fontSize: 10, fill: this.INK3 }, "overall " + this.fmt1(overallMean)));
      rows.forEach((r, i) => {
        const cy = top + rowH * i + rowH / 2;
        const anchor = r._anchor;
        if (anchor) els.push(h("line", { key: "sep", x1: 0, x2: W, y1: top + rowH - 1, y2: top + rowH - 1, stroke: this.HAIR, strokeWidth: 1 }));
        els.push(h("text", { key: "l" + i, x: mL - 14, y: cy + 4, textAnchor: "end", fontSize: 13, fontWeight: anchor ? 700 : 500, fill: this.INK }, r.group_value));
        if (r.n === 0) {
          els.push(h("text", { key: "z" + i, x: mL + 10, y: cy + 4, fontSize: 11.5, fill: this.INK3 }, "n = 0 · no respondents"));
        } else {
          const rad = rFor(r.n);
          const low = r.n < this.minN() && !anchor;
          // A low-n mean is provisional, so its mark is knocked back to a hollow ring
          // (white fill, dot-colored stroke). The x-position is the exact mean, and
          // the printed value and n are unchanged: only the mark's weight changes.
          els.push(
            h("circle", {
              key: "d" + i,
              cx: x(r.value),
              cy: cy,
              r: rad,
              fill: anchor ? "#1B1B1A" : low ? "#FFFFFF" : this.DOT,
              fillOpacity: anchor ? 0.9 : low ? 1 : 0.82,
              stroke: anchor ? "#fff" : low ? this.DOT : "#fff",
              strokeWidth: low ? 1.5 : 1,
            })
          );
          // The value reads before the group name: heavier (14/700) and tabular.
          els.push(h("text", { key: "v" + i, x: plotR + 22, y: cy + 4, textAnchor: "start", fontSize: 14, fontWeight: 700, fill: this.INK, className: "tnum" }, this.fmt1(r.value)));
          els.push(h("text", { key: "n" + i, x: W - 8, y: cy + 4, textAnchor: "end", fontSize: 11.5, fill: low ? "#A8745C" : this.INK3, className: "tnum" }, "n=" + r.n + (low ? " (low)" : "")));
        }
      });
      // The spoken label restates the drawn rows: measure, split, the overall mean,
      // then every group with its drawn value and n. Reuses fmt1, never recomputes.
      const aria =
        "Mean of " + m + " by " + split + ". Overall mean " + this.fmt1(overallMean) + " across " + this.D().meta.n_total + " respondents. " +
        bd.breakdown.map((r) => r.group_value + ": " + (r.n === 0 ? "no respondents" : this.fmt1(r.value) + ", n " + r.n)).join(". ") + ".";
      const chart = h("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", style: { display: "block", fontFamily: "Libre Franklin, sans-serif" }, role: "img", "aria-label": aria }, els);
      return h(
        "div",
        {},
        chart,
        this.dotSizeLegend(rFor),
        this.caption(
          "Each dot is a group mean on the fixed 1 to 5 scale (never autoscaled). Dot area encodes the group’s respondent count against a fixed reference, so dot size is comparable across breakdowns: a heavy dot is a large sample, a pinprick is a small one, and n is printed beside it. Low-reliability groups (n < " +
            this.minN() +
            ") are drawn as a hollow ring. The dashed line marks the overall mean (" +
            this.fmt1(overallMean) +
            ", server-provided). Groups with no respondents list their n with no dot."
        )
      );
    }
    // A small key that fixes the meaning of dot area: two reference dots sized by the
    // same rFor the plot uses, so a reader can read a count off a diameter.
    dotSizeLegend(rFor) {
      const refs = [30, 100];
      const items = refs.map((nRef) => {
        const rad = rFor(nRef);
        const d = Math.ceil(rad * 2) + 4;
        return h(
          "div",
          { key: nRef, style: { display: "flex", alignItems: "center", gap: 7 } },
          h(
            "svg",
            { width: d, height: d, style: { display: "block" }, "aria-hidden": "true" },
            h("circle", { cx: d / 2, cy: d / 2, r: rad, fill: this.DOT, fillOpacity: 0.82 })
          ),
          h("span", { className: "tnum", style: { ...this.SCALE.micro, color: this.INK2 } }, "n = " + nRef)
        );
      });
      return h(
        "div",
        { style: { display: "flex", alignItems: "center", gap: 18, marginTop: 14 } },
        h("span", { style: { ...this.SCALE.micro, color: this.INK3 } }, "Dot area = respondents:"),
        ...items
      );
    }
    render_breakdown_prop() {
      const m = this.state.measure;
      const split = this.state.split;
      const t = this.state.threshold;
      const bd = this.D().breakdown.proportion[m][split][t];
      const overall = this.D().overallProp[m][t];
      const rows = [{ group_value: "Overall", value: overall, n: this.D().meta.n_total, _anchor: true }, ...bd.breakdown];
      const W = 780,
        mL = 164,
        mR = 92,
        plotR = W - mR,
        top = 46,
        rowH = 42,
        bottom = 14;
      const barH = 18;
      const H = top + rows.length * rowH + bottom;
      const x = (v) => mL + v * (plotR - mL);
      const els = [];
      // Percent ticks at top and repeated at the bottom for tall plots.
      [0, 0.25, 0.5, 0.75, 1].forEach((tk) => {
        els.push(h("line", { key: "g" + tk, x1: x(tk), x2: x(tk), y1: top - 8, y2: H - bottom, stroke: tk === 0 ? "#C9C5BC" : "#EEEBE4", strokeWidth: 1 }));
        els.push(h("text", { key: "gt" + tk, x: x(tk), y: top - 14, textAnchor: "middle", fontSize: 11, fill: this.INK3, className: "tnum" }, Math.round(tk * 100) + "%"));
        els.push(h("text", { key: "gb" + tk, x: x(tk), y: H - 2, textAnchor: "middle", fontSize: 11, fill: this.INK3, className: "tnum" }, Math.round(tk * 100) + "%"));
      });
      els.push(h("text", { key: "axl", x: mL, y: 18, fontSize: 11.5, fill: this.INK3 }, "Share rating " + t + " or higher (%)"));
      els.push(h("line", { key: "ref", x1: x(overall), x2: x(overall), y1: top - 4, y2: H - bottom, stroke: "#B8B4AB", strokeWidth: 1, strokeDasharray: "3 3" }));
      rows.forEach((r, i) => {
        const cy = top + rowH * i + rowH / 2;
        const anchor = r._anchor;
        if (anchor) els.push(h("line", { key: "sep", x1: 0, x2: W, y1: top + rowH - 1, y2: top + rowH - 1, stroke: this.HAIR, strokeWidth: 1 }));
        els.push(h("text", { key: "l" + i, x: mL - 14, y: cy + 4, textAnchor: "end", fontSize: 13, fontWeight: anchor ? 700 : 500, fill: this.INK }, r.group_value));
        if (r.n === 0) {
          els.push(h("text", { key: "z" + i, x: mL + 10, y: cy + 4, fontSize: 11.5, fill: this.INK3 }, "n = 0 · no respondents"));
        } else {
          const low = r.n < this.minN() && !anchor;
          els.push(h("rect", { key: "b" + i, x: mL, y: cy - barH / 2, width: Math.max(1, x(r.value) - mL), height: barH, fill: anchor ? "#1B1B1A" : this.DOT, fillOpacity: anchor ? 0.88 : low ? 0.5 : 0.82, rx: 2 }));
          els.push(h("text", { key: "v" + i, x: Math.max(mL + 6, x(r.value) + 8), y: cy + 4, fontSize: 12.5, fontWeight: 600, fill: this.INK, className: "tnum" }, this.pct(r.value)));
          els.push(h("text", { key: "n" + i, x: W - 6, y: cy + 4, textAnchor: "end", fontSize: 11.5, fill: low ? "#A8745C" : this.INK3, className: "tnum" }, "n=" + r.n + (low ? " (low)" : "")));
        }
      });
      // Spoken label restates the drawn bars; values reuse pct(), never recomputed.
      const aria =
        "Share rating " + t + " or higher in " + m + " by " + split + ". Overall " + this.pct(overall) + " across " + this.D().meta.n_total + " respondents. " +
        bd.breakdown.map((r) => r.group_value + ": " + (r.n === 0 ? "no respondents" : this.pct(r.value) + ", n " + r.n)).join(". ") + ".";
      const chart = h("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", style: { display: "block", fontFamily: "Libre Franklin, sans-serif" }, role: "img", "aria-label": aria }, els);
      return h(
        "div",
        {},
        chart,
        this.caption(
          "Share of each group rating " +
            t +
            " or higher. The proportion has a true zero and a fixed 0 to 100% axis, so a bar from zero is honest here. Low-reliability groups (n < " +
            this.minN() +
            ") are knocked back and flagged; n is printed for every group."
        )
      );
    }

    render_crosstab() {
      const m = this.state.measure;
      const row = this.state.split;
      const col = this.state.andby;
      const ct = this.D().crosstab[m][row + "|" + col];
      const rowMarg = this.D().breakdown.average[m][row].breakdown.reduce((a, c) => {
        a[c.group_value] = c;
        return a;
      }, {});
      const colMarg = this.D().breakdown.average[m][col].breakdown.reduce((a, c) => {
        a[c.group_value] = c;
        return a;
      }, {});
      const grandMean = this.D().overallMean[m];
      const grandN = this.D().meta.n_total;
      const cellMap = {};
      ct.cells.forEach((c) => (cellMap[c.row_value + "|" + c.col_value] = c));
      // A deliberate warning texture, not background: at alpha 0.24 the hatch reads
      // as "handle with care" even when every body cell is low reliability.
      const hatch = "repeating-linear-gradient(45deg, rgba(120,116,108,0.24) 0, rgba(120,116,108,0.24) 1px, transparent 1px, transparent 5px)";
      const thBase = { fontSize: 12, fontWeight: 600, color: this.INK2, padding: "9px 12px", borderBottom: "1px solid " + this.HAIR, textAlign: "center", whiteSpace: "nowrap" };
      const margBg = "#F6F4EE";
      // When no two-way cell reaches the reliability bar (none populated, at least
      // one low_n), an on-grid note says so, so an all-hatched grid reads as a
      // sample-size message, not a glitch. Empty cells do not block the note: they
      // are absent data, not a reached bar. Cell status is "populated"|"low_n"|"empty".
      const noneReachBar =
        !ct.cells.some((c) => c.status === "populated") && ct.cells.some((c) => c.status === "low_n");
      const nCols = ct.col_values.length;

      const headCells = [
        h(
          "th",
          { key: "corner", scope: "col", style: { ...thBase, textAlign: "left", minWidth: 128, position: "sticky", left: 0, background: "#fff" } },
          h("span", { style: { fontSize: 11, color: this.INK3, fontWeight: 600 } }, row + " ↓ / " + col + " →")
        ),
      ];
      ct.col_values.forEach((cv) => headCells.push(h("th", { key: cv, scope: "col", style: thBase }, cv)));
      headCells.push(h("th", { key: "marg", scope: "col", style: { ...thBase, background: margBg, color: this.INK, fontStyle: "normal" } }, "by " + row));

      const bodyRows = ct.row_values.map((rv) => {
        const tds = [
          h(
            "th",
            { key: "rh", scope: "row", style: { fontSize: 12.5, fontWeight: 600, color: this.INK, padding: "0 12px", textAlign: "left", borderRight: "1px solid " + this.HAIR, position: "sticky", left: 0, background: "#fff", whiteSpace: "nowrap" } },
            rv
          ),
        ];
        ct.col_values.forEach((cv) => {
          const c = cellMap[rv + "|" + cv];
          tds.push(this.crosstabCell(c, hatch));
        });
        const rm = rowMarg[rv];
        tds.push(this.margCell(rm, margBg));
        return h("tr", { key: rv }, tds);
      });
      const margRow = [
        h(
          "th",
          { key: "mh", scope: "row", style: { fontSize: 12, fontWeight: 600, color: this.INK, padding: "0 12px", textAlign: "left", background: margBg, position: "sticky", left: 0, whiteSpace: "nowrap" } },
          "by " + col
        ),
      ];
      ct.col_values.forEach((cv) => {
        margRow.push(this.margCell(colMarg[cv], margBg));
      });
      // The grand-total corner is the one cell that always clears the reliability
      // bar, so it carries the wash; it is right-aligned to match the ledger columns.
      const gw = this.wash(grandMean);
      margRow.push(
        h(
          "td",
          { key: "grand", style: { textAlign: "right", padding: "8px 12px", background: gw.bg, borderTop: "2px solid #D9D5CC" } },
          h(
            "div",
            { style: { display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 1 } },
            h("span", { className: "tnum", style: { fontSize: 15, fontWeight: 700, color: gw.text } }, this.fmt1(grandMean)),
            h("span", { className: "tnum", style: { fontSize: 10.5, color: gw.text, opacity: 0.75 } }, "n=" + grandN)
          )
        )
      );

      // The on-grid all-low_n note: one banner row across the full body width. It
      // states the sample-size fact; the printed cell values and n stay untouched.
      const banner = noneReachBar
        ? h(
            "tr",
            { key: "__alllow" },
            h(
              "td",
              { colSpan: nCols + 2, style: { padding: "8px 12px", background: "#FBF8F3", borderBottom: "1px solid " + this.HAIR, ...this.SCALE.micro, color: "#5E5C56", lineHeight: 1.45, textAlign: "left" } },
              "No two-way cell reaches the " + this.minN() + "-respondent reliability bar on this sample. Read the printed value and n, not the absence of color."
            )
          )
        : null;

      const tableAria =
        "Mean of " + m + " by " + row + " and " + col + " cross-tab. Grand total mean " + this.fmt1(grandMean) + " across " + grandN + " respondents." + (noneReachBar ? " No two-way cell reaches the reliability bar." : "");
      const table = h(
        "table",
        { "aria-label": tableAria, style: { borderCollapse: "separate", borderSpacing: 0, width: "100%", maxWidth: 840, fontFamily: "Libre Franklin, sans-serif" } },
        h("thead", {}, h("tr", {}, headCells)),
        h("tbody", {}, [banner, ...bodyRows, h("tr", { key: "__marg", style: { borderTop: "2px solid #D9D5CC" } }, margRow)])
      );

      const sw = (bg, extra) => h("span", { style: { width: 24, height: 18, borderRadius: 3, border: "1px solid " + this.HAIR, background: bg, backgroundImage: extra || "none", display: "inline-block" } });
      const washSwatch = this.wash(4.2);
      const legend = h(
        "div",
        { style: { display: "flex", flexWrap: "wrap", alignItems: "center", gap: "12px 26px", marginTop: 20 } },
        this._legendItem(sw(washSwatch.bg), "Populated", "value + n shown; mean tinted (reliable cell)"),
        this._legendItem(sw("#FBFAF7", hatch), "Low reliability", "n < " + this.minN() + "; value + n shown, hatched, off the color scale"),
        this._legendItem(sw(this.emptyCellBg()), "No respondents", "blank, marked “n/a”; never drawn as zero"),
        this.bluesScaleKey()
      );

      return h(
        "div",
        {},
        h(
          "div",
          { style: { display: "flex", alignItems: "flex-start", gap: 10, marginBottom: 16, background: "#FBF8F3", border: "1px solid #ECE4D6", borderRadius: 9, padding: "11px 14px", maxWidth: 840 } },
          h("span", { style: { flex: "0 0 auto", marginTop: 1, color: "#A8745C", fontSize: 14 } }, "◈"),
          h(
            "span",
            { style: { fontSize: 12.5, color: "#5E5C56", lineHeight: 1.55 } },
            "Small samples produce many low-reliability cells; this is expected. On a 50-respondent sample, no two-way cell reaches the " +
              this.minN() +
              "-respondent reliability bar, so the color wash appears only on the grand-total corner. The printed value and n are the truth and survive black-and-white."
          )
        ),
        h("div", { style: { overflowX: "auto", border: "1px solid " + this.HAIR, borderRadius: 11, background: "#fff", padding: "4px 4px 0" } }, table),
        legend
      );
    }
    _legendItem(swatch, title, desc) {
      return h(
        "div",
        { style: { display: "flex", alignItems: "center", gap: 9 } },
        swatch,
        h(
          "div",
          { style: { display: "flex", flexDirection: "column" } },
          h("span", { style: { fontSize: 12, fontWeight: 600, color: this.INK } }, title),
          h("span", { style: { fontSize: 11, color: this.INK3 } }, desc)
        )
      );
    }
    // A faint flat neutral fill for an empty cell: present, but lighter than any
    // populated tint, and distinct from both the canvas and the hatch texture.
    emptyCellBg() {
      return "#F2F0EB";
    }
    // A compact sequential key for the Blues wash, sampled from the very same wash()
    // mapping the cells use (t = (mean - 1) / 4), annotated 1 to 5. It is the legend
    // for the tint, so a reader can read a mean off a cell's color.
    bluesScaleKey() {
      const W = 132,
        Hb = 12,
        steps = 28;
      const segs = [];
      for (let i = 0; i < steps; i++) {
        const mean = 1 + (i / (steps - 1)) * 4; // 1..5 across the strip
        segs.push(h("rect", { key: i, x: (i / steps) * W, y: 0, width: W / steps + 0.6, height: Hb, fill: this.wash(mean).bg }));
      }
      return h(
        "div",
        { style: { display: "flex", flexDirection: "column", gap: 3 } },
        h("span", { style: { ...this.SCALE.micro, color: this.INK3 } }, "Mean rating (cell tint)"),
        h(
          "div",
          { style: { display: "flex", alignItems: "center", gap: 6 } },
          h("span", { className: "tnum", style: { ...this.SCALE.micro, color: this.INK2 } }, "1"),
          h(
            "svg",
            { width: W, height: Hb, style: { display: "block", borderRadius: 2, border: "1px solid " + this.HAIR }, "aria-hidden": "true" },
            segs
          ),
          h("span", { className: "tnum", style: { ...this.SCALE.micro, color: this.INK2 } }, "5")
        )
      );
    }
    crosstabCell(c, hatch) {
      // Each cell is a right-aligned ledger entry: value over n, flush right with a
      // consistent right pad, so scanning a column reads cleanly down the figures.
      const stack = { display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 1 };
      if (!c || c.status === "empty") {
        // Empty is present but muted: a darker "n/a" glyph on a faint flat neutral
        // fill, distinct from both canvas and hatch. Never a value-scaled tint, never 0.
        return h(
          "td",
          { key: c ? c.col_value : Math.random(), style: { textAlign: "right", padding: "10px 12px", background: this.emptyCellBg(), borderBottom: "1px solid " + this.HAIR } },
          h("div", { style: stack }, h("span", { "aria-label": "no data", title: "No respondents in this cell.", style: { fontSize: 12, color: "#6A6862" } }, "n/a")),
          h("span", { style: { position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" } }, "no data")
        );
      }
      if (c.status === "low_n") {
        // A low_n number must not rank like a headline: weight 500, not 700.
        return h(
          "td",
          {
            key: c.col_value,
            title: "Small sample (n = " + c.n + "). Estimate is imprecise; interpret with caution.",
            style: { textAlign: "right", padding: "9px 12px", background: "#FBFAF7", backgroundImage: hatch, borderBottom: "1px solid " + this.HAIR },
          },
          h(
            "div",
            { style: stack },
            h("span", { className: "tnum", style: { fontSize: 15, fontWeight: 500, color: "#7A776F" } }, this.fmt1(c.value)),
            h("span", { className: "tnum", style: { fontSize: 10.5, color: "#A8745C", fontWeight: 500 } }, "n=" + c.n + " (low)")
          )
        );
      }
      const w = this.wash(c.value);
      return h(
        "td",
        { key: c.col_value, title: "Mean " + this.fmt1(c.value) + ", n = " + c.n + ".", style: { textAlign: "right", padding: "9px 12px", background: w.bg, borderBottom: "1px solid " + this.HAIR } },
        h(
          "div",
          { style: stack },
          h("span", { className: "tnum", style: { fontSize: 15, fontWeight: 700, color: w.text } }, this.fmt1(c.value)),
          h("span", { className: "tnum", style: { fontSize: 10.5, color: w.text, opacity: 0.7 } }, "n=" + c.n)
        )
      );
    }
    margCell(c, bg) {
      if (!c) return h("td", { style: { background: bg } });
      const low = c.n < this.minN();
      return h(
        "td",
        { style: { textAlign: "right", padding: "8px 12px", background: bg, borderBottom: "1px solid " + this.HAIR } },
        h(
          "div",
          { style: { display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 1 } },
          h("span", { className: "tnum", style: { fontSize: 13.5, fontWeight: 600, color: this.INK } }, this.fmt1(c.value)),
          h("span", { className: "tnum", style: { fontSize: 10, color: low ? "#A8745C" : this.INK3 } }, "n=" + c.n + (low ? "·low" : ""))
        )
      );
    }

    skeleton() {
      const msg = this.state.error
        ? "Could not load this view (measure " + this.state.measure + ", split " + this.state.split + "). " + this.state.error
        : "Loading…";
      return h(
        "div",
        {
          style: {
            height: 320,
            border: "1px solid " + this.HAIR,
            borderRadius: 11,
            background: "#fff",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          },
        },
        h("span", { style: { fontSize: 13, color: this.state.error ? "#A8745C" : this.INK3 } }, msg)
      );
    }
    buildCanvas() {
      if (!this.dataReady()) return this.skeleton();
      try {
        switch (this.viewType()) {
          case "dist_overall":
            return this.render_dist_overall();
          case "dist_grouped":
            return this.render_dist_grouped();
          case "breakdown":
            return this.render_breakdown();
          case "crosstab":
            return this.render_crosstab();
        }
      } catch (e) {
        return h(
          "div",
          { style: { fontSize: 13, color: "#A8745C", padding: "18px", border: "1px solid #ECE4D6", borderRadius: 9, background: "#FBF8F3" } },
          "Could not load this view (measure " + this.state.measure + ", split " + this.state.split + "). " + ((e && e.message) || "")
        );
      }
    }

    titleBlock() {
      const m = this.state.measure;
      const { split, andby, agg, threshold } = this.state;
      const vt = this.viewType();
      const N = this.D().meta.n_total;
      const prov = "(unweighted sample, n = " + (N == null ? "…" : N) + ")";
      const mm = this.measureMeta(m);
      const desc = mm ? mm.desc : "";
      if (vt === "dist_overall") return { title: "Distribution of " + m, subtitle: "“" + desc + "”  " + prov, endpoint: "GET /distribution?measure=" + m };
      if (vt === "dist_grouped") return { title: "Distribution of " + m + " by " + split, subtitle: "“" + desc + "”  " + prov, endpoint: "GET /distribution?measure=" + m + "&by=" + split };
      if (vt === "breakdown") {
        if (agg === "proportion")
          return {
            title: "Share rating " + threshold + " or higher in " + m + ", by " + split,
            subtitle: prov,
            endpoint: "GET /breakdown?measure=" + m + "&by=" + split + "&agg=proportion&threshold=" + threshold,
          };
        return { title: "Mean of " + m + " by " + split, subtitle: prov, endpoint: "GET /breakdown?measure=" + m + "&by=" + split + "&agg=average" };
      }
      return { title: "Mean of " + m + " by " + split + " and " + andby, subtitle: prov, endpoint: "GET /crosstab?measure=" + m + "&row=" + split + "&col=" + andby };
    }

    _headerButton(opts) {
      const { label, onClick, disabled, title, primary, ariaLabel } = opts;
      return h(
        "button",
        {
          onClick: disabled ? null : onClick,
          disabled: !!disabled,
          title: title || null,
          "aria-label": ariaLabel || null,
          style: {
            fontFamily: "inherit",
            fontSize: this.SCALE.meta.fontSize,
            fontWeight: primary ? 600 : 500,
            color: disabled ? "#9A988F" : primary ? "#FFFFFF" : "#0E3D6E",
            background: disabled ? "#EFEDE7" : primary ? "#2171b5" : "#F1F4F8",
            border: "1px solid " + (primary ? "#1B5E96" : "#DCE6F1"),
            padding: "5px 11px",
            borderRadius: 6,
            cursor: disabled ? "default" : "pointer",
            whiteSpace: "nowrap",
          },
        },
        label
      );
    }
    // A quiet uppercase label that titles each action group.
    _groupLabel(text) {
      return h("span", { style: { ...this.SCALE.eyebrow, color: this.INK3, marginRight: 2 } }, text);
    }
    // A read-only pill naming the dataset currently loaded, so "Use sample data" is
    // not a mystery when the sample is already live. A dot marks it apart from the
    // action buttons; it is never clickable.
    _sourceIndicator() {
      const onSample = this.state.isSample;
      return h(
        "span",
        {
          title: "The dataset currently loaded. Upload a CSV (or drag one onto the window) to replace it.",
          style: {
            ...this.SCALE.micro,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            color: this.INK2,
            background: "#FFFFFF",
            border: "1px solid " + this.HAIR,
            borderRadius: 5,
            padding: "3px 9px",
            whiteSpace: "nowrap",
          },
        },
        h("span", { "aria-hidden": "true", style: { width: 7, height: 7, borderRadius: "50%", background: onSample ? this.INK3 : this.DOT } }),
        "Showing " + (onSample ? "sample data" : "your upload")
      );
    }
    uploadControls() {
      const b = this.state.ingestBusy;
      const anyBusy = this.busy();
      // The hidden input is the click-to-browse fallback for the drop zone; it must
      // stay mounted even when the header row wraps so the click target survives.
      const fileInput = h("input", {
        ref: this._fileInput,
        type: "file",
        accept: ".csv,text/csv",
        onChange: this.onPickFile,
        style: { display: "none" },
      });
      const uploadBtn = this._headerButton({
        label: b === "upload" ? "Ingesting…" : "Upload CSV",
        onClick: () => this._fileInput.current && this._fileInput.current.click(),
        disabled: anyBusy,
        primary: true,
        ariaLabel: "Upload a CSV file to replace the dataset",
        title: "Upload a CSV to replace the dataset (POST /ingest). You can also drag a file onto the window.",
      });
      const sampleBtn = this._headerButton({
        label: b === "sample" ? "Resetting…" : "Use sample data",
        onClick: this.onUseSample,
        // Disabled while the sample is already live: there is no upload to discard.
        disabled: anyBusy || this.state.isSample,
        title: this.state.isSample
          ? "The bundled sample is already the live dataset."
          : "Discard your upload and fall back to the bundled 50-row sample (POST /ingest/sample).",
      });
      const dragHint = h(
        "span",
        { style: { ...this.SCALE.micro, color: this.INK3, whiteSpace: "nowrap" } },
        "or drag a CSV onto the window"
      );
      // The confirm prompt only appears when a live upload is about to be replaced.
      const confirm = this.state.confirmSample
        ? h(
            "div",
            {
              role: "alertdialog",
              style: { display: "flex", alignItems: "center", gap: 8, background: "#FBF8F3", border: "1px solid #ECE4D6", borderRadius: 7, padding: "4px 8px" },
            },
            h("span", { style: { ...this.SCALE.micro, color: "#5E5C56" } }, "Discard your upload and load the bundled sample?"),
            this._headerButton({ label: "Replace", onClick: this.onUseSample, disabled: anyBusy, primary: true }),
            this._headerButton({ label: "Cancel", onClick: () => this.setState({ confirmSample: false }), disabled: anyBusy })
          )
        : null;
      return h(
        "div",
        { style: { display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" } },
        fileInput,
        // Data source group: which dataset is loaded, and how to change it.
        h(
          "div",
          { style: { display: "flex", alignItems: "center", gap: 8 } },
          this._groupLabel("Data source"),
          this._sourceIndicator(),
          uploadBtn,
          sampleBtn,
          dragHint
        ),
        confirm
      );
    }
    dropOverlay() {
      if (!this.state.dragging) return null;
      return h(
        "div",
        {
          className: "drop-overlay",
          "aria-hidden": "true",
          style: {
            position: "fixed",
            inset: 0,
            zIndex: 50,
            background: "rgba(20,40,70,0.10)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            pointerEvents: "none", // let the drop reach the container beneath
          },
        },
        h(
          "div",
          {
            style: {
              background: "#FFFFFF",
              border: "2px dashed #2171b5",
              borderRadius: 14,
              padding: "30px 44px",
              boxShadow: "0 10px 40px rgba(20,40,70,0.18)",
              textAlign: "center",
            },
          },
          h("div", { style: { fontSize: 17, fontWeight: 600, color: "#0E3D6E" } }, "Drop a CSV to ingest"),
          h("div", { style: { fontSize: 12.5, color: "#6A6862", marginTop: 6 } }, "It replaces the dataset; the newest upload wins.")
        )
      );
    }
    summaryBanner() {
      const s = this.state.uploadSummary;
      if (!s) return null;
      const reasons = s.drop_reasons && Object.keys(s.drop_reasons).length
        ? " (" + Object.entries(s.drop_reasons).map(([k, v]) => v + " " + k).join("; ") + ")"
        : "";
      const dropped = s.rows_dropped
        ? s.rows_dropped + " row" + (s.rows_dropped === 1 ? "" : "s") + " dropped" + reasons
        : "no rows dropped";
      return h(
        "div",
        {
          style: {
            display: "flex",
            alignItems: "center",
            gap: 10,
            fontSize: this.SCALE.meta.fontSize,
            color: "#2C5E3A",
            background: "#F0F6F0",
            border: "1px solid #CFE3CF",
            borderRadius: 9,
            padding: "10px 13px",
            marginBottom: 18,
          },
        },
        h("span", { style: { fontWeight: 600 } }, "Ingest complete."),
        h("span", {}, s.rows_ingested + " rows ingested, " + dropped + "."),
        h(
          "button",
          {
            onClick: () => this.setState({ uploadSummary: null }),
            "aria-label": "Dismiss ingest summary",
            style: { marginLeft: "auto", border: "none", background: "transparent", color: "#6A8A6A", cursor: "pointer", fontSize: 16, lineHeight: 1 },
            title: "Dismiss",
          },
          "×"
        )
      );
    }
    // One announcement region, always mounted, filled conditionally. Screen readers
    // hear ingest results and errors because the live container is in the DOM before
    // the text arrives. Success is polite; an error preempts as an alert.
    feedbackRegion() {
      const busyVerb = { upload: "Ingesting CSV", sample: "Loading sample data" }[this.state.ingestBusy];
      return h(
        "div",
        {},
        h(
          "div",
          { role: "status", "aria-live": "polite", "aria-atomic": "true" },
          busyVerb ? h("span", { className: "visually-hidden" }, busyVerb + "…") : null,
          this.summaryBanner()
        ),
        this.state.error
          ? h(
              "div",
              {
                role: "alert",
                style: { fontSize: this.SCALE.meta.fontSize, color: "#A8745C", background: "#FBF8F3", border: "1px solid #ECE4D6", borderRadius: 9, padding: "10px 13px", marginBottom: 18 },
              },
              this.state.error
            )
          : null
      );
    }

    render() {
      if (!this.state.metaLoaded) {
        return h(
          "div",
          {
            style: {
              display: "flex",
              height: "100vh",
              alignItems: "center",
              justifyContent: "center",
              background: "#FAFAF8",
              color: this.state.error ? "#A8745C" : this.INK3,
              fontFamily: "Libre Franklin, sans-serif",
              fontSize: 13.5,
              padding: "0 24px",
              textAlign: "center",
            },
          },
          this.state.error ? "Could not reach the API: " + this.state.error : "Loading…"
        );
      }
      const tb = this.titleBlock();
      const N = this.D().meta.n_total;
      const nShown = N == null ? "…" : N;
      const caveat = CAVEAT.replace("{N}", nShown).replace("{MIN}", this.minN());
      // One eyebrow token for every rail section head and the aggregation head; one
      // micro token for every helper note. Single-sourced so the rail ink is uniform.
      const sectionLabel = (text, mb) =>
        h("div", { style: { ...this.SCALE.eyebrow, letterSpacing: "0.08em", color: this.INK3, marginBottom: mb == null ? 11 : mb } }, text);
      const helper = (text) => h("div", { style: { ...this.SCALE.micro, color: this.INK3, marginBottom: 11, lineHeight: 1.45 } }, text);
      const divider = () => h("div", { style: { height: 1, background: "#EFEDE7", margin: "22px 0" } });

      // Header in two zones: an identity zone (brand, subject) and a status zone
      // (the active measure, the sample n, the action cluster). It reflows below
      // ~900px instead of overflowing; the measure chip drops first when tight.
      const headerHair = h("span", { style: { width: 1, alignSelf: "stretch", background: this.HAIR, margin: "0 4px" } });
      const eyebrowLabel = (t) => h("span", { style: { ...this.SCALE.eyebrow, letterSpacing: "0.08em", color: this.INK3 } }, t);
      const header = h(
        "header",
        { style: { flex: "0 0 auto", display: "flex", alignItems: "center", gap: 0, minHeight: 54, height: "auto", flexWrap: "wrap", rowGap: 8, padding: "8px 22px", background: "#FFFFFF", borderBottom: "1px solid #E7E4DD" } },
        h(
          "div",
          { style: { display: "flex", alignItems: "baseline", gap: 11 } },
          h("span", { style: { width: 11, height: 11, borderRadius: "50%", background: "#08519c", display: "inline-block", transform: "translateY(1px)" } }),
          h("span", { style: { ...this.SCALE.brand, letterSpacing: "-0.01em", whiteSpace: "nowrap" } }, "Survey Insights")
        ),
        h("span", { style: { margin: "0 14px", color: "#D7D3CA" } }, "·"),
        h("span", { style: { fontSize: 13, color: "#6A6862", fontWeight: 400 } }, "U.S. public opinion about AI"),
        h("div", { style: { flex: 1, minWidth: 16 } }),
        h(
          "div",
          { style: { display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", rowGap: 8 } },
          h(
            "div",
            { className: "hide-when-tight", style: { display: "flex", alignItems: "center", gap: 8 } },
            eyebrowLabel("Active measure"),
            h(
              "span",
              { className: "tnum", style: { fontFamily: "IBM Plex Mono, monospace", fontSize: 12.5, fontWeight: 500, color: "#1B1B1A", background: "#F1F4F8", border: "1px solid #DCE6F1", padding: "3px 9px", borderRadius: 5 } },
              this.state.measure
            )
          ),
          h(
            "div",
            { style: { display: "flex", alignItems: "center", gap: 8 } },
            eyebrowLabel("Sample"),
            h("span", { className: "tnum", style: { fontWeight: 600 } }, "n = " + nShown)
          ),
          headerHair,
          this.uploadControls()
        )
      );

      const rail = h(
        "aside",
        { className: "rail-scroll", style: { flex: "0 0 322px", width: 322, background: "#FFFFFF", borderRight: "1px solid #E7E4DD", overflowY: "auto", padding: "22px 20px 28px" } },
        this.viewIndicator(),
        sectionLabel("Measure"),
        this.measureControl(),
        divider(),
        sectionLabel("Split by", 5),
        helper("None gives the distribution; one dimension gives the breakdown."),
        this.splitControl(),
        divider(),
        sectionLabel("And by", 5),
        helper("A second dimension promotes the view to a cross-tab."),
        this.andbyControl()
      );

      const main = h(
        "main",
        { style: { flex: 1, display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0 } },
        h(
          "div",
          { className: "canvas-scroll", style: { flex: 1, overflowY: "auto", padding: "30px 38px 26px" } },
          h(
            "div",
            { style: { maxWidth: 1000, margin: "0 auto" } },
            this.feedbackRegion(),
            h("div", { style: { ...this.SCALE.display, letterSpacing: "-0.012em", lineHeight: 1.28, color: "#1B1B1A" } }, tb.title),
            h("div", { style: { fontSize: 13, color: "#5E5C56", marginTop: 6, lineHeight: 1.5 } }, tb.subtitle),
            h("div", { className: "tnum", style: { fontFamily: "IBM Plex Mono, monospace", fontSize: 11.5, color: "#9A988F", marginTop: 12, letterSpacing: 0 } }, tb.endpoint),
            this.aggToolbar(),
            h("div", { style: { marginTop: 24 } }, this.buildCanvas())
          )
        ),
        h(
          "footer",
          { style: { flex: "0 0 auto", display: "flex", alignItems: "center", gap: 14, padding: "13px 38px", background: "#F4F2EC", borderTop: "1px solid #E2DFD7" } },
          h(
            "span",
            { style: { flex: "0 0 auto", ...this.SCALE.eyebrow, letterSpacing: "0.08em", color: "#A8745C", background: "#F6EAE2", border: "1px solid #EAD6C7", padding: "3px 8px", borderRadius: 4 } },
            "Methodology"
          ),
          h("span", { style: { fontSize: 12, color: "#4A4843", lineHeight: 1.5 } }, caveat)
        )
      );

      return h(
        "div",
        {
          onDragEnter: this.onDragEnter,
          onDragOver: this.onDragOver,
          onDragLeave: this.onDragLeave,
          onDrop: this.onDrop,
          style: { display: "flex", flexDirection: "column", height: "100vh", width: "100%", background: "#FAFAF8", color: "#1B1B1A", overflow: "hidden", position: "relative" },
        },
        header,
        h("div", { style: { flex: 1, display: "flex", minHeight: 0 } }, rail, main),
        this.dropOverlay()
      );
    }
  }

  ReactDOM.createRoot(document.getElementById("root")).render(h(App));
})();
