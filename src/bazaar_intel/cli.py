from __future__ import annotations

import argparse
import json
from pathlib import Path

from bazaar_intel.config import DEFAULT_DB
from bazaar_intel.cycle import CycleBusy, run_cycle
from bazaar_intel.db import init_db
from bazaar_intel.ledger import LedgerError, evaluate_due, register_prediction
from bazaar_intel.schedule import next_slot, run_forever
from bazaar_intel.timeutil import tehran_label


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="پایش بازار سرمایه ایران و دفتر پیش‌بینی")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")
    cycle = sub.add_parser("cycle")
    cycle.add_argument("--session", choices=("preopen", "close", "pilot", "phase2"), default="pilot")
    cycle.add_argument("--skip-probe", action="store_true")

    report = sub.add_parser("report")
    report.add_argument("--session", choices=("preopen", "close", "pilot", "phase2"), default="pilot")

    sub.add_parser("evaluate")
    sub.add_parser("schedule")
    sub.add_parser("status")
    sub.add_parser("install-service")

    predict = sub.add_parser("predict")
    predict.add_argument("--instrument-id", type=int, required=True)
    predict.add_argument("--quote-id", type=int, required=True)
    predict.add_argument("--type", dest="prediction_type", required=True)
    predict.add_argument("--statement", required=True)
    predict.add_argument("--direction", required=True)
    predict.add_argument("--expected", required=True)
    predict.add_argument("--horizon-end", required=True)
    predict.add_argument("--probability", type=float, required=True)
    predict.add_argument("--evidence", required=True)
    predict.add_argument("--invalidators", required=True)
    predict.add_argument("--confidence", required=True)
    predict.add_argument("--sources", default="")
    predict.add_argument("--target-low")
    predict.add_argument("--target-high")
    predict.add_argument("--horizon-days", type=int)
    predict.add_argument("--supersedes-id", type=int)

    args = parser.parse_args(argv)
    db_path = Path(args.db)
    if args.command == "init":
        init_db(db_path)
        print(db_path)
        return 0
    if args.command == "cycle":
        try:
            _conn, path = run_cycle(db_path, session=args.session, do_probe=not args.skip_probe)
        except CycleBusy as exc:
            print(str(exc))
            return 0
        print(path)
        return 0
    if args.command == "report":
        conn = init_db(db_path)
        from bazaar_intel.reports import build_report

        print(build_report(conn, args.session))
        conn.commit()
        return 0
    if args.command == "evaluate":
        conn = init_db(db_path)
        done = evaluate_due(conn)
        conn.commit()
        print(json.dumps(done, ensure_ascii=False))
        return 0
    if args.command == "schedule":
        when, session = next_slot()
        print(f"نوبت بعدی: {session} در {when.strftime('%Y-%m-%d %H:%M')} تهران. فرآیند تا توقف برنامه زنده می‌ماند.")

        def _fire(session_name: str) -> None:
            print(f"{tehran_label()} شروع {session_name}")
            _conn, path = run_cycle(db_path, session=session_name, do_probe=True)
            print(path)

        run_forever(_fire)
        return 0
    if args.command == "install-service":
        from bazaar_intel.service import install_service

        print(install_service())
        return 0
    if args.command == "status":
        conn = init_db(db_path)
        counts = {
            "sources": conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
            "instruments": conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0],
            "quotes": conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0],
            "news": conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0],
            "predictions": conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0],
            "results": conn.execute("SELECT COUNT(*) FROM prediction_results").fetchone()[0],
        }
        print(json.dumps(counts, ensure_ascii=False))
        return 0
    if args.command == "predict":
        conn = init_db(db_path)
        try:
            public_id = register_prediction(
                conn,
                instrument_id=args.instrument_id,
                quote_id=args.quote_id,
                prediction_type=args.prediction_type,
                statement=args.statement,
                direction=args.direction,
                expected_outcome=args.expected,
                horizon_end=args.horizon_end,
                success_probability=args.probability,
                evidence=args.evidence,
                invalidators=args.invalidators,
                confidence=args.confidence,
                source_ids=[int(item) for item in args.sources.split(",") if item],
                target_low=args.target_low,
                target_high=args.target_high,
                horizon_trading_days=args.horizon_days,
                supersedes_id=args.supersedes_id,
                created_by="cli",
            )
        except LedgerError as exc:
            print(str(exc))
            return 2
        conn.commit()
        print(public_id)
        return 0
    return 1
