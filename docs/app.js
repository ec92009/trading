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
  portfolio: {
    label: "Last Portfolio",
    bundledPath: "./data/recent_portfolio.json",
    bundledLabel: "committed portfolio snapshot",
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
    portfolio: emptyDataset("portfolio"),
  },
};

const dom = {
  versionBadge: document.getElementById("versionBadge"),
  reloadBundled: document.getElementById("reloadBundled"),
  applyFilters: document.getElementById("applyFilters"),
  assetFilter: document.getElementById("assetFilter"),
  textFilter: document.getElementById("textFilter"),
  limitInput: document.getElementById("limitInput"),
  limitLabel: document.getElementById("limitLabel"),
  summaryPanel: document.getElementById("summaryPanel"),
  resultsPanel: document.getElementById("resultsPanel"),
  summaryCardTemplate: document.getElementById("summaryCardTemplate"),
  tabButtons: Array.from(document.querySelectorAll(".tab-button")),
};

const filterInputs = [dom.assetFilter, dom.textFilter, dom.limitInput];

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

function parsePortfolioSnapshot(text) {
  const payload = JSON.parse(text);
  if (!payload || !Array.isArray(payload.positions)) {
    throw new Error("Not a portfolio snapshot");
  }
  return payload;
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

function recordSymbol(record, type) {
  if (type === "decisions") return String(record.symbol || "");
  if (type === "trades") return String(record.symbol || "");
  if (type === "portfolio") return String(record.symbol || "");
  if (type === "botlog") {
    const message = String(record.message || "");
    const patterns = [
      /^ORDER SYNC\s+(\S+)/,
      /^Canceled stale open order\s+(\S+)/,
      /^Canceled stale open orders:\s+(\S+)/,
      /^BUY\s+\$[0-9.,]+\s+(\S+)/,
      /^SELL\s+[0-9.]+\s+(\S+)/,
      /\[rebalance sell\s+(\S+)\]/,
      /\[stop sell\s+(\S+)\]/,
    ];
    for (const pattern of patterns) {
      const match = message.match(pattern);
      if (match) {
        return String(match[1] || "");
      }
    }
  }
  return "";
}

function assetOptionsForDataset(dataset) {
  return [...new Set(dataset.records.map((record) => recordSymbol(record, dataset.parsedType)).filter(Boolean))].sort();
}

function allAssetOptions() {
  const allSymbols = new Set();
  for (const dataset of Object.values(state.datasets)) {
    for (const symbol of assetOptionsForDataset(dataset)) {
      allSymbols.add(symbol);
    }
  }
  return [...allSymbols].sort();
}

function syncAssetFilterOptions() {
  const dataset = currentDataset();
  const options = dataset.parsedType === "botlog" ? allAssetOptions() : assetOptionsForDataset(dataset);
  const previousValue = dom.assetFilter.value;
  dom.assetFilter.innerHTML = "";
  const allOption = document.createElement("option");
  allOption.value = "";
  allOption.textContent = "All assets";
  dom.assetFilter.appendChild(allOption);
  for (const symbol of options) {
    const option = document.createElement("option");
    option.value = symbol;
    option.textContent = symbol;
    dom.assetFilter.appendChild(option);
  }
  dom.assetFilter.disabled = false;
  dom.assetFilter.value = options.includes(previousValue) ? previousValue : "";
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
  if (dataset.parsedType === "portfolio") {
    return [
      ["Positions", dataset.records.length],
      ["Allocated", formatMoney(dataset.meta?.allocated) || "—"],
      ["Cash", formatMoney(dataset.meta?.cash) || "—"],
      ["Equity", formatMoney(dataset.meta?.equity) || "—"],
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

function compactTradeHeadline(row) {
  const submitted = row.submitted_at || row.executed_at || row.filled_at || "—";
  const status = String(row.status || "unknown").replaceAll("_", " ");
  const side = String(row.side || "—").toUpperCase();
  const symbol = row.symbol || "—";
  const amount = formatMoney(row.notional) || row.notional || "—";
  const rationale = String(row.rationale || "—")
    .replace("BOT v", "BOT ")
    .replace("Khanna copy-trade ", "");
  return `${submitted} / ${status} / ${side} / ${symbol} / ${amount} / ${rationale}`;
}

function compactTradeDetails(row) {
  const submitted = row.submitted_at || "—";
  const executed = row.executed_at || row.filled_at || "";
  const filled = row.filled_at || row.executed_at || "";
  let executedPart = "Executed — later";
  let filledPart = "Filled";

  const formatElapsed = (fromValue, toValue) => {
    if (!fromValue || !toValue || fromValue === "—") {
      return null;
    }
    const fromDate = new Date(fromValue.replace(" UTC", "Z"));
    const toDate = new Date(toValue.replace(" UTC", "Z"));
    const deltaSeconds = Math.max(0, Math.round((toDate.getTime() - fromDate.getTime()) / 1000));
    if (!Number.isFinite(deltaSeconds)) {
      return null;
    }
    if (deltaSeconds === 0) {
      return "immediately";
    }
    return `in ${deltaSeconds} s.`;
  };

  if (submitted !== "—" && executed) {
    const elapsed = formatElapsed(submitted, executed);
    if (elapsed) {
      executedPart = elapsed === "immediately" ? "Executed immediately" : `Executed ${elapsed}`;
    }
  }
  const status = String(row.status || "").toLowerCase();
  let finalStatus = "Pending";
  if (status === "filled") {
    finalStatus = "Filled";
  } else if (status === "partial_fill_canceled") {
    finalStatus = "Partial fill canceled";
  } else if (status) {
    finalStatus = status.replaceAll("_", " ");
    finalStatus = finalStatus.charAt(0).toUpperCase() + finalStatus.slice(1);
  }

  if (filled) {
    const elapsed = formatElapsed(executed || submitted, filled);
    if (elapsed) {
      filledPart = elapsed === "immediately" ? `${finalStatus} immediately` : `${finalStatus} ${elapsed}`;
    } else {
      filledPart = finalStatus;
    }
  } else {
    filledPart = finalStatus;
  }
  return [`Submitted ${submitted}`, executedPart, filledPart].join(" / ");
}

function applyFilters(records, type) {
  const assetNeedle = dom.assetFilter.value.trim();
  const textNeedle = dom.textFilter.value.trim().toLowerCase();

  return records.filter((record) => {
    if (assetNeedle) {
      if (recordSymbol(record, type) !== assetNeedle) {
        return false;
      }
    }
    if (type === "decisions") {
      const haystack = JSON.stringify(record).toLowerCase();
      return !textNeedle || haystack.includes(textNeedle);
    }
    if (type === "trades") {
      const haystack = JSON.stringify(record).toLowerCase();
      return !textNeedle || haystack.includes(textNeedle);
    }
    if (type === "botlog") {
      const haystack = `${record.timestamp} ${record.logger} ${record.message}`.toLowerCase();
      return !textNeedle || haystack.includes(textNeedle);
    }
    if (type === "portfolio") {
      const haystack = JSON.stringify(record).toLowerCase();
      return !textNeedle || haystack.includes(textNeedle);
    }
    return true;
  });
}

function visibleRecords(records, type) {
  const limit = Number.parseInt(dom.limitInput.value, 10);
  const normalizedLimit = Number.isFinite(limit) && limit > 0 ? limit : 120;
  if (type === "botlog") {
    return compactBotLogRecords([...records].reverse()).slice(0, normalizedLimit);
  }
  if (type === "portfolio") {
    return [...records].slice(0, normalizedLimit);
  }
  return [...records].reverse().slice(0, normalizedLimit);
}

function formatMarketClosedWaitMessage(count) {
  return count > 1 ? `Signal changed while market was closed (${count}x)` : "Signal changed while market was closed";
}

function formatMarketClosedNextOpenMessage(nextOpen, count) {
  return count > 1 ? `Market closed (${count}x). Next open ${nextOpen}` : `Market closed. Next open ${nextOpen}`;
}

function formatMarketClosedPairMessage(nextOpen, count) {
  return count > 1
    ? `Signal changed while market was closed (${count}x). Next open ${nextOpen}`
    : `Signal changed while market was closed. Next open ${nextOpen}`;
}

function formatClosedSnapshotMessage(nextOpen, count) {
  return count > 1
    ? `Market closed (${count}x). Snapshots refreshed. Next open ${nextOpen}`
    : `Market closed. Snapshots refreshed. Next open ${nextOpen}`;
}

function formatClosedSessionMessage(nextOpen, closedCount, snapshotCount) {
  const closedPart = closedCount > 1 ? `Market closed (${closedCount}x).` : "Market closed.";
  const snapshotPart = snapshotCount > 0
    ? ` Snapshots refreshed${snapshotCount > 1 ? ` (${snapshotCount}x)` : ""}.`
    : "";
  return `${closedPart}${snapshotPart} Next open ${nextOpen}`;
}

function formatPendingSettlementMessage(pendingCount, repeatCount, tracked) {
  const base = repeatCount > 1
    ? `Waiting on ${pendingCount} pending order(s) to settle (${repeatCount}x).`
    : `Waiting on ${pendingCount} pending order(s) to settle.`;
  if (tracked) {
    return `${base} Still tracking them for later loops.`;
  }
  return base;
}

function simplifyOrderSyncMessage(message) {
  const match = message.match(
    /^ORDER SYNC\s+(\S+)\s+(\S+)\s+→\s+(\S+).*?(?:avg_price=([0-9.]+|—))?(?:\s+filled_qty=([0-9.]+|—))?.*?rationale=(.*)$/
  );
  if (!match) {
    return message;
  }
  const [, symbol, fromStatus, toStatus, avgPrice, filledQty, rationale] = match;
  const cleanStatus = String(toStatus || "").replaceAll("_", " ");
  const parts = [`${symbol}: ${String(fromStatus || "").replaceAll("_", " ")} -> ${cleanStatus}`];
  if (avgPrice && avgPrice !== "—") {
    parts.push(`avg ${formatMoney(avgPrice) || avgPrice}`);
  }
  if (filledQty && filledQty !== "—") {
    parts.push(`filled qty ${filledQty}`);
  }
  if (rationale) {
    parts.push(rationale.trim());
  }
  return parts.join(" / ");
}

function compactBotLogRecords(records) {
  const chronological = [...records].reverse();
  const compacted = [];
  for (const record of chronological) {
    const message = String(record.message || "");

    if (message.includes("Checked Capitol signals for Ro Khanna: no new trades")) {
      continue;
    }

    if (message.startsWith("ORDER SYNC ")) {
      compacted.push({
        ...record,
        message: simplifyOrderSyncMessage(message),
      });
      continue;
    }

    if (message.startsWith("Canceled stale open order ")) {
      const symbolMatch = message.match(/^Canceled stale open order\s+(\S+)/);
      const symbol = symbolMatch?.[1] || "unknown";
      const previous = compacted[compacted.length - 1];
      if (previous && previous._compactType === "canceled_orders") {
        previous._symbols.push(symbol);
        previous.timestamp = record.timestamp;
        previous.message = `Canceled stale open orders: ${previous._symbols.join(", ")}`;
        continue;
      }
      compacted.push({
        ...record,
        _compactType: "canceled_orders",
        _symbols: [symbol],
        message: `Canceled stale open orders: ${symbol}`,
      });
      continue;
    }

    if (message === "Signal state changed but market is closed. Waiting for the next session.") {
      const previous = compacted[compacted.length - 1];
      if (previous && previous._compactType === "market_closed_pair_pending") {
        previous._count += 1;
        previous.timestamp = record.timestamp;
        previous.message = formatMarketClosedWaitMessage(previous._count);
        continue;
      }
      compacted.push({
        ...record,
        _compactType: "market_closed_pair_pending",
        _count: 1,
        message: formatMarketClosedWaitMessage(1),
      });
      continue;
    }

    if (message.startsWith("Market closed. Next open ")) {
      const nextOpen = message.replace("Market closed. Next open ", "");
      const previous = compacted[compacted.length - 1];
      const mergeIntoClosedSession = (target, closedIncrement = 0, snapshotIncrement = 0) => {
        target._compactType = "market_closed_session";
        target._nextOpen = nextOpen;
        target._closedCount = (target._closedCount || 0) + closedIncrement;
        target._snapshotCount = (target._snapshotCount || 0) + snapshotIncrement;
        target.timestamp = record.timestamp;
        target.message = formatClosedSessionMessage(nextOpen, target._closedCount, target._snapshotCount);
      };
      if (previous && previous._compactType === "market_closed_pair_pending") {
        previous._compactType = "market_closed_pair";
        previous._nextOpen = nextOpen;
        previous.timestamp = record.timestamp;
        previous.message = formatMarketClosedPairMessage(nextOpen, previous._count);

        const earlier = compacted[compacted.length - 2];
        if (earlier && earlier._compactType === "market_closed_pair" && earlier._nextOpen === nextOpen) {
          earlier._count += previous._count;
          earlier.timestamp = record.timestamp;
          earlier.message = formatMarketClosedPairMessage(nextOpen, earlier._count);
          compacted.pop();
        }
        continue;
      }
      if (previous && previous._compactType === "closed_snapshot_pending") {
        mergeIntoClosedSession(previous, 1, 1);
        const earlier = compacted[compacted.length - 2];
        if (earlier && earlier._compactType === "market_closed_session" && earlier._nextOpen === nextOpen) {
          earlier._closedCount += previous._closedCount;
          earlier._snapshotCount += previous._snapshotCount;
          earlier.timestamp = record.timestamp;
          earlier.message = formatClosedSessionMessage(nextOpen, earlier._closedCount, earlier._snapshotCount);
          compacted.pop();
        }
        continue;
      }
      if (previous && previous._compactType === "market_closed_session" && previous._nextOpen === nextOpen) {
        previous._closedCount += 1;
        previous.timestamp = record.timestamp;
        previous.message = formatClosedSessionMessage(nextOpen, previous._closedCount, previous._snapshotCount || 0);
        continue;
      }
      if (previous && previous._compactType === "market_closed_next_open" && previous._nextOpen === nextOpen) {
        mergeIntoClosedSession(previous, 1, 0);
        continue;
      }
      compacted.push({
        ...record,
        _compactType: "market_closed_session",
        _closedCount: 1,
        _snapshotCount: 0,
        _nextOpen: nextOpen,
        message: formatClosedSessionMessage(nextOpen, 1, 0),
      });
      continue;
    }

    if (message.startsWith("Published updated remote snapshot files: ")) {
      const previous = compacted[compacted.length - 1];
      if (previous && previous._compactType === "market_closed_session") {
        previous._snapshotCount = (previous._snapshotCount || 0) + 1;
        previous.timestamp = record.timestamp;
        previous.message = formatClosedSessionMessage(previous._nextOpen, previous._closedCount || 0, previous._snapshotCount);
        continue;
      }
      if (previous && previous._compactType === "market_closed_next_open") {
        previous._compactType = "market_closed_session";
        previous._closedCount = previous._count || 1;
        previous._snapshotCount = 1;
        previous.timestamp = record.timestamp;
        previous.message = formatClosedSessionMessage(previous._nextOpen, previous._closedCount, previous._snapshotCount);
        continue;
      }
      compacted.push({
        ...record,
        _compactType: "closed_snapshot_pending",
        _closedCount: 0,
        _snapshotCount: 1,
        message: "Snapshots refreshed",
      });
      continue;
    }

    if (message.startsWith("Waiting on ") && message.includes(" pending order(s) to settle before the next cycle.")) {
      const match = message.match(/^Waiting on\s+(\d+)\s+pending order\(s\)/);
      const pendingCount = match?.[1] || "?";
      const previous = compacted[compacted.length - 1];
      if (
        previous
        && previous._compactType === "pending_settlement"
        && previous._pendingCount === pendingCount
      ) {
        previous._repeatCount += 1;
        previous.timestamp = record.timestamp;
        previous.message = formatPendingSettlementMessage(
          previous._pendingCount,
          previous._repeatCount,
          previous._tracked,
        );
        continue;
      }
      compacted.push({
        ...record,
        _compactType: "pending_settlement",
        _pendingCount: pendingCount,
        _repeatCount: 1,
        _tracked: false,
        message: formatPendingSettlementMessage(pendingCount, 1, false),
      });
      continue;
    }

    if (message.startsWith("Still tracking ") && message.includes(" pending order(s); will continue syncing on later loops.")) {
      const match = message.match(/^Still tracking\s+(\d+)\s+pending order\(s\)/);
      const pendingCount = match?.[1] || "?";
      const previous = compacted[compacted.length - 1];
      if (
        previous
        && previous._compactType === "pending_settlement"
        && previous._pendingCount === pendingCount
      ) {
        previous._tracked = true;
        previous.timestamp = record.timestamp;
        previous.message = formatPendingSettlementMessage(
          previous._pendingCount,
          previous._repeatCount,
          true,
        );
        continue;
      }
      compacted.push({
        ...record,
        _compactType: "pending_settlement",
        _pendingCount: pendingCount,
        _repeatCount: 0,
        _tracked: true,
        message: formatPendingSettlementMessage(pendingCount, 0, true),
      });
      continue;
    }

    compacted.push(record);
  }
  return compacted.reverse();
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
        <section class="kv-box">
          <h4>Decision state</h4>
          <div class="kv-list">
            ${statePairs.length ? statePairs.map(([key, value]) => `<div class="kv-row"><span>${escapeHtml(key)}</span><span class="mono">${escapeHtml(formatValue(value))}</span></div>`).join("") : "<div class=\"kv-row\"><span>No state</span><span>—</span></div>"}
          </div>
        </section>
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
      <p class="trade-line trade-line-primary mono">${escapeHtml(compactTradeHeadline(row))}</p>
      <p class="trade-line trade-line-secondary">${escapeHtml(compactTradeDetails(row))}</p>
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

function renderPortfolio(records, dataset) {
  if (!records.length) {
    return "<section class=\"panel empty-state\"><h2>No portfolio snapshot</h2><p>The bot has not published a holdings snapshot yet.</p></section>";
  }
  const rows = records.map((row) => `
    <tr>
      <td class="mono">${escapeHtml(row.symbol)}</td>
      <td class="mono">${escapeHtml(formatPercent(row.target_weight) || "—")}</td>
      <td class="mono">${escapeHtml(formatPercent(row.current_weight) || "—")}</td>
      <td class="mono">${escapeHtml(String(row.points ?? "—"))}</td>
      <td class="mono">${escapeHtml(formatMoney(row.current_value) || "—")}</td>
    </tr>
  `).join("");
  return `
    <section class="panel results">
      <p class="portfolio-meta">
        Snapshot ${escapeHtml(dataset.meta?.as_of || "—")} / Strategy ${escapeHtml(dataset.meta?.strategy || "—")}
      </p>
      <table class="portfolio-table">
        <thead>
          <tr>
            <th>Asset</th>
            <th>Target Weight</th>
            <th>Current Weight</th>
            <th>Points</th>
            <th>Current Balance</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </section>
  `;
}

function renderEmptyForTab(tab) {
  const label = TAB_CONFIG[tab].label;
  return `<h2>${escapeHtml(label)}</h2><p>No data loaded for this tab yet.</p>`;
}

function currentDataset() {
  return state.datasets[state.activeTab];
}

function updateStatusText(dataset) {
  return dataset;
}

function syncTabButtons() {
  for (const button of dom.tabButtons) {
    const active = button.dataset.tab === state.activeTab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  }
  dom.limitLabel.textContent = state.activeTab === "botlog" ? "Show latest UI entries" : "Show latest records";
}

function render() {
  syncTabButtons();
  const dataset = currentDataset();
  syncAssetFilterOptions();
  renderSummary(dataset.summary);
  updateStatusText(dataset);

  if (!dataset.rawText) {
    dom.resultsPanel.className = "panel empty-state";
    dom.resultsPanel.innerHTML = renderEmptyForTab(state.activeTab);
    return;
  }

  const filtered = visibleRecords(applyFilters(dataset.records, dataset.parsedType), dataset.parsedType);
  let html = "";
  if (dataset.parsedType === "decisions") {
    html = renderDecisionCards(filtered);
  } else if (dataset.parsedType === "trades") {
    html = renderTradesCards(filtered);
  } else if (dataset.parsedType === "portfolio") {
    html = renderPortfolio(filtered, dataset);
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
    meta: payload.meta || null,
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
  } else if (tab === "portfolio") {
    const parsed = parsePortfolioSnapshot(rawText);
    records = parsed.positions;
    headers = ["symbol", "qty", "current_value", "target_weight", "target_value", "current_weight"];
    setDataset(tab, {
      source: "bundled",
      sourceLabel: `Showing ${config.bundledLabel}.`,
      rawText,
      parsedType: tab,
      records,
      headers,
      meta: parsed,
    });
    return;
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
  await Promise.all(Object.keys(TAB_CONFIG).map((tab) => loadBundledTab(tab)));
  render();
}

for (const button of dom.tabButtons) {
  button.addEventListener("click", () => {
    state.activeTab = button.dataset.tab;
    render();
  });
}

dom.reloadBundled.addEventListener("click", async () => {
  await loadAllBundled();
});

dom.applyFilters.addEventListener("click", () => {
  render();
});

for (const input of filterInputs) {
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      render();
    }
  });
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
