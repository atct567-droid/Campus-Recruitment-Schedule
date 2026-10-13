from dataclasses import dataclass
import re

from .errors import ApiError


@dataclass(frozen=True)
class Page:
    rows: list[dict]
    total: int | None
    total_page: int | None
    layout: str


def _count(value, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not (
        isinstance(value, int) or isinstance(value, str) and re.fullmatch(r"\d+", value)
    ):
        raise ApiError("schema", f"{name} must be a nonnegative integer")
    result = int(value)
    if result < 0:
        raise ApiError("schema", f"{name} must be nonnegative")
    return result


def parse_page(payload: dict) -> Page:
    """Recognize explicit rows/data layouts; refuse unknown successful schemas."""
    data = payload.get("data")
    scopes = [payload] + ([data] if isinstance(data, dict) else [])
    counts = {}
    for name in ("total", "totalPage"):
        values = [_count(scope[name], name) for scope in scopes if name in scope]
        known = {value for value in values if value is not None}
        if len(known) > 1:
            raise ApiError("schema", f"Conflicting {name} values")
        counts[name] = next(iter(known), None)

    candidates = []
    if "rows" in payload:
        candidates.append(("rows", payload["rows"]))
    if isinstance(data, dict) and "rows" in data:
        candidates.append(("data.rows", data["rows"]))
    if isinstance(data, list):
        candidates.append(("data", data))
    if not candidates:
        # A null container is only an empty result when the server says zero.
        if "data" in payload and data is None and 0 in counts.values():
            candidates.append(("data", []))
        else:
            raise ApiError("schema", "Missing list container: expected rows, data.rows, or data list")
    if len(candidates) > 1 and any(value != candidates[0][1] for _, value in candidates):
        raise ApiError("schema", "Conflicting list containers")
    layout, rows = candidates[0]
    if rows is None and 0 in counts.values():
        rows = []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ApiError("schema", f"{layout} must be a list of objects")
    if rows and 0 in counts.values():
        raise ApiError("schema", "Nonempty rows with zero pagination metadata")
    if counts["total"] == 0 and counts["totalPage"] not in (None, 0, 1):
        raise ApiError("schema", "Zero total with multiple pages")
    return Page(rows, counts["total"], counts["totalPage"], layout)


def parse_detail(payload: dict) -> dict:
    data = payload.get("data")
    if data is None:
        raise ApiError("empty_detail", "Detail data is null or absent; no fair was verified")
    if not isinstance(data, dict):
        raise ApiError("schema", "Detail data must be an object")
    if not data:
        raise ApiError("empty_detail", "Detail data is empty; no fair was verified")
    return data
