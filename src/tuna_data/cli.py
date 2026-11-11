from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .backup import backup, restore
from .config import data_home
from .library import Library
from .sources import discover, LANDINGS


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private tuna research library: local disk, Google Drive sync, or mounted NAS")
    parser.add_argument("--root", help="Data root; overrides TUNA_DATA_HOME and config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("where")
    sub.add_parser("catalog")
    p = sub.add_parser("discover")
    p.add_argument("source", choices=list(LANDINGS) + ["all"])
    p = sub.add_parser("update")
    p.add_argument("--sources", nargs="+", choices=list(LANDINGS), default=list(LANDINGS))
    p.add_argument("--categories", nargs="+")
    p = sub.add_parser("import")
    p.add_argument("file")
    p.add_argument("--id", required=True)
    p.add_argument("--source-url")
    p = sub.add_parser("members")
    p.add_argument("dataset")
    p = sub.add_parser("export")
    p.add_argument("dataset")
    p.add_argument("destination")
    p.add_argument("--member")
    p.add_argument("--sheet", default="0")
    p = sub.add_parser("backup")
    p.add_argument("destination")
    p = sub.add_parser("restore")
    p.add_argument("snapshot")
    p = sub.add_parser("mirror")
    p.add_argument("destination")
    p = sub.add_parser("gfw")
    p.add_argument("rfmo")
    p.add_argument("start")
    p.add_argument("end")
    p.add_argument("--update", action="store_true")
    p = sub.add_parser("gfw-sync")
    p.add_argument("--rfmos", nargs="+", default=["IATTC", "ICCAT", "IOTC", "WCPFC", "CCSBT"])
    p.add_argument("--start-year", type=int, default=2012)
    p.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)
    lib = Library(args.root)
    try:
        if args.command == "where":
            print(lib.root)
        elif args.command == "catalog":
            print(lib.catalog().to_json(orient="records", force_ascii=False, indent=2))
        elif args.command == "discover":
            products = []
            for source in LANDINGS if args.source == "all" else [args.source]:
                products.extend(p.to_dict() for p in discover(source))
            print(json.dumps(products, ensure_ascii=False, indent=2))
        elif args.command == "update":
            result = lib.update(args.sources, args.categories)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result["errors"] else 0
        elif args.command == "import":
            print(lib.import_file(args.file, args.id, source_url=args.source_url))
        elif args.command == "members":
            print(json.dumps(lib.members(args.dataset), ensure_ascii=False, indent=2))
        elif args.command == "export":
            sheet = int(args.sheet) if args.sheet.isdigit() else args.sheet
            print(lib.export_table(args.dataset, args.destination, member=args.member, sheet_name=sheet))
        elif args.command == "backup":
            print(backup(lib, args.destination))
        elif args.command == "restore":
            print(restore(args.snapshot, lib.root).root)
        elif args.command == "mirror":
            print(lib.mirror(args.destination).root)
        elif args.command == "gfw":
            from .gfw import report
            print(report(args.rfmo, args.start, args.end, library=lib, update=args.update))
        elif args.command == "gfw-sync":
            from .gfw import sync
            sync(library=lib, rfmos=args.rfmos, start_year=args.start_year, refresh=args.refresh)
    except (Exception,) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0
