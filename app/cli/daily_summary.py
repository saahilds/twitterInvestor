from __future__ import annotations

import argparse
import json

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.daily_digest import DailyDigestService, render_digest_markdown


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild/finalize daily digest from DB rows only.")
    parser.add_argument("--date", help="ET calendar date YYYY-MM-DD")
    parser.add_argument("--rebuild", action="store_true", default=True)
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--send", action="store_true", help="Print webhook-ready markdown (no network)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    init_db()
    service = DailyDigestService(SessionLocal)
    if args.finalize:
        row = service.finalize(args.date)
    else:
        row = service.rebuild(args.date, force=True)
    if row is None:
        raise SystemExit("digest unavailable")
    payload = service.to_api_dict(row)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(payload.get("summary_markdown") or render_digest_markdown(payload))
    if args.send:
        print("\n# --send: use POST /digest/finalize from evening_pause or AlertService in-app")


if __name__ == "__main__":
    main()
