from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

SIGNALS_PATH = Path(__file__).resolve().parent.parent / "copytrade_signals.json"
CACHE_ROOT = Path(__file__).resolve().parent.parent / "_cache"
POLITICIANS_CACHE_DIR = CACHE_ROOT / "politicians"
CAPITOL_FETCH_BASE_URL = "https://preview.capitoltrades.com"
CAPITOL_SOURCE_BASE_URL = "https://www.capitoltrades.com"
RO_KHANNA_POLITICIAN_ID = "K000389"
MAX_PAGES_PER_REFRESH = 8
USER_AGENT = "Mozilla/5.0 (compatible; trading-bot/1.0)"
_BAND_RE = re.compile(r"^(?:< \d+K|\d+K[-–]\d+K|\d+K[-–]\d+M)$")
_TITLE_RE = re.compile(
    r"^(?P<politician>.+?)\s+(?P<verb>bought|sold)\s+.+\((?P<symbol>[^)]+)\)\s+on\s+(?P<traded_at>\d{4}-\d{2}-\d{2})$"
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _fetch_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "ignore")


def _trade_list_url(*, politician_id: str, page: int) -> str:
    return f"{CAPITOL_FETCH_BASE_URL}/trades?politician={politician_id}&txDate=all&page={page}"


def _trade_detail_url(trade_id: str) -> str:
    return f"{CAPITOL_FETCH_BASE_URL}/trades/{trade_id}"


def _trade_source_url(trade_id: str) -> str:
    return f"{CAPITOL_SOURCE_BASE_URL}/trades/{trade_id}"


def _fetch_trade_ids(*, politician_id: str, page: int) -> list[str]:
    html = _fetch_html(_trade_list_url(politician_id=politician_id, page=page))
    seen: set[str] = set()
    trade_ids: list[str] = []
    for match in re.finditer(r"/trades/(\d+)", html):
        trade_id = match.group(1)
        if trade_id in seen:
            continue
        seen.add(trade_id)
        trade_ids.append(trade_id)
    return trade_ids


def _normalize_symbol(raw_symbol: str) -> str:
    symbol = raw_symbol.strip()
    if ":" in symbol:
        symbol = symbol.split(":", 1)[0]
    return symbol


def _normalize_size_band(raw_band: str) -> str:
    return raw_band.replace("–", "-").strip()


def _clean_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]


def _parse_trade_detail_html(html: str, *, trade_id: str) -> dict[str, str]:
    lines = _clean_lines(html)
    header_lines = lines[: lines.index("Filing Summary")] if "Filing Summary" in lines else lines
    title_match = _TITLE_RE.match(header_lines[0])
    if title_match is None:
        raise ValueError(f"unable to parse trade title for {trade_id}")

    try:
        traded_idx = header_lines.index("Traded")
        published_idx = header_lines.index("Published")
    except ValueError as exc:
        raise ValueError(f"missing traded/published fields for {trade_id}") from exc

    size_band = next((line for line in header_lines if _BAND_RE.match(line.replace("–", "-"))), None)
    if size_band is None:
        raise ValueError(f"missing size band for {trade_id}")

    side = "buy" if title_match.group("verb") == "bought" else "sell"
    return {
        "politician": title_match.group("politician").strip(),
        "published_at": header_lines[published_idx + 1],
        "side": side,
        "size_band": _normalize_size_band(size_band),
        "source": _trade_source_url(trade_id),
        "symbol": _normalize_symbol(title_match.group("symbol")),
        "traded_at": header_lines[traded_idx + 1],
    }


def _fetch_trade_record(trade_id: str) -> dict[str, str]:
    html = _fetch_html(_trade_detail_url(trade_id))
    return _parse_trade_detail_html(html, trade_id=trade_id)


def _load_signal_rows(path: Path = SIGNALS_PATH) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _write_signal_rows(rows: list[dict[str, str]], *, path: Path = SIGNALS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent) as handle:
        json.dump(rows, handle, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def _politician_slug(name: str) -> str:
    normalized = _SLUG_RE.sub("_", name.strip().lower()).strip("_")
    return normalized or "unknown_politician"


def _politician_year_signals_path(politician_name: str, year: str) -> Path:
    return POLITICIANS_CACHE_DIR / _politician_slug(politician_name) / year / "signals.json"


def _write_json_atomic(payload: object, *, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent) as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def rebuild_politician_year_caches(*, path: Path = SIGNALS_PATH) -> dict[str, int]:
    rows = _load_signal_rows(path)
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        politician_name = str(row.get("politician") or "").strip()
        published_at = str(row.get("published_at") or "")
        if not politician_name or len(published_at) < 4:
            continue
        year = published_at[:4]
        grouped.setdefault((politician_name, year), []).append(row)

    for (politician_name, year), bucket in grouped.items():
        bucket.sort(
            key=lambda row: (
                row.get("published_at", ""),
                row.get("traded_at", ""),
                row.get("symbol", ""),
                row.get("side", ""),
                row.get("source", ""),
            )
        )
        _write_json_atomic(bucket, path=_politician_year_signals_path(politician_name, year))

    return {
        "politicians": len({politician for politician, _year in grouped}),
        "year_files": len(grouped),
    }


def _refresh_status_path_for(politician_name: str, *, path: Path) -> Path:
    if path != SIGNALS_PATH:
        return path.with_name(f"{_politician_slug(politician_name)}_refresh.json")
    return POLITICIANS_CACHE_DIR / f"{_politician_slug(politician_name)}_refresh.json"


def _write_refresh_status(payload: dict[str, object], *, status_path: Path) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def refresh_politician_signals(
    *,
    politician_id: str = RO_KHANNA_POLITICIAN_ID,
    politician_name: str = "Ro Khanna",
    path: Path = SIGNALS_PATH,
    max_pages: int = MAX_PAGES_PER_REFRESH,
) -> dict[str, object]:
    rows = _load_signal_rows(path)
    status_path = _refresh_status_path_for(politician_name, path=path)
    known_sources = {str(row.get("source")) for row in rows if row.get("source")}
    new_rows: list[dict[str, str]] = []
    pages_scanned = 0

    for page in range(max_pages):
        trade_ids = _fetch_trade_ids(politician_id=politician_id, page=page)
        pages_scanned += 1
        if not trade_ids:
            break
        unknown_ids = [trade_id for trade_id in trade_ids if _trade_source_url(trade_id) not in known_sources]
        if not unknown_ids:
            break
        for trade_id in unknown_ids:
            record = _fetch_trade_record(trade_id)
            if record["politician"] != politician_name:
                continue
            known_sources.add(record["source"])
            new_rows.append(record)

    if not new_rows:
        result = {
            "added": 0,
            "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pages_scanned": pages_scanned,
            "politician": politician_name,
            "total_rows": len(rows),
        }
        rebuild_politician_year_caches(path=path)
        _write_refresh_status(result, status_path=status_path)
        return result

    rows.extend(new_rows)
    rows.sort(
        key=lambda row: (
            row.get("published_at", ""),
            row.get("traded_at", ""),
            row.get("politician", ""),
            row.get("symbol", ""),
            row.get("side", ""),
            row.get("source", ""),
        )
    )
    _write_signal_rows(rows, path=path)
    rebuild_politician_year_caches(path=path)
    result = {
        "added": len(new_rows),
        "checked_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages_scanned": pages_scanned,
        "politician": politician_name,
        "total_rows": len(rows),
    }
    _write_refresh_status(result, status_path=status_path)
    return result
