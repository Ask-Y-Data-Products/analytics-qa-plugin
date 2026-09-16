"""Probe the real Power BI Desktop engine and its local report WebView."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
import subprocess
import socket
import re
import urllib.request

from connections import dump, digest, ensure_evidence_writable
from powerbi_inventory import semantic_summary


def status():
    home = Path.home()
    roots = [home / "Microsoft/Power BI Desktop Store App", home / "AppData/Local/Microsoft/Power BI Desktop"]
    ports = []
    for root in roots:
        for path in root.glob("AnalysisServicesWorkspaces/*/Data/msmdsrv.port.txt"):
            try:
                port = int(path.read_text(encoding="utf-16-le").strip().strip("\ufeff"))
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    pass
                ports.append({"port": port, "workspace": str(path.parent.parent)})
            except (OSError, ValueError):
                continue
    try:
        with urllib.request.urlopen("http://127.0.0.1:9333/json/list", timeout=3) as r:
            pages = json.load(r)
        pages = [{"id": p["id"], "url": p["url"], "title": p.get("title")} for p in pages if p["type"] == "page"]
    except Exception:
        pages = []
    return {"analysis_services": ports, "browser_endpoint": "http://127.0.0.1:9333" if pages else None, "pages": pages}


def load_adomd():
    import clr
    candidates = [Path(os.environ.get("QA_POWERBI_BIN", "C:/Program Files/Microsoft Power BI Desktop/bin"))]
    candidates += list(Path("C:/Program Files/WindowsApps").glob("Microsoft.MicrosoftPowerBIDesktop_*/bin"))
    package = subprocess.run(["powershell.exe", "-NoProfile", "-Command", "(Get-AppxPackage 'Microsoft.MicrosoftPowerBIDesktop').InstallLocation"], capture_output=True, text=True, timeout=20).stdout.strip()
    if package:
        candidates.append(Path(package) / "bin")
    for root in candidates:
        for name in ["Microsoft.PowerBI.AdomdClient.dll", "Microsoft.AnalysisServices.AdomdClient.dll"]:
            dll = root / name
            if dll.exists():
                sys.path.append(str(root))
                clr.AddReference(str(dll))
                from Microsoft.AnalysisServices.AdomdClient import AdomdConnection
                return AdomdConnection
    raise RuntimeError("Power BI ADOMD client not found; set QA_POWERBI_BIN")


def read_only_dax(text):
    remaining = text.lstrip()
    while remaining.startswith(("--", "//", "/*")):
        if remaining.startswith("/*"):
            end = remaining.find("*/", 2)
            if end < 0:
                raise ValueError("Unterminated leading DAX comment")
            remaining = remaining[end + 2:].lstrip()
        else:
            remaining = remaining.partition("\n")[2].lstrip()
    if not re.match(r"(?:EVALUATE|DEFINE|SELECT)\b", remaining, re.IGNORECASE):
        raise ValueError("Expected a read-only DAX/DMV query")
    return text


def dax(port, text, database=None):
    read_only_dax(text)
    connection_type = load_adomd()
    if database and any(c in database for c in ';"\r\n'):
        raise ValueError("Invalid catalog name")
    cs = f"Data Source=localhost:{port};" + (f'Initial Catalog="{database}";' if database else "")
    connection = connection_type(cs)
    connection.Open()
    try:
        command = connection.CreateCommand()
        command.CommandText = text
        command.CommandTimeout = 60
        reader = command.ExecuteReader()
        result_sets, total_rows = [], 0
        while True:
            columns = [reader.GetName(i) for i in range(reader.FieldCount)]
            if len(set(columns)) != len(columns):
                raise ValueError("Duplicate result column names would discard evidence")
            rows = []
            while reader.Read():
                total_rows += 1
                if total_rows > 5000:
                    raise RuntimeError("DAX output exceeds evidence limit; aggregate or explicitly narrow the query")
                row = {}
                for i, col in enumerate(columns):
                    if reader.IsDBNull(i):
                        row[col] = None
                    else:
                        value = reader.GetValue(i)
                        row[col] = value if isinstance(value, (int, float, str, bool)) else str(value)
                rows.append(row)
            result_sets.append({"columns": columns, "rows": rows})
            if not reader.NextResult():
                break
        reader.Close()
        return {"kind": "dax", "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "port": port,
                "database": connection.Database, "query": text, **result_sets[0], "result_sets": result_sets,
                "complete": True, "context": "engine query; not proof of applied UI state"}
    finally:
        connection.Close()


MODEL_ROWSETS = ["TMSCHEMA_MODEL", "TMSCHEMA_TABLES", "TMSCHEMA_COLUMNS", "TMSCHEMA_MEASURES",
    "TMSCHEMA_RELATIONSHIPS", "TMSCHEMA_PARTITIONS", "TMSCHEMA_EXPRESSIONS", "TMSCHEMA_HIERARCHIES",
    "TMSCHEMA_LEVELS", "TMSCHEMA_CALCULATION_GROUPS", "TMSCHEMA_CALCULATION_ITEMS", "TMSCHEMA_ROLES",
    "TMSCHEMA_TABLE_PERMISSIONS", "TMSCHEMA_VARIATIONS", "DISCOVER_CALC_DEPENDENCY"]


def model_inventory(port, database, out):
    out = Path(out)
    ensure_evidence_writable(out)
    if out.exists():
        raise ValueError("Model evidence directory exists; use a fresh directory")
    out.mkdir(parents=True)
    rowsets = {}
    for name in MODEL_ROWSETS:
        try:
            rowsets[name] = dax(port, "SELECT * FROM $SYSTEM." + name, database)
        except Exception as exc:
            rowsets[name] = {"error": str(exc), "status": "unavailable", "database": database}
        dump(out / (name + ".json"), rowsets[name])
    result = {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "port": port, "database": database,
              "summary": semantic_summary(rowsets), "rowsets": {k: {"file": k + ".json", "rows": len(v.get("rows", [])), "status": "unavailable" if "error" in v else "captured"} for k, v in rowsets.items()},
              "report_binding": "unverified: associate this catalog with the report separately"}
    dump(out / "model.json", result)
    return result


def recent_queries(port, database):
    result = dax(port, "SELECT * FROM $SYSTEM.DISCOVER_SESSIONS", database)
    queries = []
    for row in result["rows"]:
        command = row.get("SESSION_LAST_COMMAND", "") or ""
        if row.get("SESSION_CURRENT_DATABASE") == database and re.match(r"\s*(DEFINE|EVALUATE)\b", command, re.I):
            queries.append({"session_id": row.get("SESSION_ID"), "database": database, "query": command})
    return {"kind": "recent_engine_queries", "timestamp": result["timestamp"], "port": port,
            "database": database, "queries": queries,
            "note": "Best-effort last command per live session, not a complete trace or automatic visual attribution. Correlate before/after a controlled UI action; queries can be cached or superseded."}


def replay_query(capture, index, port, database):
    record = json.loads(Path(capture).read_text(encoding="utf-8-sig"))
    if index < 0 or index >= len(record.get("queries", [])):
        raise ValueError("Query index is outside the captured query list")
    selected = record["queries"][index]
    result = dax(port, selected["query"], database)
    result["replay"] = {"capture_sha256": digest(Path(capture)), "query_index": index,
                        "captured_database": selected.get("database", record.get("database")),
                        "captured_session": selected.get("session_id"),
                        "note": "Exact captured query text. Reconnecting to a different catalog requires an independently verified identity mapping."}
    return result


def check_report_title(body_text, expected_title):
    if not isinstance(expected_title, str) or not expected_title.strip():
        raise ValueError('An expected report title is required')
    prefix = [line.strip() for line in body_text.splitlines()[:20]]
    if expected_title.strip() not in prefix:
        raise ValueError('The selected WebView does not show the expected report title; rediscover targets')
    return expected_title.strip()


def browser_targets(endpoint):
    from playwright.sync_api import sync_playwright
    targets = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(endpoint)
        try:
            for context in browser.contexts:
                for page in context.pages:
                    if 'reportView' not in page.url:
                        continue
                    session = context.new_cdp_session(page)
                    try:
                        identity = session.send('Target.getTargetInfo')['targetInfo']['targetId']
                    finally:
                        session.detach()
                    targets.append({'page_id': identity, 'url': page.url,
                                    'header_lines': page.locator('body').inner_text().splitlines()[:20]})
        finally:
            browser.close()
    return {'endpoint': endpoint, 'targets': targets,
            'note': 'Match the visible report title, not list order. Engine/catalog association is a separate check.'}


def browser_capture(out, endpoint, selector=None, click_text=None, click_scope=None, page_id=None, report_title=None):
    ensure_evidence_writable(out)
    from playwright.sync_api import sync_playwright
    out = Path(out)
    if any((out / name).exists() for name in ["screen.png", "state.json", "dom.html"]):
        raise ValueError("Capture evidence already exists; use a new directory")
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(endpoint)
        pages = [page for context in browser.contexts for page in context.pages if "reportView" in page.url]
        if page_id:
            selected = []
            for candidate in pages:
                session = candidate.context.new_cdp_session(candidate)
                info = session.send("Target.getTargetInfo")["targetInfo"]
                session.detach()
                if info["targetId"] == page_id:
                    selected.append(candidate)
            pages = selected
        if len(pages) != 1:
            raise RuntimeError(f"Expected exactly one Power BI reportView page; got {len(pages)}")
        page = pages[0]
        page.set_default_timeout(15000)
        if report_title:
            check_report_title(page.locator('body').inner_text(), report_title)
        if click_text:
            scope = page.locator(click_scope) if click_scope else page
            if click_scope and scope.count() != 1:
                raise RuntimeError("Click scope must identify exactly one element")
            target = scope.get_by_text(click_text, exact=True)
            if target.count() != 1:
                raise RuntimeError("Click target is ambiguous or absent; inspect report DOM first")
            target.click()
        stable, previous = 0, None
        for _ in range(30):
            text = page.locator("body").inner_text()
            if text == previous:
                stable += 1
            else:
                stable = 0
            if stable >= 3:
                break
            previous = text
            page.wait_for_timeout(500)
        else:
            raise RuntimeError("Report text did not settle")
        target = page.locator(selector) if selector else page.locator("body")
        if target.count() != 1:
            raise RuntimeError("Capture selector must identify exactly one element")
        if selector:
            target.screenshot(path=str(out / "screen.png"))
        else:
            page.screenshot(path=str(out / "screen.png"))
        controls = page.locator('[role], [aria-label]').evaluate_all("els => els.map(e=>({tag:e.tagName,role:e.getAttribute('role'),label:e.getAttribute('aria-label'),title:e.getAttribute('title'),selected:e.getAttribute('aria-selected'),checked:e.getAttribute('aria-checked'),value:'value' in e ? e.value : null,text:e.innerText?.slice(0,250)})).slice(0,600)")
        input_values = page.locator('input, select').evaluate_all("els => els.map(e=>({tag:e.tagName,type:e.type,label:e.getAttribute('aria-label'),value:e.value,checked:e.checked ?? null}))")
        containers = page.locator(".visualContainer")
        if not containers.count():
            containers = page.locator("visual-container")
        count = containers.count()
        visuals = containers.evaluate_all("els => els.map(e=>({id:e.getAttribute('data-visual-name') || e.getAttribute('name') || e.id,label:e.getAttribute('aria-label'),text:e.innerText,rect:{x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y,width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height},selected:Array.from(e.querySelectorAll('[aria-selected=true],[aria-checked=true],.selected')).map(n=>({text:n.innerText,label:n.getAttribute('aria-label'),role:n.getAttribute('role')}))}))")
        frames = []
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                frames.append({"url": frame.url, "text": frame.locator('body').inner_text(timeout=3000)})
            except Exception as exc:
                frames.append({"url": frame.url, "observation_error": str(exc)})
        result = {"kind": "render", "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "url": page.url, "page_id": page_id, "verified_report_title": report_title, "viewport": page.evaluate("({width:innerWidth,height:innerHeight,devicePixelRatio})"), "selector": selector,
                  "action": {"click_text": click_text, "click_scope": click_scope}, "visible_text": text, "controls": controls, "input_values": input_values, "visuals": visuals, "frames": frames,
                  "screenshot": "screen.png", "settled": True, "visual_count": count,
                  "observation_status": "captured" if count else "inconclusive_no_visuals",
                  "note": "Report WebView only; native Desktop dialogs are outside this capture. Text stability is not proof chart data loaded; verify content and applied state."}
        dump(out / "state.json", result)
        (out / "dom.html").write_text(page.content(), encoding="utf-8")
        browser.close()
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    t = sub.add_parser('targets'); t.add_argument('--endpoint', default='http://127.0.0.1:9333')
    l = sub.add_parser("catalogs"); l.add_argument("--port", type=int, required=True)
    m = sub.add_parser("model"); m.add_argument("--port", type=int, required=True); m.add_argument("--database", required=True); m.add_argument("--out", required=True)
    q = sub.add_parser("queries"); q.add_argument("--port", type=int, required=True); q.add_argument("--database", required=True); q.add_argument("--out", required=True)
    r = sub.add_parser("replay"); r.add_argument("--capture", required=True); r.add_argument("--query-index", type=int, required=True); r.add_argument("--port", type=int, required=True); r.add_argument("--database", required=True); r.add_argument("--out", required=True)
    d = sub.add_parser("dax"); d.add_argument("--port", type=int, required=True); d.add_argument("--database", required=True); d.add_argument("--file", required=True); d.add_argument("--out", required=True)
    c = sub.add_parser("capture"); c.add_argument("--out", required=True); c.add_argument("--endpoint", default="http://127.0.0.1:9333"); c.add_argument("--selector"); c.add_argument("--click-text"); c.add_argument("--click-scope"); c.add_argument("--page-id"); c.add_argument('--report-title')
    a = parser.parse_args()
    try:
        if hasattr(a, "out"):
            ensure_evidence_writable(a.out)
        if a.command == "status": result = status()
        elif a.command == 'targets': result = browser_targets(a.endpoint)
        elif a.command == "catalogs": result = dax(a.port, "SELECT * FROM $SYSTEM.DBSCHEMA_CATALOGS")
        elif a.command == "model": result = model_inventory(a.port, a.database, a.out)
        elif a.command == "queries":
            if Path(a.out).exists():
                raise ValueError("Query capture exists; use a fresh path")
            result = recent_queries(a.port, a.database); dump(a.out, result)
        elif a.command == "replay":
            if Path(a.out).exists():
                raise ValueError("Replay evidence exists; use a fresh path")
            result = replay_query(a.capture, a.query_index, a.port, a.database); dump(a.out, result)
        elif a.command == "dax":
            if Path(a.out).exists():
                raise ValueError("DAX evidence already exists; use a new output name")
            result = dax(a.port, Path(a.file).read_text(encoding="utf-8-sig"), a.database); dump(a.out, result)
        else: result = browser_capture(a.out, a.endpoint, a.selector, a.click_text, a.click_scope, a.page_id, a.report_title)
        if a.command == "capture":
            result = {k: v for k, v in result.items() if k not in ["controls", "visible_text"]}
            result["state_file"] = str(Path(a.out) / "state.json")
        elif a.command == 'model':
            result = {'model_file': str(Path(a.out) / 'model.json'),
                      'port': a.port, 'database': a.database,
                      'rowsets': result['rowsets'],
                      'report_binding': result['report_binding']}
        print(json.dumps(result, default=str))
    except Exception as e:
        print(json.dumps({"error": str(e), "type": type(e).__name__}), file=sys.stderr)
        sys.exit(1)
