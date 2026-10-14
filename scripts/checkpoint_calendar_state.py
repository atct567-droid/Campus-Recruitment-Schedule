"""Public recruitment cache with opaque managed-event mappings, never credentials."""
import argparse
from pathlib import Path

from campus_recruitment.storage import Store


def checkpoint(database, destination):
    if not Path(database).exists():
        return False
    with Store(database) as store:
        store.backup(destination, public=True, include_calendar_map=True)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("work/run.db"))
    parser.add_argument("--destination", type=Path, default=Path("state-cache/real.db"))
    args = parser.parse_args()
    checkpoint(args.db, args.destination)
