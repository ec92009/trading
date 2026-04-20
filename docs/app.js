const TAB_CONFIG = {
  botlog: {
    label: "Runtime Log",
    bundledPath: "./data/recent_bot.log",
    bundledLabel: "committed runtime log",
  },
  decisions: {
    label: "Decision Log",
    bundledPath: "./data/recent_decisions.json",
    bundledLabel: "committed decision log",
  },
  trades: {
    label: "Trade Journal",
    bundledPath: "./data/recent_trades.tsv",
    bundledLabel: "committed trade journal",
  },
};

function emptyDataset(type) {
  return {
    type,
    source: "empty",
    sourceLabel: "No data loaded yet.",
    rawText: "",
    parsedType: type,
    records: [],
    headers: [],
    summary: [],
  };
}

const state = {
  activeTab: "botlog",
  datasets: {
    botlog: emptyDataset("botlog"),
    decisions: emptyDataset("decisions"),
    trades: emptyDataset("trades"),
  },
};

const dom = {
  fileInput: document.getElementById("fileInput"),
  dropzone: document.getElementById("dropzone"),
  statusText: document.getElementById("statusText"),
  sourceText: document.getElementById("sourceText"),
  versionBadge: document.getElementById("versionBadge"),
  reloadBundled: document.getElementById("reloadBundled"),
  symbolFilter: document.getElementById("symbolFilter"),
  eventFilter: document.getElementById("eventFilter"),
  textFilter: document.getElementById("textFilter"),
  limitInput: document.getElementById("limitInput"),
  summaryPanel: document.getElementById("summaryPanel"),
  resultsPanel: document.getElementById("resultsPanel"),
  summaryCardTemplate: document.getElementById("summaryCardTemplate"),
  tabButtons: Array.from(document.querySelectorAll(".tab-button")),
};

const filterInputs = [dom.symbolFilter, dom.eventFilter, dom.textFilter, dom.limitInput];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function parseDecisionLog(text) {
  let records = [];
  const trimmed = text.trim();
  if (!trimmed) {
    return [];
  }
  if (trimmed.startsWith("[")) {
    const parsed = JSON.parse(trimmed);
    records = Array.isArray(parsed) ? parsed : [];
  } else {
    records = text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => JSON.parse(line));
  }
  if (!records.every((entry) => entry && entry.event_type && entry.timestamp_utc)) {
    throw new Error("Not a decision log");
  }
  return records;
}

function parseTradesTsv(text) {
  const lines = text.split(/\r?\n/).filter(Boolean);
  if (!lines.length || !lines[0].includes("\t")) {
    throw new Error("Not a TSV file");
  }
  const headers = lines[0].split("\t");
  if (!headers.includes("symbol") || !headers.includes("order_id")) {
    throw new Error("Not a trades TSV");
  }
  const rows = lines.slice(1).map((line) => {
    const values = line.split("\t");
    return Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""]));
  });
  return { headers, rows };
}

function parseBotLog(text) {
  const records = text
    .split(/\r?\n/)
    .map((line, index) => {
      const match = line.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[([^\]]+)\] (.*)$/);
      if (!match) {
        return null;
      }
      return {
        id: index,
        timestamp: match[1],
        logger: match[2],
        message: match[3],
      };
    })
    .filter(Boolean);
  if (!records.length) {
    throw new Error("Not a bot log");
  }
  return records;
}

function summarize(dataset) {
  if (dataset.parsedType === "decisions") {
    const symbols = new Set(dataset.records.map((record) => record.symbol).filter(Boolean));
    const submitted = dataset.records.filter((record) => record.event_type === "order_submitted").length;
    const targetChanges = dataset.records.filter((record) => (record.state?.trigger_type || "").includes("copytrade")).length;
    return [
      ["Entries", dataset.records.length],
      ["Symbols", symbols.size || 0],
      ["Orders", submitted],
      ["Copytrade Events", targetChanges],
    ];
  }
  if (dataset.parsedType === "trades") {
    const symbols = new Set(dataset.records.map((row) => row.symbol).filter(Boolean));
    const pending = dataset.records.filter((row) => (row.status || "").toLowerCase() === "pending").length;
    const filled = dataset.records.filter((row) => (row.status || "").toLowerCase() === "filled").length;
    const buys = dataset.records.filter((row) => (row.side || "").toLowerCase() === "buy").length;
    return [
      ["Rows", dataset.records.length],
      ["Symbols", symbols.size || 0],
      ["Pending", pending],
      ["Buys", buys],
    ];
  }
  if (dataset.parsedType === "botlog") {
    const loggers = new Set(dataset.records.map((row) => row.logger));
    const errors = dataset.records.filter((row) => /error/i.test(row.message)).length;
    const marketMessages = dataset.records.filter((row) => /market/i.test(row.message)).length;
    return [
      ["Lines", dataset.records.length],
      ["Loggers", loggers.size || 0],
      ["Errors", errors],
      ["Market Notes", marketMessages],
    ];
  }
  return [];
}

function badgeClass(value) {
  const text = String(value || "").toLowerCase();
  if (text.includes("buy") || text.includes("filled")) return "badge-buy";
  if (text.includes("sell") || text.includes("error") || text.includes("failed")) return "badge-sell";
  if (text.includes("warn") || text.includes("pending") || text.includes("closed")) return "badge-warn";
  return "badge-neutral";
}

function severityClass(message) {
  const normalized = String(message || "").toLowerCase();
  if (normalized.includes("error")) return "badge-sell";
  if (normalized.includes("closed") || normalized.includes("waiting")) return "badge-warn";
  if (normalized.includes("buy") || normalized.includes("applying")) return "badge-buy";
  return "badge-neutral";
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  if (Array.isArray(value)) {
    return value.join(", ");
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function formatMoney(value) {
  const number = Number.parseFloat(value);
  if (!Number.isFinite(number)) {
    return null;
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: number >= 1000 ? 0 : 2,
  }).format(number);
}

function formatPercent(value) {
  const number = Number.parseFloat(value);
  if (!Number.isFinite(number)) {
    return null;
  }
  return `${(number * 100).toFixed(2)}%`;
}

function decisionTitle(record) {
  const side = String(record.order?.alpaca_request?.side || record.state?.side || "").toUpperCase();
  const symbol = record.symbol || "Portfolio";
  const notional = record.order?.alpaca_request?.notional;
  const qty = record.order?.alpaca_request?.qty ?? record.state?.qty;
  if (record.event_type === "order_submitted") {
    if (notional !== undefined) {
      return `${side || "ORDER"} ${symbol} for ${formatMoney(notional) || notional}`;
    }
    if (qty !== undefined) {
      return `${side || "ORDER"} ${symbol} for ${qty} shares`;
    }
  }
  return `${String(record.event_type || "event").replaceAll("_", " ")}${record.symbol ? `: ${symbol}` : ""}`;
}

function decisionNarrative(record) {
  const trigger = record.state?.trigger_type ? `Trigger: ${record.state.trigger_type}.` : "";
  const targetWeight = formatPercent(record.state?.target_weight);
  const targetValue = formatMoney(record.state?.target_value);
  const target = targetWeight || targetValue
    ? ` Target ${targetWeight || "—"}${targetValue ? ` (${targetValue})` : ""}.`
    : "";
  return `${record.rationale || "No rationale recorded."}${trigger ? ` ${trigger}` : ""}${target}`;
}

function tradeRequestSummary(row) {
  try {
    const parsed = JSON.parse(row.alpaca_request || "{}");
    if (parsed.notional !== undefined) {
      return `${String(parsed.side || row.side || "").toUpperCase()} ${row.symbol} for ${formatMoney(parsed.notional) || parsed.notional}`;
    }
    if (parsed.qty !== undefined) {
      return `${String(parsed.side || row.side || "").toUpperCase()} ${row.symbol} for ${parsed.qty} units`;
    }
  } catch (_error) {
    return null;
  }
  const amount = formatMoney(row.notional) || row.notional || "—";
  return `${String(row.side || "").toUpperCase()} ${row.symbol} for ${amount}`;
}

function applyFilters(records, type) {
  const symbolNeedle = dom.symbolFilter.value.trim().toLowerCase();
  const eventNeedle = dom.eventFilter.value.trim().toLowerCase();
  const textNeedle = dom.textFilter.value.trim().toLowerCase();

  return records.filter((record) => {
    if (type === "decisions") {
      const symbol = String(record.symbol || "").toLowerCase();
      const event = String(record.event_type || "").toLowerCase();
      const haystack = JSON.stringify(record).toLowerCase();
      return (
        (!symbolNeedle || symbol.includes(symbolNeedle) || haystack.includes(symbolNeedle)) &&
        (!eventNeedle || event.includes(eventNeedle) || haystack.includes(eventNeedle)) &&
        (!textNeedle || haystack.includes(textNeedle))
      );
    }
    if (type === "trades") {
      const symbol = String(record.symbol || "").toLowerCase();
      const status = String(record.status || "").toLowerCase();
      const haystack = JSON.stringify(record).toLowerCase();
      return (
        (!symbolNeedle || symbol.includes(symbolNeedle) || haystack.includes(symbolNeedle)) &&
        (!eventNeedle || status.includes(eventNeedle) || haystack.includes(eventNeedle)) &&
        (!textNeedle || haystack.includes(textNeedle))
      );
    }
    if (type === "botlog") {
      const logger = String(record.logger || "").toLowerCase();
      const haystack = `${record.timestamp} ${record.logger} ${record.message}`.toLowerCase();
      return (
        (!symbolNeedle || haystack.includes(symbolNeedle)) &&
        (!eventNeedle || logger.includes(eventNeedle) || haystack.includes(eventNeedle)) &&
        (!textNeedle || haystack.includes(textNeedle))
      );
    }
    return true;
  });
}

function visibleRecords(records) {
  const limit = Number.parseInt(dom.limitInput.value, 10);
  const normalizedLimit = Number.isFinite(limit) && limit > 0 ? limit : 120;
  return [...records].reverse().slice(0, normalizedLimit);
}

function renderSummary(cards) {
  dom.summaryPanel.innerHTML = "";
  for (const [label, value] of cards) {
    const node = dom.summaryCardTemplate.content.firstElementChild.cloneNode(true);
    node.querySelector(".summary-label").textContent = label;
    node.querySelector(".summary-value").textContent = value;
    dom.summaryPanel.appendChild(node);
  }
}

function renderDecisionCards(records) {
  if (!records.length) {
    return "<section class=\"panel empty-state\"><h2>No matching decision events</h2><p>Adjust the filters to widen the result set.</p></section>";
  }
  const cards = records.map((record) => {
    const orderPairs = Object.entries(record.order || {});
    const statePairs = Object.entries(record.state || {});
    return `
      <article class="event-card">
        <div class="event-topline">
          <span class="badge ${badgeClass(record.event_type)}">${escapeHtml(record.event_type)}</span>
          ${record.symbol ? `<span class="badge ${badgeClass(record.order?.alpaca_request?.side || record.state?.side || "")}">${escapeHtml(record.symbol)}</span>` : ""}
          <span class="timestamp">${escapeHtml(record.timestamp_utc)}</span>
        </div>
        <h3 class="entry-title">${escapeHtml(decisionTitle(record))}</h3>
        <p class="rationale">${escapeHtml(decisionNarrative(record))}</p>
        <div class="kv-grid">
          <section class="kv-box">
            <h4>Decision state</h4>
            <div class="kv-list">
              ${statePairs.length ? statePairs.map(([key, value]) => `<div class="kv-row"><span>${escapeHtml(key)}</span><span class="mono">${escapeHtml(formatValue(value))}</span></div>`).join("") : "<div class=\"kv-row\"><span>No state</span><span>—</span></div>"}
            </div>
          </section>
          <section class="kv-box">
            <h4>Order payload</h4>
            <div class="kv-list">
              ${orderPairs.length ? orderPairs.map(([key, value]) => `<div class="kv-row"><span>${escapeHtml(key)}</span><span class="mono">${escapeHtml(formatValue(value))}</span></div>`).join("") : "<div class=\"kv-row\"><span>No order payload</span><span>—</span></div>"}
            </div>
          </section>
        </div>
      </article>
    `;
  }).join("");
  return `<section class="panel results"><div class="stack">${cards}</div></section>`;
}

function renderTradesCards(records) {
  if (!records.length) {
    return "<section class=\"panel empty-state\"><h2>No matching trade rows</h2><p>Adjust the filters to widen the result set.</p></section>";
  }
  const cards = records.map((row) => `
    <article class="event-card">
      <div class="event-topline">
        <span class="badge ${badgeClass(row.status)}">${escapeHtml(row.status || "unknown")}</span>
        <span class="badge ${badgeClass(row.side)}">${escapeHtml(row.symbol || "—")}</span>
        <span class="timestamp">${escapeHtml(row.submitted_at || row.executed_at || row.filled_at || "—")}</span>
      </div>
      <h3 class="entry-title">${escapeHtml(tradeRequestSummary(row))}</h3>
      <p class="rationale">${escapeHtml(row.rationale || "No rationale recorded.")}</p>
      <div class="kv-grid">
        <section class="kv-box">
          <h4>Order status</h4>
          <div class="kv-list">
            <div class="kv-row"><span>Order ID</span><span class="mono">${escapeHtml(row.order_id || "—")}</span></div>
            <div class="kv-row"><span>Side</span><span class="mono">${escapeHtml(row.side || "—")}</span></div>
            <div class="kv-row"><span>Notional</span><span class="mono">${escapeHtml(formatMoney(row.notional) || row.notional || "—")}</span></div>
            <div class="kv-row"><span>Average price</span><span class="mono">${escapeHtml(row.avg_price || "—")}</span></div>
          </div>
        </section>
        <section class="kv-box">
          <h4>Timestamps</h4>
          <div class="kv-list">
            <div class="kv-row"><span>Submitted</span><span class="mono">${escapeHtml(row.submitted_at || "—")}</span></div>
            <div class="kv-row"><span>Executed</span><span class="mono">${escapeHtml(row.executed_at || "—")}</span></div>
            <div class="kv-row"><span>Filled</span><span class="mono">${escapeHtml(row.filled_at || "—")}</span></div>
            <div class="kv-row"><span>Request</span><span class="mono">${escapeHtml(row.alpaca_request || "—")}</span></div>
          </div>
        </section>
      </div>
    </article>
  `).join("");
  return `<section class="panel results"><div class="stack">${cards}</div></section>`;
}

function renderBotLog(records) {
  if (!records.length) {
    return "<section class=\"panel empty-state\"><h2>No matching log lines</h2><p>Adjust the filters to widen the result set.</p></section>";
  }
  const lines = records.map((record) => `
    <article class="log-line-card">
      <div class="line-topline">
        <span class="badge ${severityClass(record.message)}">${escapeHtml(record.logger)}</span>
        <span class="timestamp">${escapeHtml(record.timestamp)}</span>
      </div>
      <h3 class="entry-title">${escapeHtml(record.message)}</h3>
    </article>
  `).join("");
  return `<section class="panel results"><div class="stack">${lines}</div></section>`;
}

function renderEmptyForTab(tab) {
  const label = TAB_CONFIG[tab].label;
  return `<h2>${escapeHtml(label)}</h2><p>No data loaded for this tab yet.</p>`;
}

function currentDataset() {
  return state.datasets[state.activeTab];
}

function updateStatusText(dataset) {
  dom.statusText.textContent = `${TAB_CONFIG[state.activeTab].label}: ${dataset.sourceLabel}`;
  dom.sourceText.textContent = dataset.source === "bundled"
    ? "This tab is showing the last snapshot the bot committed to GitHub Pages."
    : dataset.source === "local"
      ? "This tab is showing a local override loaded in your browser only."
      : "This tab does not have data yet.";
}

function syncTabButtons() {
  for (const button of dom.tabButtons) {
    const active = button.dataset.tab === state.activeTab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  }
}

function render() {
  syncTabButtons();
  const dataset = currentDataset();
  renderSummary(dataset.summary);
  updateStatusText(dataset);

  if (!dataset.rawText) {
    dom.resultsPanel.className = "panel empty-state";
    dom.resultsPanel.innerHTML = renderEmptyForTab(state.activeTab);
    return;
  }

  const filtered = visibleRecords(applyFilters(dataset.records, dataset.parsedType));
  let html = "";
  if (dataset.parsedType === "decisions") {
    html = renderDecisionCards(filtered);
  } else if (dataset.parsedType === "trades") {
    html = renderTradesCards(filtered);
  } else if (dataset.parsedType === "botlog") {
    html = renderBotLog(filtered);
  } else {
    html = `
      <section class="panel results">
        <pre class="raw-block">${escapeHtml(dataset.rawText)}</pre>
      </section>
    `;
  }
  dom.resultsPanel.outerHTML = html;
  dom.resultsPanel = document.querySelector(".results, .empty-state");
}

function setDataset(tab, payload) {
  state.datasets[tab] = {
    type: tab,
    source: payload.source,
    sourceLabel: payload.sourceLabel,
    rawText: payload.rawText,
    parsedType: payload.parsedType,
    records: payload.records,
    headers: payload.headers || [],
    summary: summarize(payload),
  };
}

async function loadBundledTab(tab) {
  const config = TAB_CONFIG[tab];
  const response = await fetch(config.bundledPath, { cache: "no-store" });
  if (!response.ok) {
    setDataset(tab, {
      source: "empty",
      sourceLabel: `Could not load ${config.bundledLabel}.`,
      rawText: "",
      parsedType: tab,
      records: [],
      headers: [],
    });
    return;
  }
  const rawText = await response.text();
  let parsedType = tab;
  let records = [];
  let headers = [];
  if (tab === "decisions") {
    records = parseDecisionLog(rawText);
  } else if (tab === "trades") {
    const parsed = parseTradesTsv(rawText);
    records = parsed.rows;
    headers = parsed.headers;
  } else {
    records = parseBotLog(rawText);
  }
  setDataset(tab, {
    source: "bundled",
    sourceLabel: `Showing ${config.bundledLabel}.`,
    rawText,
    parsedType,
    records,
    headers,
  });
}

async function loadAllBundled() {
  dom.statusText.textContent = "Loading committed snapshots.";
  await Promise.all(Object.keys(TAB_CONFIG).map((tab) => loadBundledTab(tab)));
  render();
}

async function handleFile(file) {
  const rawText = await file.text();
  const name = file.name.toLowerCase();
  let tab = "";
  let parsedType = "";
  let records = [];
  let headers = [];

  try {
    if (name.endsWith(".json") || name.endsWith(".jsonl")) {
      tab = "decisions";
      parsedType = "decisions";
      records = parseDecisionLog(rawText);
    } else if (name.endsWith(".tsv")) {
      tab = "trades";
      parsedType = "trades";
      const parsed = parseTradesTsv(rawText);
      records = parsed.rows;
      headers = parsed.headers;
    } else {
      tab = "botlog";
      parsedType = "botlog";
      records = parseBotLog(rawText);
    }
  } catch (_error) {
    dom.statusText.textContent = `Could not parse ${file.name}.`;
    dom.sourceText.textContent = "Supported types are decision JSONL/JSON, trades TSV, and bot log text.";
    return;
  }

  setDataset(tab, {
    source: "local",
    sourceLabel: `Loaded local override: ${file.name}.`,
    rawText,
    parsedType,
    records,
    headers,
  });
  state.activeTab = tab;
  render();
}

function resetDragState() {
  dom.dropzone.classList.remove("dragging");
}

dom.fileInput.addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (file) {
    await handleFile(file);
  }
});

dom.dropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dom.dropzone.classList.add("dragging");
});

dom.dropzone.addEventListener("dragleave", resetDragState);
dom.dropzone.addEventListener("drop", async (event) => {
  event.preventDefault();
  resetDragState();
  const file = event.dataTransfer?.files?.[0];
  if (file) {
    await handleFile(file);
  }
});

dom.dropzone.addEventListener("keydown", () => {
  dom.fileInput.click();
});

for (const button of dom.tabButtons) {
  button.addEventListener("click", () => {
    state.activeTab = button.dataset.tab;
    render();
  });
}

dom.reloadBundled.addEventListener("click", async () => {
  await loadAllBundled();
});

for (const input of filterInputs) {
  input.addEventListener("input", render);
}

loadVersionBadge();
loadAllBundled();
async function loadVersionBadge() {
  try {
    const response = await fetch("./data/version.json", { cache: "no-store" });
    if (!response.ok) {
      dom.versionBadge.textContent = "v—";
      return;
    }
    const payload = await response.json();
    dom.versionBadge.textContent = payload.display || `v${payload.version || "—"}`;
  } catch (_error) {
    dom.versionBadge.textContent = "v—";
  }
}
