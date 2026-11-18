"""One authoritative event metadata source: Setup/event.json."""
import json
from pathlib import Path
from datetime import datetime

def event_path():
    here = Path(__file__).resolve().parent
    for folder in (here, *here.parents):
        candidate = folder / "Setup" / "event.json"
        if candidate.is_file():
            return candidate
    return here.parent / "Setup" / "event.json"

def load_event():
    path = event_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Không đọc được cấu hình giải đấu: {path} ({exc})") from exc
    required = ("event", "date_from", "date_to", "location")
    missing = [key for key in required if not str(data.get(key) or "").strip()]
    if missing:
        raise ValueError(f"Thiếu thông tin trong {path}: {', '.join(missing)}")
    for k in ("date_from", "date_to"):
        datetime.strptime(data[k], "%d/%m/%Y")
    year = int(data.get("competition_year") or data["date_from"][-4:])
    return {
        **data,
        "event_name": data["event"],
        "event_dates": _dates(data["date_from"], data["date_to"]),
        "event_venue": data["location"],
        "competition_year": year,
    }

def _dates(start, end):
    a, b = datetime.strptime(start, "%d/%m/%Y"), datetime.strptime(end, "%d/%m/%Y")
    if a.year == b.year and a.month == b.month:
        return f"{a.day} - {b.day:02d}/{b.month:02d}/{b.year}"
    return f"{start} - {end}"
