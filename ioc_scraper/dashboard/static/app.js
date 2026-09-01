(function () {
  const state = {
    lang: "en",
    theme: "dark",
    view: "control",
    overview: null,
    events: [],
    source: null,
    liveTimer: null,
    live: { sources: {}, completed: 0, failed: 0, reports: 0, accepted: 0, review: 0 }
  };

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  function t(key) {
    const pack = window.ZH_I18N[state.lang] || window.ZH_I18N.en;
    return pack[key] || window.ZH_I18N.en[key] || key;
  }

  function applyI18n() {
    document.documentElement.lang = state.lang === "zh-CN" ? "zh-CN" : "en";
    $$("[data-i18n]").forEach((node) => {
      node.textContent = t(node.getAttribute("data-i18n"));
    });
    $("#theme-toggle").textContent = state.theme === "dark" ? t("themeToLight") : t("themeToDark");
    $("#lang-toggle").textContent = state.lang === "en" ? t("langToZh") : t("langToEn");
    $("#run-collection").textContent = t("runCollection");
    $("#validate-sources").textContent = t("validate");
    document.title = t("brand");
  }

  function persist() {
    localStorage.setItem("zh-theme", state.theme);
    localStorage.setItem("zh-lang", state.lang);
  }

  function setTheme(theme) {
    state.theme = theme;
    document.documentElement.setAttribute("data-theme", theme);
    persist();
    applyI18n();
  }

  function setLang(lang) {
    state.lang = lang;
    persist();
    applyI18n();
    render();
    if (state.view === "sources") loadSources();
    if (state.view === "runs") loadRuns();
    if (state.view === "review") loadReview();
  }

  function banner(message, show) {
    const node = $("#banner");
    node.hidden = !show;
    node.textContent = message || "";
  }

  function el(tag, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  function fmtCount(value) {
    const number = Number(value || 0);
    if (number >= 1000) return (number / 1000).toFixed(1).replace(/\.0$/, "") + "K";
    return String(number);
  }

  function statusClass(status) {
    if (status === "blocked" || status === "failed") return "bad";
    if (status === "layout_changed" || status === "partial" || status === "inspect" || status === "triage") return "warn";
    if (status === "running") return "active";
    if (status === "pending" || !status) return "pending";
    return "";
  }

  function isRunning(overview) {
    return Boolean(overview && overview.current_job && overview.current_job.state === "running");
  }

  function liveMap(overview) {
    const rows = liveJob(overview).sources || [];
    const map = {};
    rows.forEach((row) => {
      map[row.source_id] = row;
    });
    return map;
  }

  function liveJob(overview) {
    const job = (overview && overview.current_job) || {};
    if ((job.sources || []).length) return job;
    const sources = Object.keys(state.live.sources).map((key) => state.live.sources[key]);
    return {
      run_id: job.run_id,
      state: job.state,
      started_at: job.started_at,
      updated_at: job.updated_at,
      completed: state.live.completed,
      failed: state.live.failed,
      reports: state.live.reports,
      accepted: state.live.accepted,
      review: state.live.review,
      sources: sources
    };
  }

  function resetLive() {
    state.live = { sources: {}, completed: 0, failed: 0, reports: 0, accepted: 0, review: 0 };
  }

  function applyLiveEvent(event) {
    if (event.type === "run_started") resetLive();
    const sourceId = event.source_id;
    if (event.type === "source_started" && sourceId != null) {
      state.live.sources[sourceId] = {
        source_id: sourceId,
        source_name: event.source_name || "",
        status: "running",
        reports: 0
      };
    }
    if (event.type === "source_completed" && sourceId != null) {
      const reports = Number(event.reports || 0);
      state.live.sources[sourceId] = {
        source_id: sourceId,
        source_name: event.source_name || "",
        status: event.status || "failed",
        reports: reports
      };
      state.live.completed += 1;
      state.live.reports += reports;
      if (event.status === "failed" || event.status === "blocked") state.live.failed += 1;
    }
    if (event.type === "indicator_accepted") state.live.accepted += 1;
    if (event.type === "indicator_reviewed") state.live.review += 1;
    if (event.type === "run_completed" || event.type === "run_failed") resetLive();
  }

  function paintClass(source, overview) {
    if (!isRunning(overview)) return statusClass(source.status);
    const live = liveMap(overview)[source.source_id];
    if (!live) return "pending";
    return statusClass(live.status);
  }

  function signalTime(overview) {
    const ts = liveJob(overview).updated_at || (state.events.length ? state.events[state.events.length - 1].ts : "");
    if (ts) return String(ts).slice(11, 19) || ts;
    return isRunning(overview) ? t("waiting") : t("justNow");
  }

  function elapsedLabel(overview) {
    const started = liveJob(overview).started_at || (state.events[0] && state.events[0].ts);
    if (!started) return t("inProgress");
    const ms = Date.parse(started);
    if (!ms) return t("inProgress");
    const sec = Math.max(0, Math.floor((Date.now() - ms) / 1000));
    const mm = String(Math.floor(sec / 60)).padStart(2, "0");
    const ss = String(sec % 60).padStart(2, "0");
    return mm + ":" + ss;
  }

  function attentionStatus(code) {
    if (code === "blocked") return t("blocked");
    if (code === "failed") return t("failed");
    if (code === "inspect") return t("inspect");
    if (code === "triage") return t("triage");
    return code;
  }

  function sourceLabel(row) {
    if (!row) return "";
    if (state.lang === "zh-CN") return row.label_zh || row.name || row.title || "";
    return row.label_en || row.name || row.title || "";
  }

  function safeHttpUrl(value) {
    try {
      const parsed = new URL(String(value || "").trim());
      if (parsed.protocol === "http:" || parsed.protocol === "https:") {
        return parsed.href;
      }
    } catch (err) {
      return "";
    }
    return "";
  }

  function sourceLink(row, text) {
    const label = text || sourceLabel(row);
    const href = safeHttpUrl(row && row.url);
    if (!href) {
      const span = el("span");
      span.textContent = label;
      return span;
    }
    const link = el("a");
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = label;
    return link;
  }

  function currentView() {
    const hash = (location.hash || "#/control").replace("#/", "");
    return ["control", "sources", "runs", "review"].includes(hash) ? hash : "control";
  }

  function showView() {
    state.view = currentView();
    $$("[data-view]").forEach((section) => {
      section.hidden = section.getAttribute("data-view") !== state.view;
    });
    $$("[data-nav]").forEach((link) => {
      link.classList.toggle("active", link.getAttribute("data-nav") === state.view);
    });
  }

  function renderMeta(overview) {
    const wrap = $("#run-meta");
    wrap.replaceChildren();
    const run = overview.latest_run || {};
    const job = liveJob(overview);
    const running = isRunning(overview);
    const enabled = (overview.metrics || {}).enabled_sources || 0;
    const stateText = running
      ? t("running") + " " + (job.completed || 0) + "/" + enabled
      : run.run_id
        ? t("complete")
        : t("idle");
    const rows = [
      [t("runId"), running ? job.run_id || t("waiting") : run.run_id || "—"],
      [t("state"), stateText],
      [t("lastSignal"), signalTime(overview)]
    ];
    rows.forEach(([label, value]) => {
      const item = el("div");
      const dt = el("dt");
      dt.textContent = label;
      const dd = el("dd");
      dd.textContent = value;
      item.append(dt, dd);
      wrap.append(item);
    });
  }

  function renderMetrics(overview) {
    const metrics = overview.metrics || {};
    const job = liveJob(overview);
    const running = isRunning(overview);
    const items = running
      ? [
          ["enabledSources", metrics.enabled_sources, t("thisRun") + " " + (job.completed || 0) + " / " + (metrics.enabled_sources || 0)],
          ["reportsAcquired", job.reports, t("thisRun")],
          ["indicatorsNew", job.accepted, t("thisRun")],
          ["humanReview", job.review, t("thisRun")]
        ]
      : [
          ["enabledSources", metrics.enabled_sources, (metrics.nominal_sources || 0) + " " + t("nominal")],
          ["reportsAcquired", metrics.reports_acquired, t("lookback") + " / 3M"],
          ["indicatorsNew", metrics.indicators_new, t("observed") + " " + Number(metrics.indicators_observed || 0).toLocaleString()],
          ["humanReview", metrics.review_pending, t("awaitingDecision")]
        ];
    const wrap = $("#metrics");
    wrap.replaceChildren();
    items.forEach(([key, value, detail]) => {
      const card = el("article", "metric");
      const label = el("p", "label");
      label.textContent = t(key);
      const amount = el("p", "value");
      amount.textContent = String(value || 0);
      const extra = el("p", "detail");
      extra.textContent = detail;
      card.append(label, amount, extra);
      wrap.append(card);
    });
  }

  function renderLanes(overview) {
    const sources = overview.sources || [];
    const metrics = overview.metrics || {};
    const job = liveJob(overview);
    const running = isRunning(overview);
    const lanes = running
      ? [
          ["discover", job.completed],
          ["fetch", job.reports],
          ["extract", (job.accepted || 0) + (job.review || 0)],
          ["persist", job.accepted]
        ]
      : [
          ["discover", sources.length || metrics.enabled_sources],
          ["fetch", metrics.reports_acquired],
          ["extract", metrics.indicators_observed],
          ["persist", metrics.indicators_new]
        ];
    const wrap = $("#lanes");
    wrap.replaceChildren();
    lanes.forEach(([key, total]) => {
      const row = el("div", "lane");
      const label = el("div", "lane-label");
      label.textContent = t(key);
      const track = el("div", "lane-track");
      const count = Math.max(12, Math.min(24, sources.length || 12));
      for (let i = 0; i < count; i += 1) {
        const tick = el("span", "tick");
        const source = sources[i];
        if (source) tick.className = "tick " + paintClass(source, overview);
        else tick.className = "tick pending";
        track.append(tick);
      }
      const amount = el("div", "lane-total");
      amount.textContent = fmtCount(total);
      row.append(label, track, amount);
      wrap.append(row);
    });
    const axis = el("div", "lane-axis");
    const stamps = running
      ? ["00:00", elapsedLabel(overview), t("liveProgress")]
      : [t("lastSnapshot"), runStamp(overview), ""];
    stamps.forEach((stamp) => {
      const node = el("span");
      node.textContent = stamp;
      axis.append(node);
    });
    wrap.append(axis);
    $("#lane-count").textContent = running
      ? t("thisRun") + " " + (job.completed || 0) + " / " + (metrics.enabled_sources || 0)
      : t("lastSnapshot") + " · " + (metrics.enabled_sources || 0) + " / " + (sources.length || 0) + " " + t("sourcesCount");
  }

  function runStamp(overview) {
    const runId = (overview.latest_run && overview.latest_run.run_id) || "";
    return runId ? runId.slice(0, 15) : "";
  }

  function renderAttention(overview) {
    const running = isRunning(overview);
    const catalog = {};
    (overview.sources || []).forEach((source) => {
      catalog[source.source_id] = source;
    });
    let items = overview.attention || [];
    if (running) {
      items = (liveJob(overview).sources || [])
        .filter((row) => row.status === "failed" || row.status === "blocked")
        .map((row) => {
          const source = catalog[row.source_id] || {};
          return {
            priority: row.status === "blocked" ? "P1" : "P2",
            status: row.status === "blocked" ? "blocked" : "failed",
            source_id: row.source_id,
            url: source.url || "",
            label_en: source.label_en || row.source_name || source.name,
            label_zh: source.label_zh || row.source_name || source.name,
            name: source.name || row.source_name,
            title: row.source_name,
            detail: t("thisRun")
          };
        });
    }
    const wrap = $("#attention");
    wrap.replaceChildren();
    $("#signal-count").textContent = String(items.length).padStart(2, "0") + " " + t("signals");
    if (!items.length) {
      const empty = el("p", "empty");
      empty.textContent = t("empty");
      wrap.append(empty);
      return;
    }
    items.forEach((item) => {
      const row = el("article", "attention-item");
      const copy = el("div");
      const title = el("h3");
      const prio = el("span", "prio");
      prio.textContent = item.priority || "P2";
      title.append(prio, document.createTextNode(" "), sourceLink(item));
      const detail = el("p", "muted");
      detail.textContent = item.detail || "";
      copy.append(title, detail);
      const status = el("strong", "state " + (item.status || ""));
      status.textContent = attentionStatus(item.status);
      row.append(copy, status);
      wrap.append(row);
    });
  }

  function renderField(overview) {
    const wrap = $("#source-field");
    wrap.replaceChildren();
    (overview.sources || []).forEach((source) => {
      const cell = el("button", "cell " + paintClass(source, overview));
      cell.type = "button";
      cell.textContent = String(source.source_id).padStart(2, "0");
      cell.addEventListener("click", () => {
        location.hash = "#/sources";
      });
      wrap.append(cell);
    });
  }

  function renderEvents() {
    const wrap = $("#event-stream");
    wrap.replaceChildren();
    const rows = state.events.slice(-5).reverse();
    if (!rows.length) {
      const ready = el("div", "cursor");
      ready.textContent = t("systemReady");
      wrap.append(ready);
      return;
    }
    rows.forEach((event) => {
      const line = el("div", event.type === "run_failed" || event.type === "failure_recorded" ? "warn" : "ok");
      const ts = (event.ts || "").slice(11, 19);
      line.textContent = ts + " " + (event.type || "").toUpperCase() + " " + summarize(event);
      wrap.append(line);
    });
    const ready = el("div", "cursor");
    ready.textContent = t("systemReady");
    wrap.append(ready);
  }

  function summarize(event) {
    return Object.keys(event)
      .filter((key) => !["type", "ts"].includes(key))
      .map((key) => key + "=" + event[key])
      .join(" ");
  }

  function fillTable(table, headers, rows) {
    table.replaceChildren();
    const head = el("thead");
    const hr = el("tr");
    headers.forEach((label) => {
      const th = el("th");
      th.textContent = label;
      hr.append(th);
    });
    head.append(hr);
    const body = el("tbody");
    if (!rows.length) {
      const empty = el("tr");
      const td = el("td");
      td.colSpan = headers.length;
      td.textContent = t("empty");
      empty.append(td);
      body.append(empty);
    } else {
      rows.forEach((cells) => {
        const tr = el("tr");
        cells.forEach((value) => {
          const td = el("td");
          if (value instanceof Node) {
            td.append(value);
          } else {
            td.textContent = value == null ? "" : String(value);
          }
          tr.append(td);
        });
        body.append(tr);
      });
    }
    table.append(head, body);
  }

  async function loadSources() {
    const payload = await getJSON("/api/sources");
    fillTable(
      $("#sources-table"),
      [t("source"), t("type"), t("status"), t("reports")],
      (payload.sources || []).map((row) => [
        sourceLink(row, String(row.source_id).padStart(2, "0") + " " + sourceLabel(row)),
        row.strategy || "",
        row.status || "",
        String(row.discovered_reports || 0)
      ])
    );
  }

  async function loadRuns() {
    const payload = await getJSON("/api/runs");
    fillTable(
      $("#runs-table"),
      [t("runId"), t("status"), t("added"), t("reviewQueue"), t("failures")],
      (payload.runs || []).map((row) => [
        row.run_id || "",
        row.state || "",
        String(row.added_indicators || 0),
        String(row.review_indicators || 0),
        String(row.failures || 0)
      ])
    );
  }

  async function loadReview() {
    const payload = await getJSON("/api/review");
    const wrap = $("#review-list");
    wrap.replaceChildren();
    const items = payload.items || [];
    if (!items.length) {
      const empty = el("p", "empty");
      empty.textContent = t("empty");
      wrap.append(empty);
      return;
    }
    items.forEach((item) => {
      const card = el("article", "review-card");
      const title = el("h3");
      title.textContent = (item.content || "") + " · " + (item.type || "");
      const meta = el("p", "muted");
      meta.textContent = (item.run_id || "") + " / " + (item.source_name || "") + " / " + (item.decision || t("pending"));
      const evidence = el("p");
      evidence.textContent = item.evidence || "";
      card.append(title, meta, evidence);
      if (item.decision === "pending") {
        const actions = el("div", "review-actions");
        const note = el("input");
        note.type = "text";
        note.placeholder = t("note");
        const accept = el("button", "primary");
        accept.textContent = t("accept");
        const reject = el("button");
        reject.textContent = t("reject");
        accept.addEventListener("click", () => decide(item, "accept", note.value));
        reject.addEventListener("click", () => decide(item, "reject", note.value));
        actions.append(note, accept, reject);
        card.append(actions);
      }
      wrap.append(card);
    });
  }

  async function decide(item, decision, note) {
    const response = await fetch(
      "/api/review/" + encodeURIComponent(item.run_id) + "/" + encodeURIComponent(item.indicator_id) + "/decision",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: decision, note: note || "" })
      }
    );
    if (response.status === 409) {
      banner(t("conflict"), true);
      return;
    }
    if (!response.ok) {
      banner(t("failedRequest"), true);
      return;
    }
    banner("", false);
    loadReview();
    loadOverview();
  }

  async function getJSON(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error("request failed");
    return response.json();
  }

  function render() {
    showView();
    if (!state.overview) return;
    renderMeta(state.overview);
    renderMetrics(state.overview);
    renderLanes(state.overview);
    renderAttention(state.overview);
    renderField(state.overview);
    renderEvents();
    const running = isRunning(state.overview);
    $("#run-collection").disabled = Boolean(running);
    $(".live-dot").classList.toggle("active", Boolean(running));
  }

  async function loadOverview() {
    try {
      state.overview = await getJSON("/api/overview");
      banner("", false);
      render();
      if (isRunning(state.overview)) startLivePoll();
      if (state.view === "sources") loadSources();
    } catch (err) {
      banner(t("disconnected"), true);
    }
  }

  function startLivePoll() {
    if (state.liveTimer) return;
    state.liveTimer = setInterval(() => {
      if (isRunning(state.overview)) {
        loadOverview();
      } else if (state.liveTimer) {
        clearInterval(state.liveTimer);
        state.liveTimer = null;
      }
    }, 2000);
  }

  async function startRun() {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lookback: "3m" })
    });
    if (response.status === 409) {
      banner(t("conflict"), true);
      return;
    }
    if (!response.ok) {
      banner(t("failedRequest"), true);
      return;
    }
    banner("", false);
    connectEvents();
    startLivePoll();
    loadOverview();
  }

  async function validateSources() {
    const response = await fetch("/api/sources/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    if (!response.ok) {
      banner(t("failedRequest"), true);
      return;
    }
    loadSources();
  }

  function connectEvents() {
    if (state.source) state.source.close();
    const source = new EventSource("/api/runs/current/events");
    state.source = source;
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data);
        applyLiveEvent(event);
        state.events.push(event);
        renderEvents();
        if (
          event.type === "run_started" ||
          event.type === "source_started" ||
          event.type === "source_completed" ||
          event.type === "run_completed" ||
          event.type === "run_failed"
        ) {
          loadOverview();
        }
      } catch (err) {
        banner(t("stale"), true);
      }
    };
    source.onerror = () => {};
  }

  function boot() {
    const savedTheme = localStorage.getItem("zh-theme");
    const savedLang = localStorage.getItem("zh-lang");
    if (savedTheme) {
      setTheme(savedTheme);
    } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
      setTheme("light");
    } else {
      setTheme("dark");
    }
    if (savedLang) state.lang = savedLang;
    applyI18n();
    showView();
    $("#theme-toggle").addEventListener("click", () => {
      setTheme(state.theme === "dark" ? "light" : "dark");
    });
    $("#lang-toggle").addEventListener("click", () => {
      setLang(state.lang === "en" ? "zh-CN" : "en");
    });
    $("#run-collection").addEventListener("click", startRun);
    $("#validate-sources").addEventListener("click", validateSources);
    window.addEventListener("hashchange", () => {
      showView();
      if (state.view === "sources") loadSources();
      if (state.view === "runs") loadRuns();
      if (state.view === "review") loadReview();
    });
    loadOverview();
    if (state.view === "sources") loadSources();
    if (state.view === "runs") loadRuns();
    if (state.view === "review") loadReview();
    connectEvents();
  }

  boot();
})();
