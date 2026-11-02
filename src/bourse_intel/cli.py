from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from bourse_intel.config import DATA_DIR, DB_PATH, TEHRAN, VAR_DIR
from bourse_intel.cycle import run_cycle
from bourse_intel.db import connect
from bourse_intel.report import build_report
from bourse_intel.server import render_dashboard


def cmd_cycle(args: argparse.Namespace) -> int:
    conn = connect(Path(args.db))
    summary = run_cycle(conn, enrich_limit=args.enrich_limit)
    VAR_DIR.mkdir(parents=True, exist_ok=True)
    target = VAR_DIR / "last_cycle.json"
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in summary if k != "probes"}, ensure_ascii=False, indent=2))
    print(f"probes: {len(summary['probes'])}")
    print(f"wrote {target}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    conn = connect(Path(args.db))
    body = build_report(conn, args.slot)
    out = VAR_DIR / "reports"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{args.slot}.md"
    path.write_text(body, encoding="utf-8")
    print(body)
    print(f"wrote {path}")
    return 0


def cmd_watch_symbol(args: argparse.Namespace) -> int:
    conn = connect(Path(args.db))
    row = conn.execute(
        "SELECT id, symbol, company_name FROM instruments WHERE symbol = ? ORDER BY id LIMIT 1",
        (args.symbol,),
    ).fetchone()
    if row is None:
        print(f"نماد {args.symbol} در پایگاه نیست. اول cycle را اجرا کنید.")
        return 1
    conn.execute(
        """
        INSERT INTO watchlist(instrument_id, note, added_at) VALUES(?, ?, ?)
        ON CONFLICT(instrument_id) DO UPDATE SET note = excluded.note
        """,
        (row["id"], args.note, datetime.now(ZoneInfo(TEHRAN)).isoformat()),
    )
    conn.commit()
    print(f"اضافه شد: {row['symbol']} {row['company_name']}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    conn_path = args.db

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = self.path.split("?", 1)
            query = parsed[1] if len(parsed) > 1 else ""
            conn = connect(Path(conn_path))
            try:
                body = render_dashboard(conn, query).encode("utf-8")
            finally:
                conn.close()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *log_args):
            print("%s - %s" % (self.address_string(), fmt % log_args))

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"dashboard http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


def _slot_now() -> str | None:
    now = datetime.now(ZoneInfo(TEHRAN))
    if now.hour == 8 and now.minute >= 50:
        return "preopen"
    if now.hour == 19 and now.minute < 10:
        return "evening"
    return None


def cmd_watch(args: argparse.Namespace) -> int:
    seen_slots: set[str] = set()
    while True:
        conn = connect(Path(args.db))
        summary = run_cycle(conn, enrich_limit=args.enrich_limit)
        slot = _slot_now()
        tehran_day = datetime.now(ZoneInfo(TEHRAN)).date().isoformat()
        if slot:
            key = f"{tehran_day}-{slot}"
            if key not in seen_slots:
                build_report(conn, slot)
                seen_slots.add(key)
                print(f"report {key}")
        conn.close()
        print(f"cycle new_items={summary['new_items']} instruments={summary['instruments']}")
        time.sleep(args.interval)


def main(argv: list[str] | None = None) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    parser = argparse.ArgumentParser(prog="bourse_intel")
    parser.add_argument("--db", type=str, default=str(DB_PATH))
    sub = parser.add_subparsers(dest="command", required=True)

    cycle = sub.add_parser("cycle")
    cycle.add_argument("--enrich-limit", type=int, default=80)
    cycle.set_defaults(func=cmd_cycle)

    report = sub.add_parser("report")
    report.add_argument("--slot", default="adhoc")
    report.set_defaults(func=cmd_report)

    watch_symbol = sub.add_parser("watch-symbol")
    watch_symbol.add_argument("symbol")
    watch_symbol.add_argument("--note", default="")
    watch_symbol.set_defaults(func=cmd_watch_symbol)

    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=cmd_serve)

    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=int, default=900)
    watch.add_argument("--enrich-limit", type=int, default=80)
    watch.set_defaults(func=cmd_watch)

    demo = sub.add_parser("demo")
    demo.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    if args.command != "demo" and "demo" in str(args.db):
        print("پایگاه دمو فقط با دستور demo پر می‌شود.")
        return 2
    return args.func(args)


def cmd_demo(args: argparse.Namespace) -> int:
    if not str(args.db).endswith("demo.db"):
        print("دستور demo فقط روی فایلی که اسمش demo.db است اجرا می‌شود.")
        return 2
    conn = connect(Path(args.db))
    conn.execute(
        """
        INSERT INTO sources(key, title, kind, method, url, verification_default, credibility_prior, notes)
        VALUES('demo_source', '[DEMO] منبع آزمایشی', 'demo', 'demo', 'demo://local', 'unconfirmed', 0, 'داده ساختگی')
        ON CONFLICT(key) DO NOTHING
        """
    )
    source = conn.execute("SELECT id FROM sources WHERE key = 'demo_source'").fetchone()
    conn.execute(
        """
        INSERT INTO instruments(source_key, external_id, symbol, name, company_name, market, instrument_type, industry, slug, updated_at)
        VALUES('demo', 'demo-1', '[DEMO]نماد', '[DEMO] شرکت آزمایشی', '[DEMO] شرکت آزمایشی', 'demo', 'equity', 'demo', 'demo', '2000-01-01T00:00:00+00:00')
        ON CONFLICT(source_key, external_id) DO NOTHING
        """
    )
    conn.execute(
        """
        INSERT INTO items(source_id, external_id, url, title, body, published_at, fetched_at, content_hash, verification)
        VALUES(?, 'demo-item', 'demo://item', '[DEMO] خبر آزمایشی', '[DEMO] این ردیف ساختگی است', '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00', 'demo-hash', 'unconfirmed')
        ON CONFLICT(content_hash) DO NOTHING
        """,
        (source["id"],),
    )
    conn.commit()
    print("داده DEMO در", args.db, "نوشته شد و وارد پایگاه زنده نمی‌شود.")
    return 0
