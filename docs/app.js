const state = {
  fileName: "",
  rawText: "",
  mode: "auto",
  parsedType: null,
  records: [],
  headers: [],
  summary: [],
};

const dom = {
  fileInput: document.getElementById("fileInput"),
  dropzone: document.getElementById("dropzone"),
  statusText: document.getElementById("statusText"),
  viewSelect: document.getElementById("viewSelect"),
  symbolFilter: document.getElementById("symbolFilter"),
  eventFilter: document.getElementById("eventFilter"),
  textFilter: document.getElementById("textFilter"),
  limitInput: document.getElementById("limitInput"),
  summaryPanel: document.getElementById("summaryPanel"),
  resultsPanel: document.getElementById("resultsPanel"),
  summaryCardTemplate: document.getElementById("summaryCardTemplate"),
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
  if (!records.length || !records.every((entry) => entry && entry.event_type && entry.timestamp_utc)) {
    throw new Error("Not a decision log");
  }
  return records;
}

function parseTradesTsv(text) {
  const lines = text.split(/\r?\n/).filter(Boolean);
  if (lines.length < 2 || !lines[0].includes("\t")) {
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

function detectAndParse(text, forcedMode = "auto") {
  const mode = forcedMode === "auto" ? null : forcedMode;
  const attempts = mode
    ? [mode]
    : ["decisions", "trades", "botlog"];

  for (const attempt of attempts) {
    try {
      if (attempt === "decisions") {
        const records = parseDecisionLog(text);
        return { type: "decisions", records };
      }
      if (attempt === "trades") {
        const { headers, rows } = parseTradesTsv(text);
        return { type: "trades", records: rows, headers };
      }
      if (attempt === "botlog") {
        const records = parseBotLog(text);
        return { type: "botlog", records };
      }
    } catch (error) {
      continue;
    }
  }
  return { type: "raw", records: [] };
}

function summarize(parsed) {
  if (parsed.type === "decisions") {
    const symbols = new Set(parsed.records.map((record) => record.symbol).filter(Boolean));
    const eventCounts = {};
    let submitted = 0;
    for (const record of parsed.records) {
      eventCounts[record.event_type] = (eventCounts[record.event_type] ?? 0) + 1;
      if (record.event_type === "order_submitted") {
        submitted += 1;
      }
    }
    const topEvent = Object.entries(eventCounts).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "n/a";
    return [
      ["Entries", parsed.records.length],
      ["Symbols", symbols.size || 0],
      ["Orders Submitted", submitted],
      ["Top Event", topEvent],
    ];
  }
  if (parsed.type === "trades") {
    const symbols = new Set(parsed.records.map((row) => row.symbol).filter(Boolean));
    const pending = parsed.records.filter((row) => (row.status || "").toLowerCase() === "pending").length;
    const filled = parsed.records.filter((row) => (row.status || "").toLowerCase() === "filled").length;
    return [
      ["Rows", parsed.records.length],
      ["Symbols", symbols.size || 0],
      ["Pending", pending],
      ["Filled", filled],
    ];
  }
  if (parsed.type === "botlog") {
    const loggers = new Set(parsed.records.map((row) => row.logger));
    const errors = parsed.records.filter((row) => /error/i.test(row.message)).length;
    const warnings = parsed.records.filter((row) => /warn/i.test(row.message)).length;
    return [
      ["Lines", parsed.records.length],
      ["Loggers", loggers.size || 0],
      ["Errors", errors],
      ["Warnings", warnings],
    ];
  }
  return [];
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

function badgeClass(value) {
  const text = String(value || "").toLowerCase();
  if (text.includes("buy")) return "badge-buy";
  if (text.includes("sell") || text.includes("error")) return "badge-sell";
  if (text.includes("stop") || text.includes("warn")) return "badge-warn";
  return "badge-neutral";
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
        (!symbolNeedle || symbol.includes(symbolNeedle)) &&
        (!eventNeedle || event.includes(eventNeedle)) &&
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
    if (type === "trades") {
      const symbol = String(record.symbol || "").toLowerCase();
      const event = String(record.status || "").toLowerCase();
      const haystack = JSON.stringify(record).toLowerCase();
      return (
        (!symbolNeedle || symbol.includes(symbolNeedle)) &&
        (!eventNeedle || event.includes(eventNeedle)) &&
        (!textNeedle || haystack.includes(textNeedle))
      );
    }
    return true;
  });
}

function visibleRecords(records) {
  const limit = Number.parseInt(dom.limitInput.value, 10);
  const normalizedLimit = Number.isFinite(limit) && limit > 0 ? limit : 200;
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
    return "<section class=\"panel empty-state\"><h2>No matching events</h2><p>Adjust the filters to widen the result set.</p></section>";
  }
  const cards = records
    .map((record) => {
      const orderPairs = Object.entries(record.order || {});
      const statePairs = Object.entries(record.state || {});
      return `
        <article class="event-card">
          <div class="event-topline">
            <span class="badge ${badgeClass(record.event_type)}">${escapeHtml(record.event_type)}</span>
            ${record.symbol ? `<span class="badge ${badgeClass(record.order?.alpaca_request?.side || record.state?.side || "")}">${escapeHtml(record.symbol)}</span>` : ""}
            <span class="timestamp">${escapeHtml(record.timestamp_utc)}</span>
          </div>
          <p class="rationale">${escapeHtml(record.rationale || "No rationale recorded.")}</p>
          <div class="kv-grid">
            <section class="kv-box">
              <h3>State</h3>
              <div class="kv-list">
                ${statePairs.length ? statePairs.map(([key, value]) => `<div class="kv-row"><span>${escapeHtml(key)}</span><span class="mono">${escapeHtml(formatValue(value))}</span></div>`).join("") : "<div class=\"kv-row\"><span>No state</span><span>—</span></div>"}
              </div>
            </section>
            <section class="kv-box">
              <h3>Order</h3>
              <div class="kv-list">
                ${orderPairs.length ? orderPairs.map(([key, value]) => `<div class="kv-row"><span>${escapeHtml(key)}</span><span class="mono">${escapeHtml(formatValue(value))}</span></div>`).join("") : "<div class=\"kv-row\"><span>No order payload</span><span>—</span></div>"}
              </div>
            </section>
          </div>
        </article>
      `;
    })
    .join("");
  return `<section class="panel results"><div class="stack">${cards}</div></section>`;
}

function renderBotLog(records) {
  if (!records.length) {
    return "<section class=\"panel empty-state\"><h2>No matching lines</h2><p>Adjust the filters to widen the result set.</p></section>";
  }
  const lines = records
    .map((record) => `
      <article class="log-line-card">
        <div class="line-topline">
          <span class="badge ${badgeClass(record.logger)}">${escapeHtml(record.logger)}</span>
          <span class="timestamp">${escapeHtml(record.timestamp)}</span>
        </div>
        <p class="message">${escapeHtml(record.message)}</p>
      </article>
    `)
    .join("");
  return `<section class="panel results"><div class="stack">${lines}</div></section>`;
}

function renderTradesTable(records, headers) {
  if (!records.length) {
    return "<section class=\"panel empty-state\"><h2>No matching rows</h2><p>Adjust the filters to widen the result set.</p></section>";
  }
  const head = headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("");
  const body = records
    .map((row) => `<tr>${headers.map((header) => `<td class="mono">${escapeHtml(row[header] || "")}</td>`).join("")}</tr>`)
    .join("");
  return `
    <section class="panel results">
      <div class="table-wrap">
        <table>
          <thead><tr>${head}</tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </section>
  `;
}

function renderRaw(text) {
  return `
    <section class="panel results">
      <pre class="raw-block">${escapeHtml(text)}</pre>
    </section>
  `;
}

function render() {
  renderSummary(state.summary);
  if (!state.rawText) {
    dom.resultsPanel.className = "panel empty-state";
    dom.resultsPanel.innerHTML = "<h2>Ready</h2><p>Load one of the trading log files to begin.</p>";
    return;
  }

  const filtered = visibleRecords(applyFilters(state.records, state.parsedType));
  let html = "";
  if (state.parsedType === "decisions") {
    html = renderDecisionCards(filtered);
  } else if (state.parsedType === "botlog") {
    html = renderBotLog(filtered);
  } else if (state.parsedType === "trades") {
    html = renderTradesTable(filtered, state.headers);
  } else {
    html = renderRaw(state.rawText);
  }
  dom.resultsPanel.outerHTML = html;
  dom.resultsPanel = document.querySelector(".results, .empty-state");
}

async function handleFile(file) {
  const text = await file.text();
  const parsed = detectAndParse(text, dom.viewSelect.value);
  state.fileName = file.name;
  state.rawText = text;
  state.parsedType = parsed.type;
  state.records = parsed.records;
  state.headers = parsed.headers || [];
  state.summary = summarize(parsed);
  dom.statusText.textContent =
    parsed.type === "raw"
      ? `Loaded ${file.name}. Format not recognized, showing raw text.`
      : `Loaded ${file.name}. Parsed as ${parsed.type}.`;
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

dom.viewSelect.addEventListener("change", () => {
  if (!state.rawText) return;
  const parsed = detectAndParse(state.rawText, dom.viewSelect.value);
  state.parsedType = parsed.type;
  state.records = parsed.records;
  state.headers = parsed.headers || [];
  state.summary = summarize(parsed);
  render();
});

for (const input of filterInputs) {
  input.addEventListener("input", render);
}
