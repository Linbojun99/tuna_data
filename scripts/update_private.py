"""One-run update for a user's scheduler. No credentials or personal paths in Git."""
import argparse
from pathlib import Path
import sys

from tuna_data import Library
from tuna_data.gfw import sync

parser = argparse.ArgumentParser()
parser.add_argument("--root", help="Data directory; defaults to user configuration")
parser.add_argument("--gfw", action="store_true", help="Also fill GFW annual partitions")
parser.add_argument("--mirror-to", help="Mounted private Drive or NAS directory")
args = parser.parse_args()
library = Library(args.root)
result = library.update()
for failure in result["errors"]:
    print(failure, file=sys.stderr)
gfw_failed = False
if args.gfw:
    try:
        sync(library=library)
    except Exception as exc:
        print(f"GFW incomplete: {exc}", file=sys.stderr)
        gfw_failed = True
if args.mirror_to:
    library.mirror(args.mirror_to)
sys.exit(1 if result["errors"] or gfw_failed else 0)
