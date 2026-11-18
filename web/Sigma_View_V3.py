
# Access shared modules while retaining project-root data paths.
from pathlib import Path as _ProjectPath
import sys as _project_sys
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parent.parent
_project_sys.path.insert(0, str(_PROJECT_ROOT / "shared modules"))

from flask import Flask, jsonify, render_template_string, request, send_from_directory, session, g, abort, redirect, url_for
from supabase import create_client, Client
import json
import os
import re
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from functools import wraps
from secrets import token_urlsafe
from dotenv import load_dotenv

# ================== CONFIG ==================
# Load .env beside this script, NOT the process working directory.
load_dotenv(os.path.join(str(_PROJECT_ROOT), '.env'))


def _env_first(*names):
    """Use the first configured nonempty variable; never expose key values."""
    return next((os.environ[n].strip() for n in names if os.environ.get(n, '').strip()), '')


def _new_client(label, url, key):
    """Keep Poomsae and Kyeorugi clients independent; give safe diagnostics."""
    from urllib.parse import urlparse
    if not url or not key:
        print(f'[{label}] Missing Supabase URL or key in .env')
        return None
    parsed = urlparse(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or not re.fullmatch(r'[a-z0-9-]+\.supabase\.co', host)
            or any(word in host for word in ('your-', 'example', 'placeholder'))):
        print(f'[{label}] Invalid Supabase URL hostname: {host or "(empty)"}. '
              'Copy Project URL from Supabase Dashboard > Project Settings > API.')
        return None
    try:
        return create_client(url, key)
    except Exception as exc:
        print(f'[{label}] Cannot initialize client for {host}: {type(exc).__name__}')
        return None


# Prefer dedicated Poomsae credentials. Existing Kyeorugi desktop variables are
# supported without duplicating sensitive values in .env.
_poomsae_url = _env_first('POOMSAE_SUPABASE_URL', 'SUPABASE_URL')
_poomsae_key = _env_first('POOMSAE_SUPABASE_KEY', 'SUPABASE_KEY', 'SUPABASE_PUBLIC_KEY')
_kyeorugi_url = _env_first('KYEORUGI_SUPABASE_URL', 'SUPABASE_URL')
_kyeorugi_key = _env_first('KYEORUGI_SUPABASE_SERVICE_ROLE_KEY', 'SUPABASE_SECRET_KEY')
supabase = _new_client('POOMSAE', _poomsae_url, _poomsae_key)
kyeorugi_db = _new_client('KYEORUGI', _kyeorugi_url, _kyeorugi_key)
POOMSAE_MATCH_TABLE = "Poomsae Matchs"
RESULT_TABLE = "Result"  # đổi nếu bảng Supabase của Ken tên khác, ví dụ: match_result/results
# Bảng danh sách VĐV/đoàn trong Supabase
# Sam để fallback vì có nơi đặt là "Information list", có nơi gõ thiếu r là "Infomation list"
SIGMA_EVENTS_TABLE = "sigma_events"
ACTIVE_EVENT_CODE = os.environ.get("ACTIVE_EVENT_CODE", "").strip()
SIGMA_DIVISIONS_TABLE = "sigma_divisions"

INFORMATION_TABLE_CANDIDATES = [
    "Information Lists",
    "Information lists",
    "Information_Lists",
    "Information_lists",
]

JSON_FILE = os.path.join(
    str(_PROJECT_ROOT),
    "sigma",
    "Open Taekwondo Championships 2026.json"
)

LOGO_FILE = "icon/NTP.png"            # đặt file vào thư mục static/NTP.png
# ============================================

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or token_urlsafe(40)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Strict',
    SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', '0') == '1',
)


def make_event_code(name):
    text = str(name or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text or "active_event"

MONTH_NAMES = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}


def parse_event_date(value):
    text = str(value or "").strip()
    if not text:
        return None

    formats = [
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%d/%m/%y",
        "%d-%m-%y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            pass

    return None


def format_event_date_range(date_from, date_to):
    start = parse_event_date(date_from)
    end = parse_event_date(date_to)

    # Nếu không parse được thì fallback dữ liệu gốc
    if not start and not end:
        return str(date_from or "").strip()

    if start and not end:
        return f"{start.day} {MONTH_NAMES[start.month]} {start.year}"

    if end and not start:
        return f"{end.day} {MONTH_NAMES[end.month]} {end.year}"

    # Cùng ngày
    if start.date() == end.date():
        return f"{start.day} {MONTH_NAMES[start.month]} {start.year}"

    # Cùng tháng, cùng năm: 22–24 May 2026
    if start.month == end.month and start.year == end.year:
        return f"{start.day}–{end.day} {MONTH_NAMES[start.month]} {start.year}"

    # Khác tháng, cùng năm: 22 April – 24 May 2026
    if start.year == end.year:
        return (
            f"{start.day} {MONTH_NAMES[start.month]} – "
            f"{end.day} {MONTH_NAMES[end.month]} {end.year}"
        )

    # Khác năm: 22 December 2026 – 5 January 2027
    return (
        f"{start.day} {MONTH_NAMES[start.month]} {start.year} – "
        f"{end.day} {MONTH_NAMES[end.month]} {end.year}"
    )

def read_local_json(path, default=None):
    try:
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"Read local JSON error {path}:", e)

    return default if default is not None else {}


def _request_event_code():
    """Trust only the event slug resolved on the server, never a request body/URL query."""
    code = getattr(g, 'event_code', None)
    if not code:
        raise ValueError('Missing verified Event Code; use /{event_slug}/...')
    return str(code)


def _scoped_query(db, table, fields='*'):
    return db.table(table).select(fields).eq('event_code', _request_event_code())


def load_active_sigma_event():
    selected = getattr(g, 'event_code', None) if request else None
    if selected:
        try:
            result = (supabase.table(SIGMA_EVENTS_TABLE).select('*')
                      .eq('event_code', selected).limit(1).execute())
            return (result.data or [None])[0]
        except Exception as exc:
            app.logger.warning('Event lookup: %s', type(exc).__name__)
            return None
    if supabase is None:
        return None
    try:
        query = supabase.table(SIGMA_EVENTS_TABLE).select("*")

        if ACTIVE_EVENT_CODE:
            res = query.eq("event_code", ACTIVE_EVENT_CODE).limit(1).execute()
        else:
            res = (
                query
                .eq("is_active", True)
                .order("updated_at", desc=True)
                .limit(1)
                .execute()
            )

        rows = res.data or []
        if rows:
            return rows[0]

    except Exception as e:
        print("Load active sigma_events error:", e)

    return None

def load_sigma_divisions_from_supabase(event_code):
    if supabase is None:
        return {}
    try:
        if not event_code:
            return {}

        res = (
            supabase
            .table(SIGMA_DIVISIONS_TABLE)
            .select("division_key, athletes")
            .eq("event_code", event_code)
            .execute()
        )

        rows = res.data or []
        divisions = {}

        for r in rows:
            key = str(r.get("division_key") or "").strip()
            athletes = r.get("athletes") or []

            if key:
                divisions[key] = athletes

        return divisions

    except Exception as e:
        print("Load sigma_divisions error:", e)
        return {}

from event_config import load_event


def _load_legacy_event_optional():
    """Optional legacy Setup/event.json; Supabase is the source for VPS events.

    On a clean GitHub/VPS deployment, Setup/event.json may legitimately not
    exist. An absent legacy file must not cause a public event page to 500.
    Other errors (invalid JSON, permissions) should remain visible in logs.
    """
    try:
        return load_event()
    except RuntimeError as exc:
        # event_config.load_event wraps FileNotFoundError in RuntimeError.
        # Swallow ONLY that missing-file case, not corrupt JSON or permissions.
        if isinstance(exc.__cause__, FileNotFoundError):
            app.logger.info('Legacy Setup/event.json is absent; using Supabase event setup')
            return {}
        raise
    except FileNotFoundError:
        app.logger.info('Legacy Setup/event.json is absent; using Supabase event setup')
        return {}


def load_setup():
    # Event-scoped configuration from sigma_events.setup, with legacy fallback ONLY
    # for the original event. This fallback must not leak across events.
    row = load_active_sigma_event()
    if row:
        setup = row.get('setup') if isinstance(row.get('setup'), dict) else {}
        legacy = _load_legacy_event_optional() if row.get('event_code') == get_legacy_event_code() else {}
        result = dict(legacy)
        result.update({k:v for k,v in setup.items() if v not in (None, '')})
        result['event'] = (setup.get('event') or setup.get('event_name')
                           or row.get('event_name') or result.get('event') or '')
        return result
    return _load_legacy_event_optional()


def load_event_json():
    # Event metadata from Setup/event.json; draw/athletes stay in existing sources.
    metadata = load_setup()
    row = load_active_sigma_event()
    if row:
        event_code = str(row.get('event_code') or '')
        divisions = load_sigma_divisions_from_supabase(event_code)
        if divisions:
            return {'event_name': metadata.get('event', ''), 'divisions': divisions}
        if event_code != get_legacy_event_code():
            return {'event_name': metadata.get('event', ''), 'divisions': {}}
    data = read_local_json(JSON_FILE, {})
    return {'event_name': metadata.get('event',''), 'divisions': data.get('divisions', {})}


# Favicon: reuse the same original NTP logo used in the portal header.
# Browser requests to /favicon.ico are redirected to the existing logo endpoint.
@app.get('/favicon.ico')
def ntp_favicon():
    return redirect('/portal-logo?v=20261118', code=302)


@app.route("/icon/<path:filename>")
def icon_files(filename):
    return send_from_directory(
        os.path.join(str(_PROJECT_ROOT), "icon"),
        filename
    )

TYPE_ALIASES = {
    "recognized": "Recognized",
    "recogmized": "Recognized",
    "recog": "Recognized",
    "freestyle": "Freestyle",
    "free": "Freestyle",
    "demonstration": "Demonstration",
    "demonstartion": "Demonstration",
    "demon": "Demonstration",
    "demo": "Demonstration",
    "mixer": "Mixer",
    "mixed": "Mixer",
}

DIVISION_WORDS = ["Individual", "Pair", "Team"]

def normalize_type(raw: str) -> str:
    s = str(raw or "").strip()
    low = s.lower()
    for k, v in TYPE_ALIASES.items():
        if k in low:
            return v
    return s.title() if s else "Other"

def parse_division_key(key: str):
    """Parse: Recognized - Individual Male Cadet -> type/division/age/gender"""
    key = str(key or "").strip()
    if " - " in key:
        raw_type, rest = key.split(" - ", 1)
    else:
        raw_type, rest = "Other", key

    typ = normalize_type(raw_type)
    division = "Other"
    for d in DIVISION_WORDS:
        if re.search(rf"\b{d}\b", rest, re.I):
            division = d
            break

    gender = ""
    if re.search(r"\bMale\b", rest, re.I):
        gender = "Male"
    elif re.search(r"\bFemale\b", rest, re.I):
        gender = "Female"

    age = rest
    age = re.sub(r"\b(Individual|Pair|Team|Male|Female)\b", "", age, flags=re.I)
    age = re.sub(r"\s+", " ", age).strip(" -")
    return {"key": key, "type": typ, "division": division, "age": age or "Open", "gender": gender}


def build_menu():
    data = load_event_json()

    menu = defaultdict(
        lambda: defaultdict(
            lambda: defaultdict(
                lambda: defaultdict(list)
            )
        )
    )
    flat = []

    for key, athletes in data.get("divisions", {}).items():
        meta = parse_division_key(key)

        athlete_rows = athletes or []

        # Số dòng thật trong file Sigma
        meta["count"] = len(athlete_rows)

        # Số VĐV/đội thi thật dựa theo No.
        # Individual: mỗi No. là 1 VĐV
        # Pair/Team: nhiều dòng chung 1 No. nhưng chỉ tính là 1 đội
        competitor_nos = set()

        for a in athlete_rows:
            no = str(a.get("No.", "") or "").strip()
            if no:
                competitor_nos.add(no)

        meta["competitor_count"] = len(competitor_nos) if competitor_nos else len(athlete_rows)

        flat.append(meta)

        gender = meta.get("gender") or "Mixed"

        menu[
            meta["type"]
        ][
            meta["division"]
        ][
            gender
        ][
            meta["age"]
        ].append(meta)

    # Only Kyeorugi Matchs controls which Kyeorugi divisions exist.
    try:
        for item in _kyeorugi_divisions():
            meta = {**item, 'type': 'Kyeorugi',
                    'division': item['group'],
                    'competitor_count': item['count']}
            flat.append(meta)
            # Kyeorugi uses its own sidebar tree from `flat`, not Poomsae hierarchy.
    except Exception as exc:
        print('[KYEORUGI MENU]', exc)
    return data.get("event_name", "Event"), convert_dict(menu), flat

def safe_float(v):
    try:
        if v is None or v == "": return None
        return float(v)
    except Exception:
        return None

def get_information_list_rows():
    """Read the ONE shared Information Lists table, with explicit failure."""
    errors = []
    clients = [('Poomsae', supabase), ('Kyeorugi', kyeorugi_db)]
    available = [(label, db) for label, db in clients if db is not None]
    if not available:
        raise RuntimeError('Chưa cấu hình kết nối Supabase cho Information Lists.')
    empty_success = False
    for label, db in available:
        try:
            rows, offset = [], 0
            while True:
                batch = (_scoped_query(db, 'Information Lists')
                         .range(offset, offset + 499).execute().data or [])
                rows.extend(batch)
                if len(batch) < 500:
                    break
                offset += 500
            if rows:
                return rows
            empty_success = True
        except Exception as exc:
            errors.append(f'{label}: {type(exc).__name__}: {exc}')
            print('[INFORMATION LIST]', label, type(exc).__name__)
    if empty_success:
        return []
    raise RuntimeError('Không thể đọc bảng Information Lists. ' + '; '.join(errors))


def get_results_for_division(division_key: str):
    """
    Bảng Result hiện tại không có cột Division, nên không lọc theo Division ở Supabase.
    Trả toàn bộ Result để JS tự đối chiếu theo Match/Result và không làm văng web.
    """
    try:
        rows = []
        for offset in range(0, 50000, 500):
            batch = (_scoped_query(supabase, RESULT_TABLE)
                     .range(offset, offset + 499).execute().data or [])
            rows.extend(batch)
            if len(batch) < 500:
                break
        for r in rows:
            r["_score"] = safe_float(
                r.get("Total")
                or r.get("total")
                or r.get("Score")
                or r.get("score")
            )
        return rows
    except Exception as e:
        print("Result load error:", e)
        return []

# ================== KYEORUGI / WEIGH-IN ==================
K_INFO = 'Information Lists'
K_REFEREES = 'Kyeorugi Referee Lists'
K_ROLES = ('Weigh-in Official', 'Inspector', 'Center Referee', 'Judge J1',
           'Judge J2', 'Judge J3', 'Review Jury', 'Recorder', 'Technical Assistant')
K_WEIGHTS = ('Gen. Weigh-in 1', 'Gen. Weigh-in 2', 'Random Weigh-in')


def _kyeorugi_client():
    if kyeorugi_db is None:
        raise RuntimeError('Chưa có kết nối Kyeorugi: kiểm tra URL Supabase hợp lệ và secret key trong .env; xem Terminal để biết hostname bị lỗi.')
    return kyeorugi_db


def _pages(table, *, columns='*'):
    db = _kyeorugi_client()
    rows = []
    start = 0
    while True:
        block = _scoped_query(db, table, columns).range(start, start + 499).execute().data or []
        rows.extend(block)
        if len(block) < 500:
            break
        start += 500
    return rows


def _kyeorugi_event_meta(event):
    """Accept Kyeorugi age/weight labels, including bare -38kg and -38.+38."""
    text = str(event or '').strip()
    low = text.lower()
    if not text or any(x in low for x in ('poomsae', 'recognized', 'freestyle', 'demonstration')):
        return None
    weights = re.findall(r'([+-])\s*(\d{1,3})(?:\s*kg)?', text, re.I)
    team = re.search(r'\bteam\s*([345])\b', text, re.I)
    mixed = bool(re.search(r'\bmixed\s*team\b', text, re.I))
    if not weights and not team and not mixed:
        return None
    division = ('Mixed Team' if mixed else f'Team {team.group(1)}' if team else 'Individual')
    # Check Female first, because 'female' contains 'male'.
    gender = ('Female' if re.search(r'\bfemale\b', low) else
              'Male' if re.search(r'\bmale\b', low) else 'Mixed' if mixed else '')
    age_match = re.search(r'\b(U9|U12|U14|U17|O18|Cadet|Junior|Senior)\b', text, re.I)
    age = age_match.group(1) if age_match else 'Open'
    age = {'u14':'Cadet (U14)', 'cadet':'Cadet (U14)',
           'u17':'Junior (U17)', 'junior':'Junior (U17)',
           'o18':'Senior (O18)', 'senior':'Senior (O18)'}.get(age.lower(), age.upper() if age.lower().startswith('u') else age)
    return {'division': division, 'gender': gender, 'age': age,
            'weight': f'{weights[0][0]}{weights[0][1]} kg' if division == 'Individual' and weights else ''}


def _kyeorugi_events(row):
    found = []
    for key, value in row.items():
        if re.fullmatch(r'Event_\d+', str(key), re.I) and value:
            meta = _kyeorugi_event_meta(value)
            if not meta:
                continue
            if meta['division'] == 'Individual':
                for sign, number in re.findall(r'([+-])\s*(\d{1,3})(?:\s*kg)?', str(value), re.I):
                    one = {**meta, 'weight': f'{sign}{number} kg'}
                    if one not in found:
                        found.append(one)
            elif meta not in found:
                found.append(meta)
    return found


def _kyeorugi_rows():
    # Explicit Position check to prevent officials/coaches being weighed.
    data = []
    for row in _pages(K_INFO):
        position = str(row.get('Position') or 'Athlete').lower()
        if any(x in position for x in ('coach', 'referee', 'official', 'manager', 'hlv')):
            continue
        events = _kyeorugi_events(row)
        if events:
            row['_kyeorugi_events'] = events
            data.append(row)
    return data


def _schedule_division_meta(label):
    """Parse Kyeorugi Matchs.Division; never infer a division from athlete events."""
    raw = re.sub(r'\s+', ' ', str(label or '').strip())
    if not raw:
        return None
    low = raw.lower()
    if re.search(r'\b(mixed\s*team|team\s*mixed)\b', low):
        group, team_kind = 'Team', 'Mixed Team'
    else:
        t = re.search(r'\bteam\s*([345])\b', low)
        if t:
            group, team_kind = 'Team', 'Team ' + t.group(1)
        elif re.search(r'\bindividuals?\b', low):
            group, team_kind = 'Individuals', ''
        else:
            return None
    gender = ('Female' if re.search(r'\bfemale\b', low) else
              'Male' if re.search(r'\bmale\b', low) else None)
    # A scheduled division without a gender remains hidden until its classification
    # is specified, rather than incorrectly merging male and female brackets.
    if gender is None:
        return None
    match = re.search(r'\b(U9|U12|U14|U17|O18|Cadet|Junior|Senior)\b', raw, re.I)
    if match is None:
        return None
    age_code = match.group(1).lower()
    age = {'u9': 'U9', 'u12': 'U12', 'u14': 'Cadet (U14)',
           'cadet': 'Cadet (U14)', 'u17': 'Junior (U17)',
           'junior': 'Junior (U17)', 'o18': 'Senior (O18)',
           'senior': 'Senior (O18)'}[age_code]
    weights = re.findall(r'([+-])\s*(\d{1,3})(?:\s*kg)?', raw, re.I)
    if group == 'Individuals' and not weights:
        return None
    weight = (f'{weights[-1][0]}{int(weights[-1][1])}kg' if group == 'Individuals' else '')
    return {'group': group, 'team_kind': team_kind, 'gender': gender,
            'age': age, 'weight': weight}


def _kyeorugi_divisions():
    """Only divisions actually scheduled in Kyeorugi Kyeorugi Matchs."""
    groups = {}
    for row in _pages('Kyeorugi Matchs'):
        raw = str(row.get('Division') or '').strip()
        meta = _schedule_division_meta(raw)
        if not meta:
            continue
        # Case-insensitive duplicate labels are counted together.
        key = raw.casefold()
        if key not in groups:
            groups[key] = {**meta, 'key': 'Kyeorugi - ' + raw,
                           'schedule_division': raw, 'count': 0}
        groups[key]['count'] += 1
    return list(groups.values())


def _api_error(err, status=400):
    return jsonify({'error': str(err)}), status


# ================== SINGLE-FILE TIMELINE WEB ==================
# Read-only. Both match tables are scoped to the verified event in the route.
# Match number: day digit + court digit (0 means court 10) + 3-digit sequence.
_TIMELINE_MATCH_TABLES = (("Poomsae", POOMSAE_MATCH_TABLE), ("Kyorugi", "Kyeorugi Matchs"))
_TIMELINE_PUBLIC_FIELDS = (
    'Match','Phase','Division','Poomsae Type','Method','Classification','Style',
    'Action','Status','Result','NOC Blue','NOC Red','Athlete Blue','Athlete Red',
    'Blue Tech 1','Blue Pre 1','Blue Point 1','Blue Tech 2','Blue Pre 2','Blue Point 2',
    'Red Tech 1','Red Pre 1','Red Point 1','Red Tech 2','Red Pre 2','Red Point 2',
    'Total Tech Blue','Total Pre Blue','Total Point Blue',
    'Total Tech Red','Total Pre Red','Total Point Red',
    'Scr.R1','Scr.R2','Scr.R3','Scr.RG',
    'Blue R1','Blue R2','Blue R3','Red R1','Red R2','Red R3',
    'Blue Round 1','Blue Round 2','Blue Round 3',
    'Red Round 1','Red Round 2','Red Round 3',
    'Blue Score R1','Blue Score R2','Blue Score R3',
    'Red Score R1','Red Score R2','Red Score R3',
)

def _timeline_decode_match(raw):
    value = str(raw or '').strip()
    if not re.fullmatch(r'[1-9][0-9][0-9]{3}', value) or int(value[2:]) == 0:
        return None
    return int(value[0]), int(value[1]) or 10, int(value[2:])


def _timeline_day(setup):
    start = parse_event_date(setup.get('date_from') or setup.get('start_date'))
    end = parse_event_date(setup.get('date_to') or setup.get('end_date'))
    if start is None:
        raise ValueError('Event chưa thiết lập date_from hợp lệ.')
    if end is not None and end.date() < start.date():
        raise ValueError('Ngày kết thúc giải không hợp lệ.')
    today = datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date()
    duration = max(1, (end.date() - start.date()).days + 1) if end else 1
    # Day is automatic; for dates outside the event keep an explicit preview state.
    elapsed = (today - start.date()).days
    day = min(max(elapsed + 1, 1), min(duration, 9))
    state = ('before' if elapsed < 0 else 'after' if elapsed >= duration else 'live')
    return day, state, start.date().isoformat(), end.date().isoformat() if end else start.date().isoformat()


def _timeline_fetch(db, table):
    rows, offset = [], 0
    while True:
        batch = (_scoped_query(db, table).range(offset, offset + 499).execute().data or [])
        rows.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
    return rows


def _timeline_status(row):
    result = str(row.get('Result') or '').strip()
    state = str(row.get('Action') or row.get('Status') or '').strip().casefold()
    if result or state in ('finish','finished','completed','done'):
        return 'finished'
    if state in ('start','running','playing','ready'):
        return 'running'
    return 'waiting'


# Public Division Schedule. Same event-scoped published data as Referee portal.
# No admin credentials, no cross-app HTTP dependency.
@app.get('/api/division-timelines')
def sigma_division_timelines_api():
    try:
        event_code = _request_event_code()
        db = kyeorugi_db or supabase
        if db is None:
            return jsonify({'error': 'Chưa kết nối Supabase.'}), 503
        managed = (db.table('division_timeline_admin')
                   .select('day_count,slots,revision')
                   .eq('event_code', event_code).limit(1).execute().data or [])
        if managed:
            doc = managed[0]
            # Derive the visible court extent from actual scheduled matches,
            # falling back to explicitly published Court assignments.
            by_day = {}
            for disc, table in (('Kyorugi', 'Kyeorugi Matchs'), ('Poomsae', POOMSAE_MATCH_TABLE)):
                source = kyeorugi_db if disc == 'Kyorugi' else supabase
                if source is None:
                    continue
                try:
                    offset = 0
                    while offset < 100000:
                        batch = (source.table(table).select('Match,Day,Court')
                                 .eq('event_code', event_code)
                                 .range(offset, offset + 499).execute().data or [])
                        for match in batch:
                            code = str(match.get('Match') or '').strip()
                            if re.fullmatch(r'[1-9][0-9][0-9]{3}', code):
                                day, court = int(code[0]), int(code[1]) or 10
                            else:
                                try:
                                    day, court = int(match.get('Day') or 0), int(match.get('Court') or 0)
                                except (ValueError, TypeError):
                                    continue
                            if 1 <= day <= 31 and 1 <= court <= 10:
                                key = str(day)
                                by_day[key] = max(by_day.get(key, 0), court)
                        if len(batch) < 500:
                            break
                        offset += 500
                except Exception as exc:
                    app.logger.warning('Sigma schedule court count %s: %s', disc, type(exc).__name__)
            for slot in doc.get('slots') or []:
                try:
                    day = str(int(slot.get('day')))
                    courts = [int(k) for k in (slot.get('assignments') or {}).keys()]
                    if courts:
                        by_day[day] = max(by_day.get(day, 0), *[c for c in courts if 1 <= c <= 10])
                except (ValueError, TypeError, AttributeError):
                    continue
            return jsonify({'mode': 'managed', 'days': doc.get('day_count') or 1,
                            'slots': doc.get('slots') or [],
                            'court_max_by_day': by_day, 'revision': doc.get('revision') or 0,
                            'event_code': event_code})
        # Referee's legacy public schedule fallback, until an admin publishes.
        event = load_active_sigma_event() or {}
        setup = event.get('setup') if isinstance(event.get('setup'), dict) else {}
        first = parse_event_date(setup.get('date_from') or setup.get('start_date'))
        last = parse_event_date(setup.get('date_to') or setup.get('end_date'))
        days = max(1, min(31, (last-first).days + 1)) if first and last and last >= first else 1
        rows, offset = [], 0
        while offset < 100000:
            batch = (db.table('division_timelines')
                     .select('day_number,session,court,start_slot,slot_span,division')
                     .eq('event_code', event_code)
                     .order('day_number').order('session').order('court').order('start_slot')
                     .range(offset,offset+499).execute().data or [])
            rows.extend(batch)
            if len(batch) < 500:
                break
            offset += 500
        if rows:
            days = min(31, max(days, max(int(r.get('day_number') or 1) for r in rows)))
        return jsonify({'mode':'legacy', 'days':days, 'items':rows,
                        'event_code':event_code, 'revision':0})
    except Exception as exc:
        app.logger.warning('Sigma Schedule Division read error: %s', type(exc).__name__)
        return jsonify({'error':'Không đọc được Schedule Division từ Supabase; kiểm tra bảng division_timeline_admin và quyền SELECT.'}), 503


@app.get('/api/timeline/matches')
def api_timeline_matches():
    try:
        code = _request_event_code()
        event = load_active_sigma_event()
        if not event or str(event.get('event_code') or '') != code:
            return jsonify({'error':'Không xác định được Event đang mở.'}), 404
        setup = event.get('setup') if isinstance(event.get('setup'), dict) else {}
        setup = {**setup, 'date_from':setup.get('date_from') or event.get('date_from'),
                 'date_to':setup.get('date_to') or event.get('date_to')}
        day, state, start_date, end_date = _timeline_day(setup)
        matches, warnings = [], []
        for discipline, table in _TIMELINE_MATCH_TABLES:
            db = supabase if discipline == 'Poomsae' else kyeorugi_db
            if db is None:
                warnings.append(f'{discipline}: chưa cấu hình kết nối Supabase.')
                continue
            try:
                for raw in _timeline_fetch(db, table):
                    parsed = _timeline_decode_match(raw.get('Match'))
                    if parsed is None or parsed[0] != day:
                        continue
                    _, court, number = parsed
                    entry = {key:raw.get(key) for key in _TIMELINE_PUBLIC_FIELDS if key in raw}
                    entry.update({'discipline':discipline,'court':court,'sequence':number,
                                  'state':_timeline_status(raw)})
                    matches.append(entry)
            except Exception as exc:
                app.logger.warning('Timeline %s event=%s lookup failed: %s', discipline, code, type(exc).__name__)
                warnings.append(f'Không đọc được {table}; kiểm tra cột event_code và quyền SELECT.')
        matches.sort(key=lambda r:(r['court'],r['sequence'],r['discipline']))
        max_court = max((r['court'] for r in matches),default=0)
        collisions = {}
        for row in matches:
            collisions.setdefault(str(row.get('Match')),set()).add(row['discipline'])
        conflicts = sorted(k for k,types in collisions.items() if len(types)>1)
        return jsonify({'event_code':code,'day':day,'state':state,
                        'date_from':start_date,'date_to':end_date,'max_court':max_court,
                        'matches':matches,'warnings':warnings,'conflicts':conflicts})
    except ValueError as exc:
        return jsonify({'error':str(exc)}), 422
    except Exception as exc:
        app.logger.error('Timeline API failed: %s', type(exc).__name__)
        return jsonify({'error':'Không thể tải Match Timeline. Kiểm tra Terminal server.'}), 503

INDEX_HTML = r"""
<!doctype html>
<html lang="vi">
<head>
<link rel="icon" type="image/png" sizes="any" href="/portal-logo?v=20261118" />
<link rel="apple-touch-icon" href="/portal-logo?v=20261118" />
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ event_name }}</title>
  <style>

*{box-sizing:border-box}
body{margin:0;font-family:Arial,Helvetica,sans-serif;background:#07111f;color:#eef6ff}
.topbar{
    min-height:126px;
    height:auto;
    display:grid;
    grid-template-columns:180px minmax(0,1fr) 180px;
    align-items:center;
    padding:10px 24px;
    background:linear-gradient(90deg,#03152d,#064b93,#03152d);
    border-bottom:4px solid #f5c542;
}

.logoBox img{
    max-height:100px;
    max-width:150px;
    object-fit:contain;
    background:white;
}
/* Keep the event logo visible on the left even if an image is temporarily missing. */
.logoBox{display:flex;align-items:center;justify-content:flex-start;min-width:0}
.site-logo-fallback{width:100px;height:74px;align-items:center;justify-content:center;background:#fff;color:#142d41;font:bold italic 31px Arial,sans-serif;border-radius:3px}

.eventTitle{
    text-align:center;
    min-width:0;
    overflow:hidden;
    padding:0 10px;
}

.eventTitle h1{
    margin:0 0 6px;
    font-size:clamp(20px, 3vw, 34px);
    line-height:1.12;
    text-transform:uppercase;
    letter-spacing:.8px;
    white-space:normal;
    overflow-wrap:break-word;
}

.eventTitle div{
    font-size:clamp(13px, 1.6vw, 18px);
    line-height:1.25;
}
.layout{display:grid;grid-template-columns:340px 1fr;min-height:calc(100vh - 118px)}
.sidebar{background:#081827;border-right:1px solid #21415f;padding:16px;overflow:auto}
.sideTitle{font-weight:900;color:#f5c542;margin:6px 0 14px;font-size:20px}
.quickMenu{
    margin-bottom:18px;
    padding-bottom:16px;
    border-bottom:1px solid #21415f;
}

.quickMenuTitle{
    font-weight:900;
    color:#ffd34d;
    margin:6px 0 12px;
    font-size:20px;
    text-transform:uppercase;
}

.quickMenuBtn{
    width:100%;
    display:block;
    border:1px solid #244765;
    border-radius:10px;
    background:#123c67;
    color:white;
    font-size:17px;
    font-weight:900;
    padding:13px 14px;
    margin-bottom:9px;
    text-align:left;
    cursor:pointer;
}

.quickMenuBtn:hover,
.quickMenuBtn.active{
    background:#ffd34d;
    color:#06111f;
}

.tool-card{
    background:#0b1d30;
    border:1px solid #203e5b;
    border-radius:14px;
    padding:14px;
    margin-bottom:16px;
}

.tool-title-row{
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:12px;
    margin-bottom:12px;
}

.tool-title-row h3{
    margin:0;
    color:#ffd34d;
    font-size:22px;
}

.filter-grid{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
    gap:10px;
    margin-bottom:12px;
}

.filter-grid input,
.filter-grid select{
    width:100%;
    padding:10px 12px;
    border:1px solid #244765;
    border-radius:10px;
    background:#06192b;
    color:white;
    outline:none;
}

.filter-grid input::placeholder{
    color:#91a9bf;
}

.table-scroll{
    width:100%;
    overflow:auto;
    border:1px solid #1e3a55;
    border-radius:12px;
}

#infoListTable,
#rankingTable{
    min-width:900px;
}

.rank-gold{
    color:#ffd34d;
    font-weight:900;
}

.rank-silver{
    color:#dce7f3;
    font-weight:900;
}

.rank-bronze{
    color:#e2a85d;
    font-weight:900;
}

.rank-fourth{
    color:#9fd0ff;
    font-weight:900;
}

.ranking-card-grid{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(420px,1fr));
    gap:16px;
}
/* Kyeorugi ranking: 3 divisions per row on desktop. */
/* Same card dimensions, title fonts and table spacings as Poomsae Ranking.
   Reserve three equal grid slots on wide screens, even when only two exist. */
#kRankingCards{grid-template-columns:repeat(3,minmax(0,1fr))}
@media (max-width:1100px ){ #kRankingCards{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:700px ){ #kRankingCards{grid-template-columns:1fr}}


.ranking-division-card{
    border:1px solid #24527a;
    border-radius:14px;
    overflow:hidden;
    background:#08233c;
}

.ranking-division-title{
    background:#123c67;
    color:#ffd34d;
    font-size:18px;
    font-weight:900;
    text-align:center;
    padding:12px;
    letter-spacing:.5px;
}

.ranking-medal-table{
    width:100%;
    border-collapse:collapse;
    min-width:0!important;
}

.ranking-medal-table th{
    background:#0f2e4e;
    color:white;
    font-size:13px;
    padding:9px;
}

.ranking-medal-table td{
    color:white;
    font-size:14px;
    font-weight:700;
    padding:10px;
    border-bottom:1px solid #1e3a55;
}

.medal-cell{
    width:95px;
    white-space:nowrap;
    font-weight:900;
}

.medal-gold{
    color:#ffd34d!important;
}

.medal-silver{
    color:#dce7f3!important;
}

.medal-bronze{
    color:#e2a85d!important;
}

.ranking-empty-cell{
    color:#7893aa!important;
    font-weight:600!important;
}

.medal-standing-table{
    min-width:1200px;
    table-layout:auto;
}

.medal-standing-table th{
    text-align:center;
    vertical-align:middle;
    white-space:nowrap;
}

.medal-standing-table td{
    text-align:center;
    vertical-align:middle;
}



.medal-division-cell{
    min-width:118px;
    font-weight:900;
    line-height:1.35;
    white-space:nowrap;
}

.medal-icon-wrap{
    display:flex;
    align-items:center;
    justify-content:center;
    gap:6px;
    font-size:20px;
    min-height:24px;
}

.medal-icon-item{
    display:inline-flex;
    align-items:center;
    justify-content:center;
    font-weight:900;
}

.medal-icon-symbol{
    font-size:22px;
    line-height:1;
}

.medal-standing-table{
    min-width:1200px;
    table-layout:auto;
    border-collapse:separate;
    border-spacing:0;
}

.medal-standing-table thead th{
    text-align:center!important;
    vertical-align:middle!important;
    white-space:nowrap;
    font-weight:900;
    border:1px solid #3b79ad!important;
    box-shadow:inset 0 0 0 1px rgba(255,255,255,.06);
}

.medal-standing-table tbody td{
    border-right:1px solid #1e3a55;
    border-bottom:1px solid #1e3a55;
}

.medal-standing-head-type{
    background:#164d82!important;
    color:#ffd34d!important;
    font-size:17px!important;
    letter-spacing:.5px;
    height:34px;
}

.medal-standing-head-division{
    background:#1fd0a6!important;
    color:#06111f!important;
    font-size:15px!important;
    height:32px;
    text-transform:uppercase;
}

.medal-standing-head-age{
    background:#0b2947!important;
    color:#d7efff!important;
    font-size:12px!important;
    min-width:120px;
    height:32px;
}

.medal-standing-fixed-head{
    background:#123c67!important;
    color:#ffd34d!important;
    font-size:14px!important;
    text-align:center!important;
    vertical-align:middle!important;
    border:1px solid #3b79ad!important;
}

.medal-standing-summary-head{
    background:#123c67!important;
    color:#ffd34d!important;
    font-size:13px!important;
    text-align:center!important;
    vertical-align:middle!important;
    border:1px solid #3b79ad!important;
}

/* ===== Medal Standings: chỉ cố định cột Unit/NOC, không ăn nhầm header con ===== */

.medal-standing-fixed-head{
    position:sticky;
    left:0;
    z-index:20;
    background:#123c67!important;
    color:#ffd34d!important;
    font-size:14px!important;
    text-align:center!important;
    vertical-align:middle!important;
    border:1px solid #3b79ad!important;
}

.medal-standing-unit-cell{
    position:sticky;
    left:0;
    z-index:10;
    background:#0b1d30!important;
    font-weight:900;
    color:#ffd34d;
    text-align:left!important;
    border-right:1px solid #3b79ad!important;
    border-bottom:1px solid #1e3a55!important;
}

.medal-standing-head-type{
    background:#164d82!important;
    color:#ffd34d!important;
    font-size:17px!important;
    letter-spacing:.5px;
    height:34px;
    text-align:center!important;
    vertical-align:middle!important;
    border:1px solid #3b79ad!important;
}

.medal-standing-head-division{
    background:#1fd0a6!important;
    color:#06111f!important;
    font-size:15px!important;
    height:32px;
    text-transform:uppercase;
    text-align:center!important;
    vertical-align:middle!important;
    border:1px solid #3b79ad!important;
}

.medal-standing-head-age{
    background:#0b2947!important;
    color:#d7efff!important;
    font-size:12px!important;
    min-width:120px;
    height:32px;
    text-align:center!important;
    vertical-align:middle!important;
    border:1px solid #3b79ad!important;
}

.medal-standing-summary-head{
    background:#123c67!important;
    color:#ffd34d!important;
    font-size:13px!important;
    text-align:center!important;
    vertical-align:middle!important;
    border:1px solid #3b79ad!important;
}

.medal-standing-gold{
    color:#ffd34d;
}

.medal-standing-silver{
    color:#dce7f3;
}

.medal-standing-bronze{
    color:#e2a85d;
}

.medal-standing-total{
    font-weight:900;
    font-size:15px;
}

.medal-standing-rank{
    font-size:18px;
    font-weight:900;
    color:#7fe7ff;
}

.medal-unit-sub{
    display:block;
    margin-top:4px;
    color:#9bb7cf;
    font-size:12px;
    font-weight:700;
    line-height:1.35;
}

.medal-unit-main{
    display:block;
    color:#ffd34d;
    font-size:15px;
    font-weight:900;
    margin-bottom:4px;
}

.medal-unit-total{
    color:#ffffff;
    font-weight:900;
}

.medal-unit-male{
    color:#7fe7ff;
    font-weight:800;
}

.medal-unit-female{
    color:#ffb3d1;
    font-weight:800;
}

.medal-unit-other{
    color:#cfe6ff;
    font-weight:800;
}

.medal-unit-coach{
    color:#b8ff7a;
    font-weight:800;
}

.group{margin-bottom:10px;border:1px solid #203b57;border-radius:12px;overflow:hidden;background:#0c2135}
.groupHead,.subHead,.genderHead,.ageBtn{
    width:100%;
    border:0;
    color:#fff;
    text-align:left;
    cursor:pointer;
}

.groupHead{
    padding:14px 18px;
    background:#123c67;
    font-size:18px;
    font-weight:900;
}

.subHead{
    padding:12px 34px;
    background:#0f2e4e;
    font-size:16px;
    font-weight:800;
}

.genderHead{
    padding:11px 52px;
    background:#0c2842;
    color:#d7e9ff;
    font-size:15px;
    font-weight:800;
}

.ageBtn{
    padding:10px 76px;
    background:#0b2239;
    font-size:14px;
    border-top:1px solid #173959;
}

.ageBtn:hover,
.ageBtn.active{
    background:#f5c542;
    color:#06111f;
    font-weight:900;
}
.hidden{display:none!important}
.notice-dialog{width:min(610px,calc(100vw - 28px));text-align:center}
.notice-message{font-size:16px;line-height:1.65;white-space:pre-line;color:#eef6ff;margin:18px 0}
.notice-dialog .actions{justify-content:center!important}
.notice-dialog .actions button{min-width:125px}
.content{padding:18px;overflow:auto}
.selectedBox{display:flex;justify-content:space-between;align-items:center;background:#0d2238;border:1px solid #244765;border-radius:14px;padding:16px 18px;margin-bottom:16px}
.label{color:#9bb7cf;font-size:13px;text-transform:uppercase}
.selectedBox h2{margin:4px 0 0;font-size:25px}
.selectedBox button,.btn-result{padding:10px 22px;border:0;border-radius:10px;background:#ffd34d;color:#001b33;font-weight:900;cursor:pointer}
.selectedBox button:hover,.btn-result:hover{filter:brightness(1.08)}
.card{background:#0b1d30;border:1px solid #203e5b;border-radius:14px;padding:14px;margin-bottom:16px}
.section-title-row{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}
.section-title-row h3,.card h3{margin:0;color:#ffd34d}
table{
    width:100%;
    max-width:100%;
    border-collapse:collapse;
    min-width:0;
    table-layout:auto;
}
th,td{
    border-bottom:1px solid #1e3a55;
    padding:10px;
    text-align:left;
    font-size:14px;
    white-space:normal;
    word-break:normal;
    overflow-wrap:break-word;
}
#athleteTable th:nth-child(1),
#athleteTable td:nth-child(1){
    width:70px;
    text-align:center;
    white-space:nowrap;
}

#athleteTable th:nth-child(2),
#athleteTable td:nth-child(2){
    width:90px;
    text-align:center;
    white-space:nowrap;
}

#athleteTable th:nth-child(3),
#athleteTable td:nth-child(3){
    width:auto;
    min-width:260px;
    white-space:normal;
    word-break:normal;
    overflow-wrap:break-word;
}

#athleteTable th:nth-child(4),
#athleteTable td:nth-child(4){
    width:95px;
    text-align:center;
    white-space:nowrap;
}
th{background:#123c67;color:#fff;position:sticky;top:0}
tr:hover td{background:#102b46}
.empty{padding:18px;color:#b9c9d8}
.layout.result-mode{display:block}
.layout.result-mode .content{width:100%;max-width:none;padding:18px;min-height:calc(100vh - 118px)}
.layout.result-mode #sigmaView{min-height:calc(100vh - 155px);display:flex;flex-direction:column}
.layout.result-mode .sigma-bracket{flex:1;min-height:0}
.layout.result-mode .sigma-svg{height:auto!important;min-height:0!important}
.sigma-view{margin-top:10px;border:1px solid #24527a;border-radius:14px;padding:14px;background:#06192b}
.sigma-title-row{
    display:grid;
    grid-template-columns:120px 1fr 120px;
    align-items:center;
    margin-bottom:22px;
    padding-top:6px;
}
.sigma-title{
    text-align:center;
    color:#ffd34d;
    font-size:38px;
    font-weight:900;
    letter-spacing:4px;
    margin:0;
    line-height:1.05;
    text-shadow:
        0 2px 0 rgba(0,0,0,.55),
        0 0 12px rgba(255,211,77,.25);
}
.sigma-bracket{display:block;width:100%;overflow:hidden;padding:0}
.sigma-svg{width:100%;height:auto;min-height:0;display:block;background:#06192b;border:1px solid #24527a;border-radius:12px}
.sigma-svg-box{fill:#08233c;stroke:#2f6fa3;stroke-width:1.8}
.sigma-svg-box.player-blue{
    fill:#0B63CE;
    stroke:#003B8E;
    stroke-width:3;
}

.sigma-svg-box.player-red{
    fill:#C93C4A;
    stroke:#7A1F2B;
    stroke-width:3;
}

.sigma-svg-name-blue,
.sigma-svg-noc-blue,
.sigma-svg-seed-blue{
    fill:white;
    font-size:15px;
    font-weight:900;
}

.sigma-svg-name-red,
.sigma-svg-noc-red,
.sigma-svg-seed-red{
    fill:white;
    font-size:15px;
    font-weight:900;
}
.sigma-svg-line{
    stroke:#9fd0ff;
    stroke-width:2;
    fill:none;
    stroke-linecap:square;
    stroke-linejoin:miter;
}

.sigma-svg-guide{
    stroke:#5f7f9d;
    stroke-width:1;
    stroke-dasharray:4 6;
    opacity:.55;
}

.sigma-svg-phase-box{
    fill:#f5f8fc;
    stroke:#cbd5e1;
    stroke-width:1.2;
}

.sigma-svg-phase-text{
    fill:#17324d;
    font-size:12px;
    font-weight:800;
}

.sigma-svg-code-box{
    fill:#ffffff;
    stroke:green;
    stroke-width:2.2;
}

.sigma-svg-code{
    fill:green;
    font-size:13px;
    font-weight:900;
    paint-order:stroke;
    stroke:#7a5a00;
    stroke-width:.6px;
    stroke-linejoin:round;
}
.sigma-svg-code-box.winner{fill:#ffd34d;stroke:#ffd34d}

.sigma-svg-code-box.winner-blue{
    fill:#0B63CE;
    stroke:#003B8E;
    stroke-width:3;
}

.sigma-svg-code-box.winner-red{
    fill:#C93C4A;
    stroke:#7A1F2B;
    stroke-width:3;
}



.sigma-svg-winner-code{
    fill:#001b33;
    font-size:14px;
    font-weight:900;
}

.sigma-svg-winner-code-blue{
    fill:white;
    font-size:14px;
    font-weight:900;
}

.sigma-svg-winner-code-red{
    fill:white;
    font-size:14px;
    font-weight:900;
}

/* ===== Champion Final Box ===== */
.sigma-svg-code-box.champion{
    fill:#f6d56b!important;
    stroke:#ffbf00!important;
    stroke-width:3.5!important;
}

.sigma-svg-winner-code-champion{
    fill:#ffffff!important;
    font-size:15px!important;
    font-weight:900!important;
    paint-order:stroke;
    stroke:#5b4100;
    stroke-width:2px;
    stroke-linejoin:round;
}

.sigma-svg-champion-cup{
    font-size:42px!important;
    font-weight:900!important;
}


@media(max-width:1100px){.layout{grid-template-columns:280px 1fr}.eventTitle h1{font-size:25px}}
@media(max-width:700px){
    .topbar{
        grid-template-columns:120px minmax(0,1fr);
        padding:8px 10px;
        min-height:110px;
    }

    .rightBlank{
        display:none;
    }

    .logoBox img{
        max-height:78px;
        max-width:110px;
    }

    .eventTitle{
        padding:0 4px;
    }

    .header-title{
        font-size:clamp(18px, 6vw, 28px);
        line-height:1.08;
        letter-spacing:.5px;
    }

    .header-date,
    .header-location{
        font-size:clamp(11px, 3.2vw, 15px);
        line-height:1.18;
        margin-top:3px;
    }

    .layout{
        min-height:calc(100vh - 110px);
    }
}

@media(max-width:430px){
    .topbar{
        grid-template-columns:1fr;
        text-align:center;
        min-height:auto;
        padding:8px 8px 10px;
    }

    .logoBox{
        display:flex;
        justify-content:center;
        margin-bottom:6px;
    }

    .logoBox img{
        max-height:62px;
        max-width:96px;
    }

    .header-title{
        font-size:22px;
    }

    .header-date,
    .header-location{
        font-size:12px;
    }
}
.header-title{
    color:#FF8F0F;
    font-size:clamp(34px, 4.2vw, 60px);
    font-weight:900;
    letter-spacing:1.4px;
    line-height:1.05;
    text-transform:uppercase;
    text-shadow:
        0 2px 0 rgba(0,30,70,.9),
        0 0 10px rgba(255,255,255,.38),
        0 0 22px rgba(0,180,255,.25);
}

.event-meta{
    display:flex;
    flex-direction:column;
    align-items:center;
    justify-content:center;
    gap:5px;
    margin-top:8px;
}

.event-meta-row{
    display:inline-flex;
    align-items:center;
    justify-content:center;
    gap:8px;
    min-height:25px;
    padding:3px 13px;
    border:1px solid rgba(255,211,77,.35);
    border-radius:999px;
    background:rgba(3,21,45,.45);
    box-shadow:
        inset 0 0 0 1px rgba(255,255,255,.04),
        0 3px 10px rgba(0,0,0,.18);
}

.event-meta-row.venue-row{
    border-color:rgba(127,231,255,.32);
    background:rgba(3,28,55,.42);
}

.event-meta-label{
    color:#ffd34d;
    font-size:12px;
    font-weight:900;
    letter-spacing:1.2px;
    text-transform:uppercase;
}

.header-date{
    color:#ffffff;
    font-size:16px;
    font-weight:900;
    letter-spacing:.3px;
}

.header-location{
    color:#7fe7ff;
    font-size:16px;
    font-weight:900;
    letter-spacing:.2px;
}
.genderHead{
    width:100%;
    border:0;
    cursor:pointer;
    text-align:left;

    padding:10px 52px;

    background:#12385d;

    color:#d7e9ff;
    font-size:15px;
    font-weight:700;

    border-left:4px solid #2f6aa3;
}
.cutoff-result-wrap{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(360px,1fr));
    gap:18px;
    padding:10px;
}

.cutoff-phase-box{
    border:1px solid #24527a;
    border-radius:14px;
    background:#08233c;
    overflow:hidden;
}

.cutoff-phase-title{
    background:#123c67;
    color:#ffd34d;
    font-size:22px;
    font-weight:900;
    text-align:center;
    padding:12px;
    letter-spacing:1px;
}

.cutoff-table{
    width:100%;
    min-width:0;
    border-collapse:collapse;
}

.cutoff-table th{
    background:#0f2e4e;
    color:white;
    font-size:14px;
    padding:10px;
}

.cutoff-table td{
    color:white;
    font-size:15px;
    font-weight:700;
    padding:10px;
    border-bottom:1px solid #1e3a55;
}

.cutoff-table td:first-child{
    color:#ffd34d;
    font-weight:900;
    text-align:center;
}

/* ================= LEFT SIDEBAR TOGGLE + DRAG TABLE ================= */

.sidebar-toggle-btn{
    position:fixed;
    left:285px;
    top:150px;
    z-index:9999;
    width:35px;
    height:35px;
    border:2px solid #ffd34d;
    border-radius:10px;
    background:#123c67;
    color:#ffd34d;
    font-size:20px;
    font-weight:900;
    line-height:1;
    cursor:pointer;
    box-shadow:0 8px 24px rgba(0,0,0,.35);
    display:flex;
    align-items:center;
    justify-content:center;
}

.sidebar-toggle-btn:hover{
    background:#ffd34d;
    color:#06111f;
}

body.sidebar-collapsed-desktop .sidebar-toggle-btn{
    left:20px;
}

.sidebar-backdrop{
    display:none;
}

.layout{
    grid-template-columns:340px minmax(0,1fr);
}

.layout > .sidebar{
    grid-column:1;
}

.layout > .content{
    grid-column:2;
    min-width:0;
}

.layout.sidebar-collapsed{
    grid-template-columns:0 minmax(0,1fr);
}

.layout.sidebar-collapsed .sidebar{
    width:0!important;
    min-width:0!important;
    padding:0!important;
    overflow:hidden!important;
    border-right:0!important;
}

.layout.sidebar-collapsed .content{
    grid-column:2;
    padding-left:18px;
}

/* Medal Standings kéo ngang/dọc */
#medalStandingsView .table-scroll{
    max-height:calc(100vh - 265px);
    overflow:auto;
    cursor:grab;
    user-select:none;
    -webkit-overflow-scrolling:touch;
    overscroll-behavior:contain;
}

#medalStandingsView .table-scroll.dragging{
    cursor:grabbing;
}

#medalStandingsTable{
    min-width:max-content;
}

/* ===== Medal Standings: đóng băng header 3 tầng, không bị hở ===== */

#medalStandingsTable{
    border-collapse:separate!important;
    border-spacing:0!important;
}

/* Ép chiều cao 3 hàng header cố định để rowspan hai bên khớp tuyệt đối */
#medalStandingsTable thead tr:nth-child(1){
    height:36px!important;
}

#medalStandingsTable thead tr:nth-child(2){
    height:32px!important;
}

#medalStandingsTable thead tr:nth-child(3){
    height:32px!important;
}

/* Tất cả ô header không dùng padding lớn nữa, tránh tạo khoảng hở */
#medalStandingsTable thead th{
    position:sticky!important;
    padding:0 8px!important;
    line-height:1.15!important;
    box-sizing:border-box!important;
    vertical-align:middle!important;
    background-clip:padding-box!important;
}

/* Hàng 1: Recognized / Freestyle + Unit/NOC + Total */
#medalStandingsTable thead tr:nth-child(1) th{
    top:0!important;
    height:36px!important;
    z-index:60!important;
}

/* Hàng 2: Individual / Pair / Team */
#medalStandingsTable thead tr:nth-child(2) th{
    top:36px!important;
    height:32px!important;
    z-index:58!important;
}

/* Hàng 3: Female Cadet / Male Cadet / ... */
#medalStandingsTable thead tr:nth-child(3) th{
    top:68px!important;
    height:32px!important;
    z-index:56!important;
}

/* Các ô rowspan 3 hàng phải cao đúng bằng 36 + 32 + 32 = 100px */
#medalStandingsTable .medal-standing-fixed-head,
#medalStandingsTable .medal-standing-summary-head{
    height:100px!important;
    min-height:100px!important;
    top:0!important;
    z-index:70!important;
}

/* Unit/NOC header vừa sticky dọc vừa sticky ngang */
#medalStandingsTable .medal-standing-fixed-head{
    position:sticky!important;
    left:0!important;
    z-index:90!important;
}

/* Cột Unit/NOC body sticky ngang */
#medalStandingsTable tbody .medal-standing-unit-cell{
    position:sticky!important;
    left:0!important;
    z-index:35!important;
    background:#0b1d30!important;
}

/* Nền chắc để không bị xuyên/chừa hở khi kéo */
#medalStandingsTable .medal-standing-head-type{
    background:#164d82!important;
}

#medalStandingsTable .medal-standing-head-division{
    background:#1fd0a6!important;
}

#medalStandingsTable .medal-standing-head-age{
    background:#0b2947!important;
}

#medalStandingsTable .medal-standing-fixed-head,
#medalStandingsTable .medal-standing-summary-head{
    background:#123c67!important;
}


/* Mobile / Tablet: menu trượt từ bên trái */
@media(max-width:900px){
    body{
        overflow-x:hidden;
    }

    .topbar{
        grid-template-columns:72px minmax(0,1fr) 48px;
        min-height:92px;
        padding:8px 8px;
    }

    .logoBox img{
        max-height:62px;
        max-width:74px;
    }

    .header-title{
        font-size:clamp(17px, 5vw, 28px)!important;
        line-height:1.08;
    }

    .header-date,
    .header-location{
        font-size:clamp(11px, 3vw, 14px)!important;
    }

    .layout{
        display:block;
        min-height:calc(100vh - 92px);
    }

    .content{
        width:100%;
        padding:10px;
    }

    .sidebar{
        position:fixed;
        top:0;
        left:-86vw;
        width:86vw;
        max-width:360px;
        height:100vh;
        z-index:9998;
        background:#081827;
        border-right:2px solid #ffd34d;
        border-left:0;
        padding:16px;
        overflow:auto;
        transition:left .24s ease;
        box-shadow:12px 0 30px rgba(0,0,0,.45);
    }

    .layout.menu-open .sidebar{
        left:0;
    }

    .sidebar-backdrop{
        position:fixed;
        inset:0;
        z-index:9997;
        background:rgba(0,0,0,.48);
        display:none;
    }

    body.sidebar-open .sidebar-backdrop{
        display:block;
    }

    .sidebar-toggle-btn{
        top:22px;
        left:10px;
        right:auto;
        width:42px;
        height:42px;
        font-size:24px;
        border-radius:11px;
    }

    body.sidebar-open .sidebar-toggle-btn{
        left:calc(min(86vw, 360px) + 8px);
    }

    .tool-card{
        padding:10px;
        border-radius:12px;
    }

    .tool-title-row{
        align-items:flex-start;
    }

    .tool-title-row h3{
        font-size:18px;
        padding-left:0;
    }

    .btn-result{
        padding:9px 14px;
        font-size:13px;
    }

    .filter-grid{
        grid-template-columns:1fr;
        gap:8px;
    }

    #medalStandingsView .table-scroll{
        max-height:calc(100vh - 225px);
        border-radius:10px;
    }

    .medal-standing-table th,
    .medal-standing-table td{
        padding:7px 8px;
        font-size:12px;
    }

    .medal-standing-head-type{
        font-size:14px!important;
        height:30px;
    }

    .medal-standing-head-division{
        font-size:12px!important;
        height:28px;
    }

    .medal-standing-head-age{
        font-size:11px!important;
        min-width:92px;
        height:28px;
    }

    .medal-standing-fixed-head{
        min-width:126px!important;
        font-size:12px!important;
    }

    .medal-standing-unit-cell{
        min-width:126px;
    }

    .medal-unit-sub{
        font-size:10px;
        line-height:1.25;
    }

    .medal-icon-symbol{
        font-size:18px;
    }

    .medal-standing-total,
    .medal-standing-rank{
        font-size:13px;
    }
}

/* ================= CLEAN MOBILE FIX: LIST / RANKING / SIGMA ================= */

/* Không cho trang chính bị kéo ngang, nhưng không ép layout con quá mức */
html,
body{
    max-width:100%;
    overflow-x:hidden;
}

.layout,
.content{
    min-width:0;
}

/* Ranking Summary: desktop giữ đẹp, mobile chuyển 1 cột */
@media(max-width:900px){

    .content{
        padding:10px!important;
        overflow:visible!important;
    }

    .tool-card,
    .card,
    .selectedBox,
    .sigma-view{
        max-width:100%;
        min-width:0;
        box-sizing:border-box;
    }

    /* Ranking không bị bóp / không tràn */
    .ranking-card-grid{
        display:grid!important;
        grid-template-columns:1fr!important;
        gap:14px!important;
        width:100%!important;
    }

    .ranking-division-card{
        width:100%!important;
        max-width:100%!important;
        min-width:0!important;
        border-radius:16px!important;
        overflow:hidden!important;
    }

    .ranking-division-title{
        font-size:20px!important;
        line-height:1.2!important;
        padding:14px 10px!important;
        word-break:break-word;
    }

    .ranking-medal-table{
        width:100%!important;
        min-width:0!important;
        table-layout:fixed!important;
    }

    .ranking-medal-table th,
    .ranking-medal-table td{
        padding:10px 8px!important;
        font-size:14px!important;
        white-space:normal!important;
        word-break:break-word!important;
    }

    .ranking-medal-table th:nth-child(1),
    .ranking-medal-table td:nth-child(1){
        width:30%!important;
    }

    .ranking-medal-table th:nth-child(2),
    .ranking-medal-table td:nth-child(2){
        width:22%!important;
    }

    .ranking-medal-table th:nth-child(3),
    .ranking-medal-table td:nth-child(3){
        width:48%!important;
    }

    .medal-cell{
        width:auto!important;
        white-space:normal!important;
    }

    /* Information List: giữ dạng kéo ngang, không bóp cột */
    #infoListView .table-scroll{
        width:100%!important;
        max-width:100%!important;
        overflow:auto!important;
        -webkit-overflow-scrolling:touch;
    }

    #infoListTable{
        min-width:900px!important;
        width:max-content!important;
    }

    /* Team/Athlete List: bảng kéo ngang trong card, không làm vỡ màn hình */
    .athlete-card{
        overflow:hidden!important;
    }

    .athlete-card #athleteTable{
        display:block!important;
        width:100%!important;
        max-width:100%!important;
        min-width:0!important;
        overflow-x:auto!important;
        -webkit-overflow-scrolling:touch;
        border-collapse:collapse!important;
    }

    #athleteTable thead,
    #athleteTable tbody,
    #athleteTable tr{
        width:max-content;
        min-width:620px;
    }

    #athleteTable th,
    #athleteTable td{
        white-space:nowrap!important;
        font-size:14px!important;
        padding:10px 12px!important;
    }

    #athleteTable th:nth-child(1),
    #athleteTable td:nth-child(1){
        min-width:60px!important;
        width:60px!important;
    }

    #athleteTable th:nth-child(2),
    #athleteTable td:nth-child(2){
        min-width:90px!important;
        width:90px!important;
    }

    #athleteTable th:nth-child(3),
    #athleteTable td:nth-child(3){
        min-width:300px!important;
        width:300px!important;
        white-space:normal!important;
    }

    #athleteTable th:nth-child(4),
    #athleteTable td:nth-child(4){
        min-width:110px!important;
        width:110px!important;
    }

    /* Watching box: không để nút Refresh đè chữ */
    .selectedBox{
        display:grid!important;
        grid-template-columns:minmax(0,1fr) auto!important;
        gap:10px!important;
        align-items:center!important;
        padding:14px!important;
        overflow:hidden!important;
    }

    .selectedBox h2{
        font-size:clamp(22px, 6vw, 36px)!important;
        line-height:1.18!important;
        word-break:break-word!important;
        margin:4px 0 0!important;
    }

    .selectedBox button{
        padding:10px 18px!important;
        min-width:110px!important;
        white-space:nowrap!important;
    }

    /* Team/Athlete title row: không để nút Sigma đè tiêu đề */
    .section-title-row{
        display:grid!important;
        grid-template-columns:minmax(0,1fr) auto!important;
        gap:10px!important;
        align-items:center!important;
    }

    .section-title-row h3{
        font-size:clamp(20px, 5.5vw, 30px)!important;
        line-height:1.18!important;
        word-break:break-word!important;
    }

    .section-title-row .btn-result{
        min-width:105px!important;
        white-space:nowrap!important;
    }

    /* Sigma Result: sửa header 3 cột bị đè */
    .sigma-title-row{
        display:grid!important;
        grid-template-columns:1fr!important;
        gap:12px!important;
        text-align:center!important;
    }

    .sigma-title-row > div:first-child{
        display:none!important;
    }

    .sigma-title-row .btn-result,
    #btnReturn{
        justify-self:center!important;
        min-width:130px!important;
        padding:11px 22px!important;
    }

    .sigma-title{
        font-size:clamp(34px, 10vw, 56px)!important;
        line-height:1.1!important;
        letter-spacing:4px!important;
    }

    #sigmaDivisionTitle{
        font-size:clamp(22px, 6.2vw, 34px)!important;
        line-height:1.2!important;
        word-break:break-word!important;
    }

    /* Cutoff/Sigma table: nằm gọn trong khung, kéo ngang khi cần */
    .sigma-bracket{
        width:100%!important;
        overflow:auto!important;
        -webkit-overflow-scrolling:touch;
    }

    .cutoff-result-wrap{
        display:grid!important;
        grid-template-columns:1fr!important;
        gap:14px!important;
        padding:8px!important;
        min-width:0!important;
    }

    .cutoff-phase-box{
        width:100%!important;
        max-width:100%!important;
        overflow:auto!important;
        border-radius:14px!important;
    }

    .cutoff-table{
        min-width:520px!important;
        width:max-content!important;
    }

    .cutoff-table th,
    .cutoff-table td{
        font-size:14px!important;
        padding:10px 8px!important;
        white-space:normal!important;
    }

    /* Medal standings giữ kéo ngang */
    #medalStandingsView{
        overflow:hidden!important;
    }

    #medalStandingsView .table-scroll{
        width:100%!important;
        max-width:100%!important;
        overflow:auto!important;
        -webkit-overflow-scrolling:touch;
    }

    #medalStandingsTable{
        min-width:max-content!important;
    }
}

/* Điện thoại nhỏ */
@media(max-width:480px){

    .content{
        padding:8px!important;
    }

    .tool-card,
    .card,
    .selectedBox,
    .sigma-view{
        border-radius:15px!important;
        padding:10px!important;
    }

    .ranking-division-title{
        font-size:18px!important;
    }

    .ranking-medal-table th,
    .ranking-medal-table td{
        font-size:13px!important;
        padding:9px 7px!important;
    }

    #athleteTable thead,
    #athleteTable tbody,
    #athleteTable tr{
        min-width:600px;
    }

    #athleteTable th,
    #athleteTable td{
        font-size:13px!important;
    }

    .selectedBox{
        grid-template-columns:1fr!important;
    }

    .selectedBox button{
        justify-self:end!important;
    }

    .section-title-row{
        grid-template-columns:1fr auto!important;
    }
}

.athlete-table-scroll{
    width:100%;
    max-width:100%;
    overflow:auto;
    -webkit-overflow-scrolling:touch;
}

@media(max-width:900px){
    .athlete-card #athleteTable{
        display:table!important;
        width:max-content!important;
        min-width:620px!important;
    }
}

/* ================= SIGMA PRINT / PDF BUTTONS ================= */

.sigma-action-box{
    display:flex;
    flex-direction:column;
    gap:8px;
    align-items:stretch;
    justify-content:center;
}

.sigma-action-btn{
    min-width:118px;
    padding:9px 14px!important;
    white-space:nowrap;
    font-size:13px!important;

    background:#123c67!important;
    color:#ffd34d!important;
    border:2px solid #ffd34d!important;
    border-radius:10px!important;

    display:flex;
    align-items:center;
    justify-content:center;
    gap:7px;

    font-weight:900!important;
    box-shadow:0 4px 12px rgba(0,0,0,.28);
}

.sigma-action-btn:hover{
    background:#ffd34d!important;
    color:#06111f!important;
    filter:none!important;
}

.sigma-action-btn:active{
    transform:translateY(1px);
}

.cutoff-phase-title-row{
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:10px;
    background:#123c67;
    padding:10px 12px;
}

.cutoff-phase-title-row .cutoff-phase-title{
    background:transparent!important;
    padding:0!important;
    flex:1;
}

.cutoff-phase-actions{
    display:flex;
    gap:8px;
    flex-wrap:wrap;
    justify-content:flex-end;
}

.cutoff-print-btn{
    width:34px;
    height:30px;
    padding:0!important;

    display:inline-flex;
    align-items:center;
    justify-content:center;

    border:2px solid #ffd34d!important;
    border-radius:9px;

    background:#123c67!important;
    color:#ffd34d!important;

    font-size:15px;
    font-weight:900;
    line-height:1;

    cursor:pointer;
    white-space:nowrap;

    box-shadow:0 3px 10px rgba(0,0,0,.28);
}

.cutoff-print-btn:hover{
    background:#ffd34d!important;
    color:#06111f!important;
    filter:none!important;
}

.cutoff-print-btn:active{
    transform:translateY(1px);
}

.cutoff-phase-actions{
    display:flex;
    gap:6px;
    flex-wrap:nowrap;
    justify-content:flex-end;
    align-items:center;
}

@media(max-width:900px){
    .sigma-title-row{
        grid-template-columns:1fr!important;
        gap:12px!important;
    }

    .sigma-title-row > div:first-child{
        display:none!important;
    }

    .sigma-action-box{
        flex-direction:row;
        flex-wrap:wrap;
        justify-content:center;
        align-items:center;
    }

    .sigma-action-btn{
        min-width:105px;
        font-size:12px!important;
        padding:9px 12px!important;
    }

    .cutoff-phase-title-row{
        flex-direction:column;
        align-items:center;
        text-align:center;
    }

    .cutoff-phase-actions{
        justify-content:center;
        gap:7px;
    }

    .cutoff-print-btn{
        width:36px;
        height:32px;
        font-size:15px;
    }
}

.sigma-action-icon{
    font-size:17px;
    line-height:1;
    display:inline-flex;
    align-items:center;
    justify-content:center;
}

#btnReturn.sigma-action-btn{
    background:#ffd34d!important;
    color:#06111f!important;
    border:2px solid #ffd34d!important;
}

#btnReturn.sigma-action-btn:hover{
    background:#ffdf70!important;
    color:#06111f!important;
}

/* ================= SIDEBAR PANEL IN SIGMA RESULT ================= */

.layout.result-mode.menu-open .sidebar{
    display:block!important;
    position:fixed!important;
    top:0!important;
    left:0!important;
    width:340px!important;
    max-width:86vw!important;
    height:100vh!important;
    z-index:9998!important;

    background:#081827!important;
    border-right:2px solid #ffd34d!important;
    padding:16px!important;
    overflow:auto!important;

    box-shadow:12px 0 30px rgba(0,0,0,.45)!important;
}

.layout.result-mode.menu-open .content{
    width:100%!important;
}

.layout.result-mode.menu-open ~ .sidebar-backdrop,
body.sidebar-open .sidebar-backdrop{
    display:block!important;
}

/* Nút 3 gạch luôn nổi trên Sigma */
.layout.result-mode ~ .sidebar-toggle-btn,
body .sidebar-toggle-btn{
    z-index:10000!important;
}

/* Vị trí nút khi đang ở Sigma Result */
.layout.result-mode .sidebar-toggle-btn{
    left:14px!important;
}

/* Vì nút nằm ngoài .layout nên dùng body/result-mode bằng class phụ */
body.sigma-result-mode .sidebar-toggle-btn{
    left:14px!important;
    top:204px!important;
}

body.sigma-result-mode.sidebar-open .sidebar-toggle-btn{
    left:calc(min(340px, 86vw) + 10px)!important;
}

/* Mobile trong Sigma */
@media(max-width:900px){
    body.sigma-result-mode .sidebar-toggle-btn{
        top:22px!important;
        left:10px!important;
    }

    body.sigma-result-mode.sidebar-open .sidebar-toggle-btn{
        left:calc(min(86vw, 360px) + 8px)!important;
    }

    .layout.result-mode.menu-open .sidebar{
        width:86vw!important;
        max-width:360px!important;
    }
}



  /* Kyeorugi ONLY: wider Full name column, with left-aligned names.
     Existing Poomsae Team/Athlete List CSS is unaffected. */
  #athleteTable.kyeorugi-athletes th:nth-child(1),
  #athleteTable.kyeorugi-athletes td:nth-child(1){
      width:82px!important; min-width:82px!important; text-align:center!important;
  }
  #athleteTable.kyeorugi-athletes th:nth-child(2),
  #athleteTable.kyeorugi-athletes td:nth-child(2){
      width:82px!important; min-width:82px!important; text-align:center!important;
  }
  #athleteTable.kyeorugi-athletes th:nth-child(3),
  #athleteTable.kyeorugi-athletes td:nth-child(3){
      width:235px!important; min-width:185px!important; text-align:left!important;
  }
  #athleteTable.kyeorugi-athletes th:nth-child(4),
  #athleteTable.kyeorugi-athletes td:nth-child(4){
      width:auto!important; min-width:390px!important;
      text-align:left!important; white-space:normal!important;
  }
  #athleteTable.kyeorugi-athletes th:nth-child(5),
  #athleteTable.kyeorugi-athletes td:nth-child(5){
      width:135px!important; min-width:135px!important; text-align:left!important;
  }

  /* Poomsae athlete/team roster: 4 columns, grouped by competition No. */
  #athleteTable.poomsae-athletes th:nth-child(1),
  #athleteTable.poomsae-athletes td:nth-child(1){
      width:75px!important;min-width:75px!important;text-align:center!important;
  }
  #athleteTable.poomsae-athletes th:nth-child(2),
  #athleteTable.poomsae-athletes td:nth-child(2){
      width:95px!important;min-width:95px!important;text-align:center!important;
  }
  #athleteTable.poomsae-athletes th:nth-child(3),
  #athleteTable.poomsae-athletes td:nth-child(3){
      width:210px!important;min-width:150px!important;text-align:left!important;
      white-space:normal!important;
  }
  #athleteTable.poomsae-athletes th:nth-child(4),
  #athleteTable.poomsae-athletes td:nth-child(4){
      width:auto!important;min-width:300px!important;text-align:left!important;
      white-space:normal!important;overflow-wrap:anywhere!important;
  }
  
/* ===== Timelines inside current Event (single-file, read-only) ===== */
.timeline-tool{width:100%;min-width:0;max-width:none;background:#091725;border:1px solid #24527a;border-radius:13px;padding:9px;margin-bottom:12px}
.timeline-heading{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:6px 8px 10px;color:#f8faff}
.timeline-heading-title{font-size:clamp(18px,2vw,28px);font-weight:900;color:#ffe096}
.timeline-heading-title small{display:block;font-size:11px;letter-spacing:.08em;color:#b5d2e6}
.timeline-heading-meta{flex:1;text-align:center;color:#ffe096;font-weight:900;font-size:clamp(12px,1.2vw,17px)}
.timeline-clock{border:1.5px solid #d8b656;border-radius:12px;padding:7px 14px;text-align:center;white-space:nowrap;color:#ffe096;font-weight:900;font-size:clamp(12px,1.3vw,20px)}
.timeline-alert{font-size:13px;color:#ffd1d1;text-align:center;padding:2px 5px;min-height:18px}
.timeline-court-stack{display:flex;flex-direction:column;gap:6px;min-width:0}
.timeline-court-row{display:grid;grid-template-columns:3fr 2fr;gap:6px;height:clamp(142px,calc((100vh - 340px)/5),210px);min-height:142px}
.timeline-panel{min-width:0;border:1px solid #47647b;overflow:hidden;display:flex;flex-direction:column}
.timeline-panel-head{flex:0 0 auto;padding:5px 9px;font-weight:900;font-size:clamp(11px,1vw,16px);line-height:1.15;color:white;background:#135f89}
.timeline-current .timeline-panel-head{background:#864436}
.timeline-next-table{flex:1;min-height:0;display:grid;grid-template-rows:repeat(3,minmax(0,1fr))}
.timeline-next-line{min-height:0;display:grid;grid-template-columns:minmax(38px,7%) minmax(36px,7%) minmax(0,1.8fr) minmax(120px,1.15fr) minmax(0,1.8fr) minmax(36px,7%) minmax(38px,7%)}
.timeline-next-line>div{display:flex;align-items:center;min-width:0;overflow:hidden;padding:2px 4px;border:1px solid #153249;font-weight:800;font-size:clamp(10px,1.03vw,16px);line-height:1.12}
.timeline-next-line .tl-blue,.timeline-current-blue{background:#c6e5ff;color:#173451}
.timeline-next-line .tl-red,.timeline-current-red{background:#ffe1e2;color:#772735}
.timeline-next-line .tl-mid{background:#163349;color:#ffe096;justify-content:center;text-align:center;font-size:clamp(10px,.92vw,14px);white-space:normal;overflow-wrap:anywhere}
.timeline-next-line .tl-name{overflow-wrap:anywhere;word-break:normal}
.timeline-next-line .tl-red.tl-name{justify-content:flex-end;text-align:right}
.timeline-next-line .tl-wait{justify-content:center!important;text-align:center!important}
.timeline-flag{display:flex!important;justify-content:center!important;padding:1px!important}
.timeline-flag img{max-width:100%;max-height:35px;object-fit:contain}
.timeline-empty-flag{display:block}
.timeline-current-status{flex:0 0 auto;min-height:20px;background:#112b40;color:#ffe096;text-align:center;font-size:clamp(10px,.85vw,13px);font-weight:800;padding:3px 5px}
.timeline-current-sides{flex:1;display:grid;grid-template-columns:1fr 1fr;min-height:0}
.timeline-current-side{min-width:0;overflow:hidden;padding:5px 8px;display:flex;flex-direction:column;gap:2px}
.timeline-identity{display:flex;align-items:center;gap:8px;font-weight:900;font-size:clamp(13px,1.3vw,20px);min-height:27px}
.timeline-current-red .timeline-identity{justify-content:flex-end}
.timeline-current-side .timeline-flag img{max-width:52px;max-height:32px}
.timeline-athlete{font-size:clamp(12px,1.05vw,17px);font-weight:900;line-height:1.12;overflow-wrap:anywhere;min-height:1.2em}
.timeline-current-red .timeline-athlete{text-align:right}
.timeline-score-lines{margin-top:auto;display:grid;gap:2px}
.timeline-score-line{font-size:clamp(10px,1vw,15px);white-space:nowrap;overflow:hidden;text-overflow:clip;font-weight:850}
.timeline-current-red .timeline-score-line{text-align:right}
.timeline-score-line:last-child.timeline-total{background:#afd2f7;border-radius:4px;padding:2px}
.timeline-current-red .timeline-score-line:last-child.timeline-total{background:#f7bac9}
.timeline-soon{background:#112b40;border-radius:12px;min-height:220px;padding:40px 18px;text-align:center}
.timeline-soon h2{color:#ffe096;margin:0 0 14px}
.timeline-soon p{color:#bfd8e8}
.timeline-row-wave{animation:timeline-wave .45s ease both}
@keyframes timeline-wave{from{opacity:.15;transform:translateY(-14px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:1120px){.timeline-next-line{grid-template-columns:minmax(30px,7%) minmax(32px,7%) minmax(0,1.65fr) minmax(100px,1.1fr) minmax(0,1.65fr) minmax(32px,7%) minmax(30px,7%)}.timeline-court-row{height:158px}}
@media(max-width:850px){.timeline-court-row{grid-template-columns:1fr;height:auto;min-height:0}.timeline-panel{min-height:156px}.timeline-current{min-height:135px}.timeline-next-line>div{font-size:clamp(9px,1.4vw,13px)}.timeline-heading-meta{flex-basis:100%;order:3}}


/* Match Timeline V4 WEB: shared clock + viewport-locked five-court board. */
.rightBlank{display:flex;justify-content:flex-end;align-items:center;min-width:0}
.site-header-clock{width:clamp(120px,12vw,188px);padding:9px 8px;border:1.5px solid #e6be57;border-radius:11px;background:#17364c;color:#ffe096;text-align:center;font-weight:900;font-variant-numeric:tabular-nums;line-height:1.18}
#siteHeaderClockTime{font-size:clamp(16px,1.6vw,25px)}
#siteHeaderClockDate{font-size:clamp(12px,1.02vw,16px);margin-top:5px;color:white}
body.timeline-screen-open,body.timeline-screen-open html{overflow:hidden!important;height:100dvh}
body.timeline-screen-open .topbar{position:relative}
body.timeline-screen-open .layout.timeline-active{height:calc(100dvh - var(--timeline-topbar-height, 126px))!important;min-height:0!important;overflow:hidden!important}
body.timeline-screen-open .layout.timeline-active > .content{height:100%;min-height:0;overflow:hidden!important;padding:8px 12px!important;display:flex;flex-direction:column}
body.timeline-screen-open #timelineView{height:100%;min-height:0;margin:0;padding:6px 8px;display:flex;flex-direction:column;overflow:hidden}
body.timeline-screen-open #timelineMatchesContent{height:100%;flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden}
body.timeline-screen-open .timeline-court-stack{flex:1;min-height:0;display:grid;grid-template-rows:repeat(5,minmax(0,1fr));gap:5px;overflow:hidden}
body.timeline-screen-open .timeline-court-row{height:auto!important;min-height:0!important;grid-template-columns:minmax(0,3fr) minmax(0,2fr)!important;gap:5px}
body.timeline-screen-open .timeline-panel{min-height:0!important;min-width:0;overflow:hidden}
body.timeline-screen-open .timeline-next-table,body.timeline-screen-open .timeline-current-sides{min-height:0}
body.timeline-screen-open .timeline-next-line{min-height:0}
body.timeline-screen-open .timeline-next-line>div{min-height:0;padding:1px 3px;font-size:clamp(9px,.96vw,16px)}
body.timeline-screen-open .timeline-panel-head{padding:3px 8px;font-size:clamp(10px,.92vw,15px)}
body.timeline-screen-open .timeline-current-status{padding:1px 3px;min-height:0}
body.timeline-screen-open .timeline-current-side{padding:3px 6px}
body.timeline-screen-open .timeline-heading{position:relative;display:flex;align-items:center;justify-content:center;flex:none;min-height:58px;padding:1px 88px 4px}
body.timeline-screen-open .timeline-heading-center{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;min-width:0;text-align:center}
body.timeline-screen-open .timeline-heading-title{font-size:clamp(18px,1.7vw,27px);line-height:1.13}
body.timeline-screen-open .timeline-heading-meta{flex:none;font-size:clamp(12px,1.1vw,17px);line-height:1.13}
body.timeline-screen-open .timeline-refresh{position:absolute;right:5px;top:50%;transform:translateY(-50%);padding:8px 14px}
body.timeline-screen-open .timeline-alert{flex:none;min-height:0;padding:0;font-size:12px}
/* Page enters from the left, tilting softly like a page turn. The left panel leads. */
body.timeline-screen-open .timeline-row-wave{animation:none!important}
body.timeline-screen-open .timeline-row-wave > .timeline-panel{transform-origin:left center;animation:timeline-page-sweep .52s cubic-bezier(.22,.7,.22,1) both}
body.timeline-screen-open .timeline-row-wave > .timeline-panel.timeline-current{animation-delay:.11s}
@keyframes timeline-page-sweep{0%{opacity:.25;transform:perspective(1200px) rotateY(-6deg) translateX(-20px)}65%{opacity:.94;transform:perspective(1200px) rotateY(1deg) translateX(2px)}100%{opacity:1;transform:perspective(1200px) rotateY(0) translateX(0)}}
@media(max-width:900px){
 .rightBlank{display:flex!important}
 .site-header-clock{padding:5px;width:115px}
 #siteHeaderClockTime{font-size:14px}
 #siteHeaderClockDate{font-size:11px}
 body.timeline-screen-open .timeline-next-line{grid-template-columns:minmax(24px,6%) minmax(29px,7%) minmax(0,1.8fr) minmax(65px,1.15fr) minmax(0,1.8fr) minmax(29px,7%) minmax(24px,6%)}
 body.timeline-screen-open .timeline-next-line>div{font-size:9px}
 body.timeline-screen-open .timeline-refresh{padding:6px 8px;font-size:11px}
}
@media(max-width:550px){.topbar{grid-template-columns:60px minmax(0,1fr) 90px!important}.logoBox img{max-width:54px!important;max-height:55px!important}.site-header-clock{width:87px!important}#siteHeaderClockTime{font-size:12px}#siteHeaderClockDate{font-size:10px}}
@media(prefers-reduced-motion:reduce){body.timeline-screen-open .timeline-row-wave > .timeline-panel{animation:none!important}}


/* V4: adaptive text fits within its own timeline cell; no column-size shifts. */
.timeline-next-line .tl-fit-text,
.timeline-current-side .tl-fit-text{display:block;width:100%;max-width:100%;min-width:0;line-height:1.07;overflow-wrap:anywhere;word-break:normal}
/* Center NOC codes for both CHEONG and HONG in NEXT MATCHES. */
.timeline-next-line .tl-noc{justify-content:center!important;text-align:center!important}
.timeline-next-line .tl-noc .tl-fit-text{text-align:center!important}
.timeline-next-line .tl-mid .tl-fit-text{text-align:center}
.timeline-next-line .tl-name.tl-wait .tl-fit-text{text-align:center}
.timeline-next-line .tl-red.tl-name .tl-fit-text{text-align:right}
.timeline-next-line .tl-blue.tl-name .tl-fit-text{text-align:left}
.timeline-current-side .timeline-score-line .tl-fit-text{white-space:nowrap}
/* Accordion selection remains visible inside the sidebar, not the document. */
.sidebar .groupHead:focus-visible,.sidebar .subHead:focus-visible,
.sidebar .genderHead:focus-visible,.sidebar .ageBtn:focus-visible{outline:2px solid #ffd34d;outline-offset:-2px}


/* Schedule Division: shared read-only Referee layout, scoped to Sigma View */
.sd-timeline{background:#0b1d30;border:1px solid #203e5b;border-radius:14px;padding:14px;color:#eef6ff;max-width:100%;}
.sd-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px}
.sd-heading h2{font-size:23px;color:#ffd34d;margin:0 0 4px;font-weight:900}
.sd-heading p{font-size:13px;color:#a6c3da;margin:0}
.sd-heading .btn-result{flex:none}
.sd-days{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px}
.sd-day{cursor:pointer;background:#123c67;color:#fff;border:1px solid #315e80;border-radius:9px;padding:10px 18px;font-weight:900;font-size:14px}
.sd-day.active{background:#ffd34d;color:#102037;border-color:#ffd34d}
.sd-status{font-size:13px;color:#a9d7f6;margin:7px 0 11px;min-height:19px}
.sd-board-scroll{overflow:auto;max-height:calc(100dvh - 350px);border:1px solid #47647b;border-radius:12px;background:#f4f7fa;color:#162738;overscroll-behavior:contain}
.sd-board{min-width:0;width:100%;font-family:Arial,Helvetica,sans-serif;font-size:13px;--sd-courts:1}
.sd-head,.sd-row{display:grid;grid-template-columns:100px repeat(var(--sd-courts),minmax(105px,1fr));min-width:max(100%,calc(100px + var(--sd-courts)*105px))}
.sd-head{position:sticky;top:0;z-index:5}
.sd-head>div{background:#ffe6a0;color:#142536;font-weight:900;text-align:center;padding:11px 4px;border-right:1px solid #39434e;border-bottom:2px solid #39434e}
.sd-time{min-height:59px;display:flex;align-items:center;justify-content:center;text-align:center;padding:6px;background:#e7eef5;font-size:12px;font-weight:800;color:#153249;border-right:1px solid #9bafbf;border-bottom:1px solid #9bafbf;white-space:nowrap}
.sd-cell{background:#fff;min-height:59px;border-right:1px solid #9bafbf;border-bottom:1px solid #9bafbf;display:flex;flex-direction:column;align-items:stretch;justify-content:center;text-align:center;padding:3px 4px;min-width:0}
.sd-cell.booked{background:#d8ebf9}
.sd-entry{padding:5px 3px;border-bottom:1px solid #9bbbd1;overflow-wrap:anywhere;line-height:1.25}
.sd-entry:last-child{border-bottom:none}
.sd-entry strong{display:block;font-size:14px;font-weight:900;letter-spacing:.1px;color:#062b46}
.sd-entry small{display:block;font-size:11px;color:#385a73}
.sd-break{min-height:33px;text-align:center;padding:9px 20px;border-bottom:1px solid #6d7884;font-weight:900;letter-spacing:.3px}
.sd-break.lunch{background:#e6d3a7;color:#68480a;border-color:#b69547}
.sd-break.break{background:#c9e9e1;color:#0b5d50;border-color:#63a99a}
.sd-empty{padding:16px;background:#eaf1f7;color:#40556a;text-align:center}
@media(max-width:750px){.sd-timeline{padding:9px}.sd-day{padding:8px 12px}.sd-board-scroll{max-height:calc(100dvh - 290px)}}

/* Vietnamese header typography: crisp combining marks on Windows/Chrome. */
.topbar .eventTitle,
.topbar .eventTitle h1,
.topbar .event-meta,
.topbar .header-date,
.topbar .header-location {
  font-family: "Segoe UI", "Arial", sans-serif !important;
  font-kerning: normal;
  font-synthesis: none;
  letter-spacing: 0 !important;
  -webkit-font-smoothing: auto;
  text-rendering: optimizeLegibility;
}
.topbar .eventTitle { overflow: visible !important; }
.topbar .header-title {
  font-size: clamp(22px, 2.4vw, 42px) !important;
  font-weight: 800 !important;
  line-height: 1.32 !important;
  white-space: normal !important;
  overflow: visible !important;
  overflow-wrap: normal !important;
  word-break: normal !important;
  text-shadow: 0 2px 2px rgba(0, 24, 55, .75) !important;
}
.topbar .header-date,
.topbar .header-location {
  font-weight: 700 !important;
  line-height: 1.42 !important;
  text-shadow: none !important;
  white-space: normal;
}
.topbar .event-meta-row { min-height: 27px; line-height: 1.42; }
@media (max-width: 900px) {
  .topbar .header-title { font-size: clamp(17px, 4.1vw, 30px) !important; line-height: 1.32 !important; }
  .topbar .header-date, .topbar .header-location { font-size: clamp(11px, 2.4vw, 15px) !important; }
}
@media (max-width: 430px) {
  .topbar .header-title { font-size: clamp(18px, 5.2vw, 24px) !important; }
}


/* Referee-inspired visuals without interfering with Sigma's division/sidebar logic. */
body { background:#07111f; min-height:100vh; }
body::before { content:""; position:fixed; inset:0; z-index:-1; pointer-events:none;
  background-image:url("{{ url_for('portal_background') }}");
  background-position:center; background-size:cover; background-repeat:no-repeat; opacity:.50; }
/* Keep body and the original Sigma layout scrollable: no overflow:hidden or fixed viewport height. */
body > .topbar, body > main.layout, body > .sigma-footer { position:relative; }
body > main.layout { min-height:calc(100vh - 220px); }
.sigma-footer { position:relative; width:100%; text-align:center;
  background:linear-gradient(180deg,#081b30,#061323); border-top:1px solid #315879;
  color:#b9d1e8; font-size:11px; padding:9px 12px; line-height:1.18; }
.sigma-footer .footer-name {color:#ffd34d; font-weight:900; font-size:15px;letter-spacing:.5px}
.sigma-footer .footer-quote {color:#d9eaff; font-size:12px; margin:3px auto}
.sigma-footer .footer-contact {margin-top:3px}
.sigma-footer .footer-contact a {color:#83d7ff;text-decoration:none;font-weight:700}


/* =============================================================
   FIXED APP SHELL: header/footer stay visible; sidebar/body scroll
   independently. Scrollbars are visually hidden, not disabled.
   Does not change Division data, menu handlers, or Sigma logic.
   ============================================================= */
html{height:100%;overflow:hidden!important}
body{
  height:100vh;height:100dvh;min-height:0!important;
  display:grid!important;
  grid-template-rows:auto minmax(0,1fr) auto;
  grid-template-columns:minmax(0,1fr);
  overflow:hidden!important;
}
body > .topbar{
  grid-row:1;position:relative!important;z-index:12;
  min-width:0;
}
body > main.layout{
  grid-row:2;min-height:0!important;height:100%!important;
  width:100%;max-width:100%;overflow:hidden!important;
  position:relative!important;z-index:1;
}
body > .sigma-footer{
  grid-row:3;position:relative!important;z-index:12;
  width:100%;min-height:0;
}
/* Scroll containers retain wheel/touch/keyboard navigation. */
body > main.layout > .sidebar,
body > main.layout > .content{
  min-height:0!important;max-height:100%!important;
  overscroll-behavior:contain;
  overflow-y:auto!important;
  overflow-x:hidden;
  scrollbar-width:none!important; /* Firefox */
  -ms-overflow-style:none!important;
  -webkit-overflow-scrolling:touch;
}
body > main.layout > .sidebar::-webkit-scrollbar,
body > main.layout > .content::-webkit-scrollbar,
.sd-board-scroll::-webkit-scrollbar,
.table-scroll::-webkit-scrollbar,
.athlete-table-scroll::-webkit-scrollbar,
#medalStandingsView .table-scroll::-webkit-scrollbar,
.sigma-bracket::-webkit-scrollbar{width:0!important;height:0!important;display:none!important}
.sd-board-scroll,.table-scroll,.athlete-table-scroll,
#medalStandingsView .table-scroll,.sigma-bracket{
  scrollbar-width:none!important;-ms-overflow-style:none!important;
}
/* Result/Sigma mode uses display:block but its body panel still scrolls. */
body > main.layout.result-mode > .content{
  height:100%!important;max-height:100%!important;
  overflow-y:auto!important;
}
/* Match Timelines: use actual grid-row height, not a second viewport subtraction. */
body.timeline-screen-open .layout.timeline-active{
  height:100%!important;min-height:0!important;
}
body.timeline-screen-open .layout.timeline-active > .content{
  height:100%!important;max-height:100%!important;
  overflow:hidden!important;
}
/* Mobile side drawer keeps its original open/close behavior. */
@media(max-width:900px){
  body > main.layout{display:block!important}
  body > main.layout > .content{
    height:100%!important;max-height:100%!important;
    overflow-y:auto!important;
    overflow-x:hidden!important;
  }
  body > main.layout > .sidebar{
    position:fixed!important;
    top:0!important;
    height:100dvh!important;
    max-height:100dvh!important;
    overflow-y:auto!important;
  }
  .layout.result-mode.menu-open > .sidebar{
    overflow-y:auto!important;
  }
}
@media print{
  html,body{height:auto!important;overflow:visible!important;display:block!important}
  body > main.layout,body > main.layout > .content{
    height:auto!important;max-height:none!important;overflow:visible!important;
  }
}

</style>
</head>
<body>
<header class="topbar">
  <div class="logoBox"><img src="{{ logo_file }}" alt="NTP" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" /><span class="site-logo-fallback" style="display:none">NTP</span></div>
  <div class="eventTitle">
      <h1 class="header-title">{{ event_name }}</h1>

      <div class="event-meta">
          {% if event_date %}
          <div class="event-meta-row date-row">
              <span class="event-meta-label">Dates</span>
              <span class="header-date">{{ event_date }}</span>
          </div>
          {% endif %}

          {% if event_location %}
          <div class="event-meta-row venue-row">
              <span class="event-meta-label">Venue</span>
              <span class="header-location">{{ event_location }}</span>
          </div>
          {% endif %}
      </div>
  </div>
  <div class="rightBlank"><div id="siteHeaderClock" class="site-header-clock" aria-label="Current Vietnam time"><div id="siteHeaderClockTime"></div><div id="siteHeaderClockDate"></div></div></div>

</header>

<button id="btnToggleSidebar" class="sidebar-toggle-btn" type="button" title="Menu">
    ☰
</button>

<div id="sidebarBackdrop" class="sidebar-backdrop"></div>

<main class="layout">
  <aside class="sidebar">

      <div class="quickMenu">
          <div class="quickMenuTitle">REGISTRATION FORM</div>
          <button class="quickMenuBtn" type="button" onclick='window.location.href={{ registration_url|tojson }}'>▸ Registration Form</button>
      </div>

      <div class="quickMenu">
          <div class="quickMenuTitle">LIST & RANKING</div>

          <button id="btnOpenInfoList" class="quickMenuBtn">
              ▸ List
          </button>

          <button id="btnOpenMedalStandings" class="quickMenuBtn">
              ▸ Medal Standings
          </button>

          <button id="btnOpenRanking" class="quickMenuBtn">
              ▸ Poomsae Ranking
          </button>
          <button id="btnOpenKyeorugiRanking" class="quickMenuBtn" type="button">
              ▸ Kyeorugi Ranking
          </button>
      </div>

      <div class="quickMenu">
        <div class="quickMenuTitle">TIMELINE</div>
        <button id="btnScheduleDivisionTimeline" class="quickMenuBtn" type="button">▸ Schedule Division</button>
        <button id="btnMatchTimeline" class="quickMenuBtn" type="button">▸ Matchs Timeline</button>
      </div>

      <div class="sideTitle">DIVISIONS</div>
      <div id="menu"></div>

  </aside>



  <section class="content">
    <section id="timelineView" class="timeline-tool hidden" aria-label="Match Timelines">
      <div id="timelineSchedulePlaceholder" class="sd-timeline hidden">
        <div class="sd-heading"><div><h2>Schedule Division</h2><p>Competition order by day and court</p></div><button id="sdRefresh" type="button" class="btn-result">Refresh</button></div>
        <div id="sdDays" class="sd-days" role="tablist" aria-label="Competition days"></div>
        <div id="sdStatus" class="sd-status" role="status"></div>
        <div class="sd-board-scroll"><div id="sdBoard" class="sd-board"></div></div>
      </div>
      <div id="timelineMatchesContent" class="hidden">
        <div class="timeline-heading">
          <div class="timeline-heading-center">
            <div class="timeline-heading-title">MATCH TIMELINES</div>
            <div class="timeline-heading-meta" id="timelineDayInfo"></div>
          </div>
          <button class="btn-result timeline-refresh" type="button" id="timelineRefresh">Refresh</button>
        </div>
        <div class="timeline-alert" id="timelineAlert" role="status"></div>
        <div class="timeline-court-stack" id="timelineCourtStack"></div>
      </div>
    </section>
    <section id="infoListView" class="tool-card hidden">
        <div class="tool-title-row">
            <h3>Information List</h3>
            <button class="btn-result" onclick="loadInformationList()">Refresh</button>
        </div>

        <div class="filter-grid">
            <input id="infoSearch" placeholder="Tìm tên, NOC, quốc gia, đoàn..." oninput="renderInformationList()">

            <select id="infoNocFilter" onchange="renderInformationList()">
                <option value="">Country</option>
            </select>

            <select id="infoGenderFilter" onchange="buildInfoWeightOptions();renderInformationList()">
                <option value="">Gender</option>
                <option value="MALE">MALE</option>
                <option value="FEMALE">FEMALE</option>
            </select>

            <select id="infoAgeFilter" onchange="buildInfoWeightOptions();renderInformationList()">
                <option value="">Age</option>
            </select>

        </div>
        <div class="filter-grid info-division-filters">
            <select id="infoCategoryFilter" onchange="onInfoCategoryChange()">
                <option value="">Category: All</option>
                <option value="Poomsae">Poomsae</option>
                <option value="Kyeorugi">Kyeorugi</option>
            </select>
            <select id="infoEventFilter" onchange="onInfoDivisionChange()">
                <option value="">Division: All</option>
            </select>
            <select id="infoWeightFilter" onchange="renderInformationList()" class="hidden">
                <option value="">Weight: All</option>
            </select>
        </div>

        <div class="table-scroll">
            <table id="infoListTable"></table>
        </div>
    </section>


    <section id="rankingView" class="tool-card hidden">
        <div class="tool-title-row">
            <h3>Poomsae Ranking</h3>
            <button class="btn-result" onclick="loadRankingSummary()">Refresh</button>
        </div>

        <div class="filter-grid">
            <input id="rankingSearch" placeholder="Tìm NOC, tên VĐV hoặc division..." oninput="renderRankingSummary()">

            <select id="rankingTypeFilter" onchange="renderRankingSummary()">
                <option value="">All Type</option>
                <option value="Recognized">Recognized</option>
                <option value="Freestyle">Freestyle</option>
                <option value="Demonstration">Demonstration</option>
                <option value="Mixer">Mixer</option>
            </select>

            <select id="rankingMedalFilter" onchange="renderRankingSummary()">
                <option value="">All Medal</option>
                <option value="gold">Gold</option>
                <option value="silver">Silver</option>
                <option value="bronze">Bronze</option>
            </select>
        </div>

        <div id="rankingCards" class="ranking-card-grid"></div>
    </section>

    <section id="kyeorugiRankingView" class="tool-card hidden">
      <div class="tool-title-row">
        <h3>Kyeorugi Ranking</h3>
        <button class="btn-result" onclick="loadKyeorugiRanking()">Refresh</button>
      </div>
      <div class="filter-grid">
        <input id="kRankingSearch" placeholder="Tìm Division, NOC, VĐV..." oninput="renderKyeorugiRanking()" />
        <select id="kRankingMedal" onchange="renderKyeorugiRanking()">
          <option value="">All Medal</option><option value="gold">Gold</option>
          <option value="silver">Silver</option><option value="bronze">Bronze</option>
        </select>
      </div>
      <div id="kRankingStatus" class="k-status"></div>
      <div id="kRankingCards" class="ranking-card-grid"></div>
    </section>

    <section id="medalStandingsView" class="tool-card hidden">
        <div class="tool-title-row">
            <h3>Medal Standings</h3>
            <button class="btn-result" onclick="loadMedalStandings()">Refresh</button>
        </div>

        <div class="filter-grid">
            <input
                id="medalStandingSearch"
                placeholder="Tìm NOC, quốc gia, division..."
                oninput="renderMedalStandings()"
            >

            <select id="medalStandingTypeFilter" onchange="renderMedalStandings()">
                <option value="">All Type</option>
                <option value="Recognized">Recognized</option>
                <option value="Freestyle">Freestyle</option>
                <option value="Demonstration">Demonstration</option>
                <option value="Mixer">Mixer</option>
                <option value="Kyeorugi">Kyeorugi</option>
            </select>
        </div>

        <div class="table-scroll">
            <table id="medalStandingsTable" class="medal-standing-table"></table>
        </div>
    </section>

    <div class="selectedBox">
      <div>
        <div class="label">Watching</div>
        <h2 id="selectedTitle">Select Division</h2>
      </div>
      <button onclick="reloadCurrent()">Refresh</button>
    </div>

    <section class="card athlete-card">
      <div class="section-title-row">
        <h3>Team/Athlete List</h3>
        <button id="btnResult" class="btn-result">Sigma</button>
      </div>

      <div class="table-scroll athlete-table-scroll">
          <table id="athleteTable"></table>
      </div>
    </section>

    <div id="sigmaView" class="sigma-view hidden">
      <div class="sigma-title-row">
        <div></div>

        <div style="text-align:center">
            <div class="sigma-title">SIGMA RESULT</div>
            <div id="sigmaDivisionTitle"
                 style="
                    color:#cfe6ff;
                    font-size:23px;
                    font-weight:900;
                    margin-top:8px;
                    letter-spacing:1px;
                    line-height:1.15;
                    text-shadow:0 2px 0 rgba(0,0,0,.4);
                 ">
            </div>
            <div id="sigmaRoundSummary" class="sigma-round-summary"></div>
        </div>

        <div class="sigma-action-box">
            <button id="btnSigmaPrint" class="btn-result sigma-action-btn" type="button">
                <span class="sigma-action-icon">🖨</span>
            </button>

            <button id="btnSigmaPdf" class="btn-result sigma-action-btn" type="button">
                <span class="sigma-action-icon">📄</span>
            </button>

            <button id="btnReturn" class="btn-result sigma-action-btn" type="button">
                Return
            </button>
        </div>
      </div>

      <div id="sigmaBracket" class="sigma-bracket"></div>
    </div>
  </section>
</main>
<footer class="sigma-footer" role="contentinfo">
  <div class="footer-name">Taekwondo Event &amp; Competition Management Platform</div>
  <div class="footer-quote">Connecting Passion · Honoring Sportsmanship · Shaping Taekwondo’s Future</div>
  <div>Created and Designed by <strong style="color:#fff">Nguyễn Thiên Phụng</strong></div>
  <div class="footer-contact">Contact: <a href="mailto:nhoctotokute93@gmail.com">nhoctotokute93@gmail.com</a></div>
</footer>

<script>
// Resolve APIs under /Sigma-view/ on the public domain, while preserving
// normal root-relative API calls during localhost development.
const SIGMA_PUBLIC_PREFIX = {{ event_prefix|tojson }};
function sigmaFetch(resource, options){
    const url = typeof resource === 'string' && resource.startsWith('/api/')
        ? SIGMA_PUBLIC_PREFIX + resource : resource;
    return window.fetch(url, options);
}



// ===== Match Timelines: menu-integrated, event-scoped =====
const TimelineWeb = (()=>{
  const byId=id=>document.getElementById(id);
  const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const get=(row,...keys)=>{for(const k of keys){const v=row?.[k];if(v!==undefined&&v!==null&&v!=='')return v;}return '';};
  let opened=false,activeMode='',data=null,page=0,refreshTimer=null,pageTimer=null,clockTimer=null,version=0;
  // Court 10 uses 0 as the second digit of the 5-digit match number.
  const maxPages=()=>Math.max(1,Math.ceil((data?.max_court||0)/5));
  const isWinner=(name,noc)=>/\bWINNER\b/i.test(`${name||''} ${noc||''}`);
  function reducedDivision(row){
    let s=String(row?.Division||'');
    if(row?.discipline==='Kyorugi' && /^individual\b/i.test(s) && /[+-]\s*\d/.test(s)) s=s.replace(/^individual\s+/i,'');
    return s;
  }
  const nocCountry={VIE:'vn',USA:'us',KOR:'kr',EGY:'eg',KAZ:'kz',INA:'id',NEP:'np',ARM:'am',MGL:'mn',PHI:'ph',CAN:'ca',IRI:'ir',IRN:'ir',CHN:'cn',CHN2:'cn',LAO:'la',ESP:'es',TPE:'tw',THA:'th',MAS:'my',SGP:'sg',JPN:'jp',GBR:'gb',FRA:'fr',GER:'de',NED:'nl',AUS:'au',NZL:'nz',IND:'in',PAK:'pk',UZB:'uz',TKM:'tm',KGZ:'kg',TJK:'tj',TUR:'tr',BRA:'br',MEX:'mx',ITA:'it',UKR:'ua',POL:'pl',BEL:'be',SWE:'se',NOR:'no',DEN:'dk',FIN:'fi',SUI:'ch',AUT:'at',GRE:'gr',POR:'pt',CZE:'cz',HUN:'hu',ISR:'il',UAE:'ae',KSA:'sa',QAT:'qa',JOR:'jo',IRQ:'iq',CAM:'kh',MYA:'mm',BAN:'bd',SRI:'lk',HKG:'hk',MAC:'mo',AFG:'af',RSA:'za',NGR:'ng',TUN:'tn',MAR:'ma',ARG:'ar',CHI:'cl',COL:'co',PER:'pe',CUB:'cu',PUR:'pr'};
  function flag(noc){
    const name=String(noc||'').trim().toUpperCase().split(/[\s(\-\d]/)[0];
    const cc=nocCountry[name];
    if(!cc)return '';
    // Public SVG flag assets, not Supabase storage or credentials.
    return `<img alt="${escapeHtml(name)}" loading="lazy" src="https://flagcdn.com/w80/${cc}.png" onerror="this.style.display='none'">`;
  }
  function sideIdentity(row,side){
    const blue=side==='Blue', noc=String(get(row,'NOC '+side)), name=String(get(row,'Athlete '+side));
    if(isWinner(name,noc))return {noc:'',name:'Waiting...',flag:''};
    return {noc,name,flag:flag(noc)};
  }
  function nextLine(row){
    if(!row)return `<div class="timeline-next-line"><div class="tl-blue"></div><div class="tl-blue"></div><div class="tl-blue"></div><div class="tl-mid"></div><div class="tl-red"></div><div class="tl-red"></div><div class="tl-red"></div></div>`;
    const b=sideIdentity(row,'Blue'),r=sideIdentity(row,'Red');
    return `<div class="timeline-next-line">
      <div class="tl-blue timeline-flag">${b.flag}</div><div class="tl-blue tl-noc"><span class="tl-fit-text">${escapeHtml(b.noc)}</span></div>
      <div class="tl-blue tl-name ${b.name==='Waiting...'?'tl-wait':''}"><span class="tl-fit-text">${escapeHtml(b.name)}</span></div>
      <div class="tl-mid"><span class="tl-fit-text">${escapeHtml(row.Match)} • ${escapeHtml(row.discipline)}<br>${escapeHtml(reducedDivision(row))}</span></div>
      <div class="tl-red tl-name ${r.name==='Waiting...'?'tl-wait':''}"><span class="tl-fit-text">${escapeHtml(r.name)}</span></div>
      <div class="tl-red tl-noc"><span class="tl-fit-text">${escapeHtml(r.noc)}</span></div><div class="tl-red timeline-flag">${r.flag}</div>
      </div>`;
  }
  function formatScore(v){if(v===null||v===undefined||v==='')return '';const n=Number(v);return Number.isFinite(n)?n.toFixed(3):String(v);}
  function poomsaeScores(row,side){
    const results=[];
    for(const index of [1,2]){
      const vals=[get(row,`${side} Tech ${index}`),get(row,`${side} Pre ${index}`),get(row,`${side} Point ${index}`)];
      if(vals.some(v=>v!==''))results.push({label:'P'+index,text:`P${index}: ${vals.map(formatScore).join(' | ')}`});
    }
    const totals=[get(row,`Total Tech ${side}`),get(row,`Total Pre ${side}`),get(row,`Total Point ${side}`)];
    if(results.length>=2 && totals.some(v=>v!==''))results.push({label:'TT',text:'TT: '+totals.map(formatScore).join(' | ')});
    return results;
  }
  function pairScore(v,side){
    if(v===null||v===undefined||v==='')return '';
    let raw=v;
    if(typeof raw==='string'){
      try{raw=JSON.parse(raw);}catch(_){const m=raw.match(/^\s*(\d+)\s*[:|/]\s*(\d+)\s*$/);if(m)return m[side==='Blue'?1:2];return '';}
    }
    if(Array.isArray(raw))return raw[side==='Blue'?0:1]??'';
    if(typeof raw==='object'){
      const props=Object.entries(raw),keys=(side==='Blue'?['blue','cheong']:['red','hong']);
      for(const [key,val] of props){if(keys.some(k=>key.toLowerCase().replace(/[^a-z]/g,'').includes(k))){
        if(val&&typeof val==='object')return val.score??val.points??val.point??'';
        return val;
      }}
    }
    return '';
  }
  function kyorugiScores(row,side){
    const mode=String(get(row,'Classification','Method','Style')).toLowerCase();
    const total=/total|gold|3 round/.test(mode);
    const result=[];
    for(const n of [1,2,3]){
      const raw=get(row,`Scr.R${n}`);
      let score=get(row,`${side} R${n}`,`${side} Round ${n}`,`${side} Score R${n}`) || pairScore(raw,side);
      if(score==='')continue;
      if(!total && !/^\s*[01]\s*\(/.test(String(score))){
        const b=pairScore(raw,'Blue'),r=pairScore(raw,'Red');
        if(b!==''&&r!==''&&Number(b)!==Number(r))score=`${Number(side==='Blue'?Number(b)>Number(r):Number(r)>Number(b))} (${score})`;
      }
      result.push({label:`R${n}`,text:`R${n}: ${score}`});
    }
    if(total){const gold=pairScore(get(row,'Scr.RG'),side);if(gold!=='')result.push({label:'GOLD',text:'GOLD: '+gold});}
    return result;
  }
  function currentSide(row,side){
    const id=sideIdentity(row,side),cls=side==='Blue'?'timeline-current-blue':'timeline-current-red';
    const points=row?(row.discipline==='Poomsae'?poomsaeScores(row,side):kyorugiScores(row,side)):[];
    const flagHtml=id.flag?`<span class="timeline-flag">${id.flag}</span>`:'';
    return `<div class="timeline-current-side ${cls}"><div class="timeline-identity">${side==='Blue'?flagHtml:''}<span>${escapeHtml(id.noc)}</span>${side==='Red'?flagHtml:''}</div>
      <div class="timeline-athlete"><span class="tl-fit-text">${escapeHtml(id.name)}</span></div><div class="timeline-score-lines">${points.map(p=>`<div class="timeline-score-line ${p.label==='TT'?'timeline-total':''}"><span class="tl-fit-text">${escapeHtml(p.text)}</span></div>`).join('')}</div></div>`;
  }
  function groupForCourt(court){
    const entries=(data?.matches||[]).filter(r=>r.court===court);
    const running=entries.filter(r=>r.state==='running');
    const current=running[0]||null;
    const upcoming=entries.filter(r=>r.state==='waiting' && r!==current).slice(0,3);
    return {current,upcoming,conflict:running.length>1};
  }
  function courtRow(court){
    const {current,upcoming}=groupForCourt(court);
    const head=current?`COURT ${court}  |  CURRENT MATCH #${escapeHtml(current.Match)}`:`COURT ${court}  |  CURRENT MATCH`;
    const status=current?`${escapeHtml(current.discipline.toUpperCase())} • ${escapeHtml(current.Phase||'')} • ${escapeHtml(reducedDivision(current))}`:'WAITING FOR MATCH';
    return `<div class="timeline-court-row">
      <div class="timeline-panel"><div class="timeline-panel-head">COURT ${court} &nbsp; | &nbsp; NEXT MATCHES</div><div class="timeline-next-table">${[0,1,2].map(i=>nextLine(upcoming[i])).join('')}</div></div>
      <div class="timeline-panel timeline-current"><div class="timeline-panel-head">${head}</div><div class="timeline-current-status">${status}</div>
      <div class="timeline-current-sides">${currentSide(current,'Blue')}${currentSide(current,'Red')}</div></div>
    </div>`;
  }
  // Measure real browser layout: shrink font only when text exceeds the cell.
  // All labels are recreated on refresh, so font sizes never accumulate reductions.
  let fitFrame=0;
  function fitTimelineText(){
    fitFrame=0;
    if(!opened || activeMode!=='matches')return;
    const stack=byId('timelineCourtStack');
    if(!stack)return;
    stack.querySelectorAll('.tl-fit-text').forEach(el=>{
      const cell=el.parentElement;
      if(!cell || !el.textContent.trim())return;
      const cellStyle=window.getComputedStyle(cell);
      const rect=cell.getBoundingClientRect();
      const usableHeight=Math.max(5,rect.height - (parseFloat(cellStyle.paddingTop)||0) - (parseFloat(cellStyle.paddingBottom)||0) - 2);
      const usableWidth=Math.max(8,rect.width - (parseFloat(cellStyle.paddingLeft)||0) - (parseFloat(cellStyle.paddingRight)||0) - 2);
      const original=Number.parseFloat(cellStyle.fontSize)||12;
      const minimum=7;
      el.style.fontSize=original+'px';
      el.style.width=usableWidth+'px';
      for(let size=original;size>=minimum;size-=0.5){
        el.style.fontSize=size+'px';
        if(el.scrollHeight<=usableHeight+1 && el.scrollWidth<=usableWidth+1)break;
      }
    });
  }
  function scheduleFit(){
    if(fitFrame)cancelAnimationFrame(fitFrame);
    fitFrame=requestAnimationFrame(fitTimelineText);
  }
  // Observe only the stable stack, not children whose font sizes change.
  const timelineFitObserver=typeof ResizeObserver!=='undefined'?new ResizeObserver(scheduleFit):null;
  if(timelineFitObserver){
    const stack=byId('timelineCourtStack');
    if(stack)timelineFitObserver.observe(stack);
  }
  if(document.fonts?.ready)document.fonts.ready.then(scheduleFit);
  function render(animate=false){
    if(!opened||activeMode!=='matches'||!data)return;
    const pages=maxPages();page=page%pages;
    const low=page*5+1,high=low+4;
    const info=`DAY ${data.day} &nbsp; | &nbsp; COURTS ${low}–${Math.min(high,Math.max(data.max_court,5))} &nbsp; | &nbsp; PAGE ${page+1}/${pages}`;
    byId('timelineDayInfo').innerHTML=info;
    const warns=[...(data.warnings||[])];
    if((data.conflicts||[]).length)warns.push('Trùng mã trận giữa hai môn: '+data.conflicts.slice(0,5).join(', '));
    byId('timelineAlert').textContent=warns.join('  •  ');
    const stack=byId('timelineCourtStack');
    stack.innerHTML=Array.from({length:5},(_,i)=>courtRow(low+i)).join('');
    if(animate)stack.querySelectorAll('.timeline-court-row').forEach(el=>el.classList.add('timeline-row-wave'));
    scheduleFit();
  }
  async function refresh(){
    if(!opened||activeMode!=='matches')return;
    const turn=version;
    try{
      const response=await sigmaFetch('/api/timeline/matches',{cache:'no-store'});
      const payload=await response.json();
      if(!response.ok)throw new Error(payload.error||`HTTP ${response.status}`);
      if(turn!==version||!opened)return;
      data=payload;
      if(page>=maxPages())page=0;
      render();
    }catch(error){if(turn===version&&opened)byId('timelineAlert').textContent='DATA ERROR: '+error.message;}
  }
  function resizeBoard(){
    if(!opened||activeMode!=='matches')return;
    const top=document.querySelector('.topbar');
    if(top)document.documentElement.style.setProperty('--timeline-topbar-height',`${Math.ceil(top.getBoundingClientRect().height)}px`);
    scheduleFit();
  }
  window.addEventListener('resize',resizeBoard);
  function tickSharedHeaderClock(){
    const d=new Date();const options={timeZone:'Asia/Ho_Chi_Minh'};
    const clock=byId('siteHeaderClockTime'),date=byId('siteHeaderClockDate');
    if(clock)clock.textContent=d.toLocaleTimeString('en-GB',{...options,hour12:false});
    if(date)date.textContent=d.toLocaleDateString('en-GB',options);
  }
  tickSharedHeaderClock();setInterval(tickSharedHeaderClock,1000);
  // Division Timeline is independently published in Supabase by the Referee Admin.
  let sdDay=1, sdData=null;
  const sdEsc=escapeHtml;
  const sdAssignments=(v)=>Array.isArray(v)?v.filter(x=>x&&typeof x==='object'):(v&&typeof v==='object'?[v]:[]);
  function sdDisplayType(a){
 const explicit=String(a?.display_type||'').toLowerCase();
 const hint=String(a?.division||'').toLowerCase();
 const discipline=String(a?.discipline||'').toLowerCase();
 if(discipline==='kyeorugi'||discipline==='kyorugi')return 'Kyorugi';
 if(explicit.includes('freestyle')||hint.includes('freestyle'))return 'Freestyle';
 if(explicit.includes('demonstration')||hint.includes('demonstration'))return 'Demonstration';
 return 'Poomsae';
}
  function sdDraw(){
    if(!opened||activeMode!=='schedule'||!sdData)return;
    const days=Math.max(1,Math.min(31,Number(sdData.days)||1));
    sdDay=Math.min(Math.max(1,sdDay),days);
    const tabs=byId('sdDays');
    tabs.innerHTML=Array.from({length:days},(_,i)=>'<button type="button" class="sd-day'+(sdDay===i+1?' active':'')+'" data-day="'+(i+1)+'" role="tab" aria-selected="'+(sdDay===i+1)+'">Day '+(i+1)+'</button>').join('');
    tabs.querySelectorAll('[data-day]').forEach(b=>b.addEventListener('click',()=>{sdDay=Number(b.dataset.day);sdDraw()}));
    const board=byId('sdBoard'),status=byId('sdStatus');
    if(sdData.mode==='managed'){
      const slots=(sdData.slots||[]).filter(s=>Number(s.day)===sdDay).sort((a,b)=>String(a.start||'').localeCompare(String(b.start||''))||String(a.end||'').localeCompare(String(b.end||'')));
      const fromAssign=slots.flatMap(s=>Object.keys(s.assignments||{}).map(Number));
      const fromSaved=Number(sdData.court_max_by_day?.[String(sdDay)]||0);
      const courts=Math.min(10,Math.max(1,fromSaved,...fromAssign.filter(x=>Number.isInteger(x)&&x>=1&&x<=10)));
      board.style.setProperty('--sd-courts',String(courts));
      status.textContent='Day '+sdDay+' · '+slots.filter(s=>!['lunch','break'].includes(s.kind)).length+' time slots · '+courts+' Courts';
      let html='<div class="sd-head"><div>Time</div>'+Array.from({length:courts},(_,i)=>'<div>Court '+(i+1)+'</div>').join('')+'</div>';
      const lunch=slots.some(s=>s.kind==='lunch');let fallback=false;
      for(const slot of slots){
        const kind=slot.kind||'match';
        if(!lunch&&!fallback&&slot.session==='Evening'){html+='<div class="sd-break lunch">LUNCH TIME</div>';fallback=true;}
        if(kind==='lunch'||kind==='break'){
          html+='<div class="sd-break '+(kind==='lunch'?'lunch':'break')+'">'+(kind==='lunch'?'LUNCH TIME':'BREAK TIME')+' · '+sdEsc(slot.start)+'–'+sdEsc(slot.end)+'</div>';continue;
        }
        html+='<div class="sd-row"><div class="sd-time">'+sdEsc(slot.start)+'–'+sdEsc(slot.end)+'</div>';
        for(let c=1;c<=courts;c++){
          const entries=sdAssignments((slot.assignments||{})[String(c)]);
          html+='<div class="sd-cell'+(entries.length?' booked':'')+'">'+entries.map(a=>'<div class="sd-entry"><strong>'+sdEsc(a.phase)+' - '+sdEsc(sdDisplayType(a))+'</strong><small>'+sdEsc(a.division)+'</small></div>').join('')+'</div>';
        }
        html+='</div>';
      }
      if(!slots.length)html+='<div class="sd-empty">Chưa có lịch thi đấu cho Day '+sdDay+'. Ban tổ chức sẽ cập nhật lịch tại đây.</div>';
      board.innerHTML=html;
    }else{
      const entries=(sdData.items||[]).filter(r=>Number(r.day_number)===sdDay);
      const courts=10;board.style.setProperty('--sd-courts',String(courts));
      status.textContent='Day '+sdDay+' · '+entries.length+' scheduled divisions · 10 Courts';
      let html='<div class="sd-head"><div>Time</div>'+Array.from({length:10},(_,i)=>'<div>Court '+(i+1)+'</div>').join('')+'</div>';
      for(const [part,minimum] of [['Morning',5],['Evening',6]]){
        const rows=entries.filter(x=>x.session===part);
        const count=Math.max(minimum,...rows.map(x=>Number(x.start_slot||1)+Number(x.slot_span||1)-1));
        for(let k=1;k<=count;k++){
          html+='<div class="sd-row"><div class="sd-time">'+(k===1?part:'')+'</div>';
          for(let c=1;c<=10;c++){
            const hit=rows.filter(x=>Number(x.court)===c&&Number(x.start_slot)<=k&&k<Number(x.start_slot)+Number(x.slot_span||1));
            html+='<div class="sd-cell'+(hit.length?' booked':'')+'">'+hit.map(x=>'<div class="sd-entry">'+sdEsc(x.division)+'</div>').join('')+'</div>';
          }
          html+='</div>';
        }
        if(part==='Morning')html+='<div class="sd-break lunch">LUNCH TIME</div>';
      }
      if(!entries.length)html+='<div class="sd-empty">Chưa có lịch thi đấu cho Day '+sdDay+'. Ban tổ chức sẽ cập nhật lịch tại đây.</div>';
      board.innerHTML=html;
    }
  }
  async function sdRefresh(){
    const generation=version;byId('sdStatus').textContent='Loading Schedule Division...';
    try{
      const response=await sigmaFetch('/api/division-timelines',{credentials:'same-origin',cache:'no-store'});
      const payload=await response.json();
      if(!response.ok)throw new Error(payload.error||'HTTP '+response.status);
      if(!opened||activeMode!=='schedule'||generation!==version)return;
      sdData=payload;sdDraw();
    }catch(e){if(!opened||activeMode!=='schedule'||generation!==version)return;
      byId('sdStatus').textContent='Không tải được Schedule Division: '+e.message;
      byId('sdBoard').innerHTML='';}
  }
  byId('sdRefresh')?.addEventListener('click',()=>{sdData=null;sdRefresh()});
  function stop(){opened=false;activeMode='';version++;clearInterval(refreshTimer);clearInterval(pageTimer);refreshTimer=pageTimer=null;document.body.classList.remove('timeline-screen-open');document.querySelector('.layout')?.classList.remove('timeline-active');byId('timelineView')?.classList.add('hidden');}
  function open(mode,button){
    stop();hideMainDivisionViews();
    ['infoListView','rankingView','kyeorugiRankingView','medalStandingsView'].forEach(id=>byId(id)?.classList.add('hidden'));
    clearQuickMenuActive();byId(button)?.classList.add('active');
    opened=true;activeMode=mode;byId('timelineView').classList.remove('hidden');
    byId('timelineSchedulePlaceholder').classList.toggle('hidden',mode!=='schedule');
    byId('timelineMatchesContent').classList.toggle('hidden',mode!=='matches');
    if(mode==='schedule'){sdData=null;byId('sdBoard').innerHTML='';sdRefresh();}
    if(mode==='matches'){
      data=null;page=0;byId('timelineCourtStack').innerHTML='';byId('timelineAlert').textContent='Loading Match Timelines...';
      document.body.classList.add('timeline-screen-open');document.querySelector('.layout')?.classList.add('timeline-active');resizeBoard();requestAnimationFrame(resizeBoard);
      refresh();refreshTimer=setInterval(refresh,8000);
      pageTimer=setInterval(()=>{if(data&&maxPages()>1){page=(page+1)%maxPages();render(true);}},10000);
    }
    if(typeof closeSidebarOnMobile==='function')closeSidebarOnMobile();
  }
  byId('btnScheduleDivisionTimeline')?.addEventListener('click',()=>open('schedule','btnScheduleDivisionTimeline'));
  byId('btnMatchTimeline')?.addEventListener('click',()=>open('matches','btnMatchTimeline'));
  byId('timelineRefresh')?.addEventListener('click',refresh);
  window.addEventListener('pagehide',stop);
  return {stop};
})();

let CURRENT_KEY = "";
let FLAT = [];
let CURRENT_RESULTS = [];
let CURRENT_POOMSAE_MATCHES = [];
let CURRENT_POOMSAE_MATCH_ERROR = '';

let INFORMATION_ROWS = [];
let ALL_RESULTS_ROWS = [];
let RANKING_CARDS = [];
let MEDAL_STANDINGS_ROWS = [];
let MEDAL_STANDINGS_DIVISIONS = [];

function clearQuickMenuActive(){
    document.querySelectorAll(".quickMenuBtn").forEach(b => b.classList.remove("active"));
}

function hideMainDivisionViews(){
    if(typeof TimelineWeb !== 'undefined') TimelineWeb.stop();
    document.getElementById('kyeorugiRankingView')?.classList.add('hidden');
    const selectedBox = document.querySelector(".selectedBox");
    const athleteCard = document.querySelector(".athlete-card");
    const sigmaView = document.getElementById("sigmaView");
    const medalStandingsView = document.getElementById("medalStandingsView");
    const layout = document.querySelector(".layout");

    if(layout) layout.classList.remove("result-mode");
    if(selectedBox) selectedBox.classList.add("hidden");
    if(athleteCard) athleteCard.classList.add("hidden");
    if(sigmaView) sigmaView.classList.add("hidden");
    if(medalStandingsView) medalStandingsView.classList.add("hidden");
}

function showMainDivisionViews(){
    if(typeof TimelineWeb !== 'undefined') TimelineWeb.stop();
    document.getElementById('kyeorugiRankingView')?.classList.add('hidden');
    const selectedBox = document.querySelector(".selectedBox");
    const athleteCard = document.querySelector(".athlete-card");
    const infoListView = document.getElementById("infoListView");
    const rankingView = document.getElementById("rankingView");
    const medalStandingsView = document.getElementById("medalStandingsView");

    if(infoListView) infoListView.classList.add("hidden");
    if(rankingView) rankingView.classList.add("hidden");
    if(medalStandingsView) medalStandingsView.classList.add("hidden");

    if(selectedBox) selectedBox.classList.remove("hidden");
    if(athleteCard) athleteCard.classList.remove("hidden");

    clearQuickMenuActive();
}

function showInfoListView(){
    document.getElementById('kyeorugiRankingView')?.classList.add('hidden');
    hideMainDivisionViews();

    const infoListView = document.getElementById("infoListView");
    const rankingView = document.getElementById("rankingView");
    const medalStandingsView = document.getElementById("medalStandingsView");

    if(infoListView) infoListView.classList.remove("hidden");
    if(rankingView) rankingView.classList.add("hidden");
    if(medalStandingsView) medalStandingsView.classList.add("hidden");

    clearQuickMenuActive();
    document.getElementById("btnOpenInfoList")?.classList.add("active");

    loadInformationList();
}

function showRankingView(){
    document.getElementById('kyeorugiRankingView')?.classList.add('hidden');
    hideMainDivisionViews();

    const infoListView = document.getElementById("infoListView");
    const rankingView = document.getElementById("rankingView");
    const medalStandingsView = document.getElementById("medalStandingsView");

    if(infoListView) infoListView.classList.add("hidden");
    if(rankingView) rankingView.classList.remove("hidden");
    if(medalStandingsView) medalStandingsView.classList.add("hidden");

    clearQuickMenuActive();
    document.getElementById("btnOpenRanking")?.classList.add("active");

    loadRankingSummary();
}

function showKyeorugiRankingView(){
    hideMainDivisionViews();
    document.getElementById('infoListView')?.classList.add('hidden');
    document.getElementById('rankingView')?.classList.add('hidden');
    document.getElementById('medalStandingsView')?.classList.add('hidden');
    document.getElementById('kyeorugiRankingView')?.classList.remove('hidden');
    clearQuickMenuActive();
    document.getElementById('btnOpenKyeorugiRanking')?.classList.add('active');
    loadKyeorugiRanking();
}
let K_RANKING_CARDS = [];
async function loadKyeorugiRanking(){
    const status=document.getElementById('kRankingStatus');
    if(status) status.textContent='Đang tải Kyeorugi Matchs...';
    try{
        const response=await sigmaFetch('/api/kyeorugi/ranking', {cache:'no-store'});
        const body=await response.json();
        if(!response.ok) throw new Error(body.error || 'HTTP '+response.status);
        K_RANKING_CARDS=body.cards || [];
        if(status) status.textContent=K_RANKING_CARDS.length+' divisions đã có lịch thi đấu';
    }catch(err){
        K_RANKING_CARDS=[];
        if(status) status.textContent='Không thể tải Ranking: '+err.message;
    }
    renderKyeorugiRanking();
}
function renderKyeorugiRanking(){
    const box=document.getElementById('kRankingCards');
    if(!box) return;
    const search=normalizeUpper(document.getElementById('kRankingSearch')?.value || '');
    const medal=document.getElementById('kRankingMedal')?.value || '';
    const cards=K_RANKING_CARDS.filter(c=>!search || normalizeUpper([c.title,...c.medals.flatMap(m=>[m.noc,m.name])].join(' ')).includes(search));
    box.innerHTML=cards.length?cards.map(c=>`<div class="ranking-division-card">
       <div class="ranking-division-title">${esc(c.title)}
          <div style="font-size:12px;color:#cfe6ff;margin-top:4px">${c.completed} / ${c.matches} matches completed</div>
       </div><table class="ranking-medal-table"><thead><tr><th>Medal</th><th>NOC</th><th>${c.is_team ? "Country" : "Full name"}</th></tr></thead>
       <tbody>${c.medals.filter(m=>!medal || m.medal===medal).map(m=>`<tr>
         <td class="medal-cell ${medalClass(m.medal)}">${esc(m.label)}</td>
         <td>${esc(m.noc||'-')}</td><td>${esc(m.name||'-')}</td>
       </tr>`).join('')}</tbody></table></div>`).join(''):
       '<div class="empty">Chưa có Division có lịch thi đấu phù hợp</div>';
}

function showMedalStandingsView(){
    document.getElementById('kyeorugiRankingView')?.classList.add('hidden');
    hideMainDivisionViews();

    const infoListView = document.getElementById("infoListView");
    const rankingView = document.getElementById("rankingView");
    const medalStandingsView = document.getElementById("medalStandingsView");

    if(infoListView) infoListView.classList.add("hidden");
    if(rankingView) rankingView.classList.add("hidden");
    if(medalStandingsView) medalStandingsView.classList.remove("hidden");

    clearQuickMenuActive();
    document.getElementById("btnOpenMedalStandings")?.classList.add("active");

    loadMedalStandings();
}

function getRowAny(row, keys){
    for(const k of keys){
        if(row && row[k] !== undefined && row[k] !== null && row[k] !== ""){
            return row[k];
        }
    }
    return "";
}

function normalizeUpper(v){
    return String(v || "").trim().toUpperCase();
}

function getMetaMatchPrefix(meta){
    if(!meta) return "";

    const typeMap = {
        "Recognized":"R",
        "Freestyle":"F",
        "Demonstration":"D",
        "Mixer":"M"
    };

    const divMap = {
        "Individual":"I",
        "Pair":"P",
        "Team":"T"
    };

    const genderMap = {
        "Male":"M",
        "Female":"F"
    };

    const t = typeMap[meta.type] || "";
    const d = divMap[meta.division] || "";
    const g = genderMap[meta.gender] || "";

    let ageRaw = String(meta.age || "").trim().toUpperCase();

    const ageMap = {
        "CADET": "U14",
        "JUNIOR": "U17",
        "UNDER 14": "U14",
        "U 14": "U14",
        "U14": "U14",
        "UNDER 17": "U17",
        "U 17": "U17",
        "U17": "U17"
    };

    let age = ageMap[ageRaw] || ageRaw.replace(/\s+/g, "");

    return `${t}${d}${g}${age}`;
}

function getResultMatch(row){
    return normalizeUpper(getRowAny(row, ["Match", "match"]));
}

function getResultNocBlue(row){
    return String(getRowAny(row, ["NOC Blue", "NOC_Blue", "NOC blue", "noc_blue"])).trim();
}

function getResultNocRed(row){
    return String(getRowAny(row, ["NOC Red", "NOC_Red", "NOC red", "noc_red"])).trim();
}

function getResultWinnerNoc(row){
    const result = normalizeUpper(getRowAny(row, ["Result", "result", "Winner", "winner"]));
    const nocBlue = getResultNocBlue(row);
    const nocRed = getResultNocRed(row);

    // CHEONG = BLUE
    if(result.includes("CHEONG") || result.includes("BLUE")){
        return nocBlue;
    }

    // HONG = RED
    if(result.includes("HONG") || result.includes("RED")){
        return nocRed;
    }

    if(nocBlue && result.includes(nocBlue.toUpperCase())){
        return nocBlue;
    }

    if(nocRed && result.includes(nocRed.toUpperCase())){
        return nocRed;
    }

    return "";
}

function getResultLoserNoc(row){
    const winner = normalizeUpper(getResultWinnerNoc(row));
    const blue = getResultNocBlue(row);
    const red = getResultNocRed(row);

    if(!winner) return "";

    if(winner === normalizeUpper(blue)) return red;
    if(winner === normalizeUpper(red)) return blue;

    return "";
}

function getRowScore(row){
    const fields = [
        "Total Point Blue",
        "Blue Point 1",
        "Total",
        "Score",
        "score",
        "_score"
    ];

    for(const f of fields){
        const v = row[f];
        if(v !== undefined && v !== null && v !== ""){
            const n = Number(v);
            if(!Number.isNaN(n)) return n;
        }
    }

    return 0;
}

function getPhaseName(row){
    return String(getRowAny(row, ["Phase", "phase", "Round", "round"])).trim();
}

function placeLabel(place){
    if(Number(place) === 1) return `<span class="rank-gold">🥇 1</span>`;
    if(Number(place) === 2) return `<span class="rank-silver">🥈 2</span>`;
    if(Number(place) === 3) return `<span class="rank-bronze">🥉 3</span>`;
    if(Number(place) === 4) return `<span class="rank-fourth">🥉 4</span>`;
    return "";
}

async function loadInformationList(){
    const table = document.getElementById("infoListTable");
    if(table){
        table.innerHTML = `<tr><td class="empty">Đang tải Information List...</td></tr>`;
    }

    if(!FLAT || !FLAT.length){
        try{
            const menuData = await sigmaFetch("/api/menu").then(r => r.json());
            FLAT = menuData.flat || [];
        }catch(e){
            console.error(e);
        }
    }

    try{
        INFORMATION_ROWS = await sigmaFetch("/api/information-list").then(async r => {
            const data=await r.json();
            if(!r.ok) throw new Error(data.error || 'HTTP '+r.status);
            return data;
        });
    }catch(e){
        console.error(e);
        INFORMATION_ROWS = [];
        if(table) table.innerHTML=`<tr><td class="empty">${esc('Không thể tải Information Lists: '+e.message)}</td></tr>`;
        return;
    }

    buildInformationFilters();
    renderInformationList();
    // Weight options are rebuilt directly from the downloaded Event_* records.
}

// Individual weight categories come EXCLUSIVELY from Information Lists Event_1..Event_12.
// Do not use Kyeorugi Matchs or weighting.json to populate this dropdown.
const INFO_POOMSAE_DIVISIONS = [
    'Recognized Individual','Recognized Pair','Recognized Team',
    'Freestyle Individual','Freestyle Pair','Freestyle Team','Freestyle Mixed Team',
    'Demonstration','Breaking'
];
const INFO_TEAM_DIVISIONS = ['Team 3','Team 4','Team 5','Mixed Team'];
function onInfoCategoryChange(){
    document.getElementById('infoEventFilter').value='';
    document.getElementById('infoWeightFilter').value='';
    buildInformationFilters();renderInformationList();
}
function onInfoDivisionChange(){
    buildInfoWeightOptions();renderInformationList();
}
function infoAgeCode(value){
    const age=String(value||'').toUpperCase();
    if(/U9/.test(age))return 'U9';
    if(/U12/.test(age))return 'U12';
    if(/U14|CADET/.test(age))return 'U14';
    if(/U17|JUNIOR/.test(age))return 'U17';
    if(/O18|SENIOR/.test(age))return 'O18';
    return age;
}
function infoWeightKey(value){
    const m=String(value||'').match(/([+-])\s*(\d{1,3}(?:[.,]\d+)?)/);
    return m ? m[1]+String(Number(m[2].replace(',','.'))) : '';
}
function infoIndividualWeights(event){
    const raw=String(event||'');
    if(infoIsPoomsaeEvent(raw)||/\bteam\b/i.test(raw))return [];
    // "-38kg", "+38 KG", "- 38 Kg", "+-38kg"; ignore no-sign values.
    const weights=[];
    const found=raw.matchAll(/([+-])\s*(\d{1,3}(?:[.,]\d+)?)(?:\s*kg)?/gi);
    for(const hit of found){
        const key=infoWeightKey(hit[0]);
        if(key && !weights.includes(key))weights.push(key);
    }
    return weights;
}
function buildInfoWeightOptions(){
    const box=document.getElementById('infoWeightFilter');
    const category=document.getElementById('infoCategoryFilter')?.value;
    const division=document.getElementById('infoEventFilter')?.value;
    const selectedAge=document.getElementById('infoAgeFilter')?.value||'';
    const gender=normalizeUpper(document.getElementById('infoGenderFilter')?.value||'');
    if(!box)return;
    if(category!=='Kyeorugi'||division!=='Individual'){
        box.classList.add('hidden');box.innerHTML='<option value="">Weight: All</option>';return;
    }
    box.classList.remove('hidden');
    const previous=infoWeightKey(box.value);
    const weights=new Set();
    (INFORMATION_ROWS||[]).forEach(row=>{
        if(gender && normalizeUpper(getRowAny(row,['Gender','gender']))!==gender)return;
        if(selectedAge && !rowHasSelectedAge(row,selectedAge))return;
        for(const ev of getInfoRowEvents(row)){
            for(const weight of infoIndividualWeights(ev))weights.add(weight);
        }
    });
    const sorted=[...weights].sort((a,b)=>{
        const na=Number(a.slice(1)),nb=Number(b.slice(1));
        return na-nb || (a[0]==='-'?-1:1)-(b[0]==='-'?-1:1);
    });
    box.innerHTML='<option value="">Weight: All</option>'+sorted.map(w=>{
        const label=w+' kg';return `<option value="${esc(w)}">${esc(label)}</option>`;
    }).join('');
    box.value=sorted.includes(previous)?previous:'';
}
function buildInformationFilters(){
    const nocSelect=document.getElementById('infoNocFilter');
    const ageSelect=document.getElementById('infoAgeFilter');
    const eventSelect=document.getElementById('infoEventFilter');
    if(nocSelect){
        const previous=nocSelect.value;
        const countries=[...new Set(INFORMATION_ROWS.map(r=>String(getRowAny(r,['NOC','Country','noc','country'])).trim()).filter(Boolean))].sort();
        nocSelect.innerHTML='<option value="">Country</option>'+countries.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');
        nocSelect.value=countries.includes(previous)?previous:'';
    }
    if(ageSelect){
        const previous=ageSelect.value;
        const ages=new Set((FLAT||[]).map(m=>m.age).filter(Boolean));
        ['U9','U12','Cadet (U14)','Junior (U17)','Senior (O18)'].forEach(a=>ages.add(a));
        ageSelect.innerHTML='<option value="">Age</option>'+[...ages].sort((a,b)=>String(a).localeCompare(String(b),undefined,{numeric:true})).map(a=>`<option value="${esc(a)}">${esc(a)}</option>`).join('');
        ageSelect.value=ages.has(previous)?previous:'';
    }
    if(eventSelect){
        const previous=eventSelect.value;
        const category=document.getElementById('infoCategoryFilter')?.value||'';
        const kOptions=['Individual',...INFO_TEAM_DIVISIONS];
        const opts=category==='Kyeorugi'?kOptions:category==='Poomsae'?INFO_POOMSAE_DIVISIONS:[...INFO_POOMSAE_DIVISIONS,...kOptions];
        eventSelect.innerHTML='<option value="">Division: All</option>'+opts.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');
        eventSelect.value=opts.includes(previous)?previous:'';
    }
    buildInfoWeightOptions();
}
function infoIsPoomsaeEvent(event){return /\b(recognized|freestyle|demonstration|breaking|mixer)\b/i.test(event);}
function infoDivisionMatches(event, division, category){
    const text=String(event||'');
    if(category==='Poomsae'&&!infoIsPoomsaeEvent(text))return false;
    if(category==='Kyeorugi'&&infoIsPoomsaeEvent(text))return false;
    if(!division)return true;
    if(category==='Kyeorugi'){
        if(division==='Individual')return !/\bteam\b/i.test(text) && /[+-]\s*\d+/.test(text);
        if(division==='Mixed Team')return /\bmixed\s*team|team\s*mixed\b/i.test(text);
        return new RegExp('\\b'+division.replace(' ','\\s*')+'\\b','i').test(text);
    }
    return normText(text).includes(normText(division));
}


function normText(v){
    return String(v || "")
        .trim()
        .toUpperCase()
        .replace(/\s*-\s*/g, " ")
        .replace(/\s+/g, " ");
}

function getInfoRowEvents(row){
    const events = [];

    Object.keys(row || {}).forEach(k => {
        if(String(k).toLowerCase().startsWith("event")){
            const v = String(row[k] || "").trim();
            if(v) events.push(v);
        }
    });

    return events;
}

function rowHasSelectedDivision(row, selectedDivision){
    if(!selectedDivision) return true;

    const selected = normText(selectedDivision);
    const rowEvents = getInfoRowEvents(row);

    return rowEvents.some(ev => {
        const e = normText(ev);
        return e === selected || e.includes(selected) || selected.includes(e);
    });
}

function getBirthYearFromDob(dob){
    const s = String(dob || "").trim();
    if(!s) return null;

    const m = s.match(/^(\d{4})/);
    if(!m) return null;

    const y = Number(m[1]);
    if(Number.isNaN(y)) return null;

    return y;
}

function getAgeFromRow(row){
    const dob = getRowAny(row, ["DOB", "dob", "Birthdate", "birthdate", "Date of Birth", "date_of_birth"]);
    const y = getBirthYearFromDob(dob);

    if(!y) return null;

    // Ken đang set event năm 2026
    return 2026 - y;
}

function rowHasSelectedAge(row, selectedAge){
    if(!selectedAge) return true;

    const ageText = normText(selectedAge);
    const age = getAgeFromRow(row);

    // Nếu không có DOB thì không loại, để tránh mất VĐV do dữ liệu thiếu
    if(age === null) return true;

    if(ageText === "U9" || ageText.includes("UNDER 9"))return age <= 9;
    if(ageText === "U12" || ageText.includes("UNDER 12"))return age <= 12;
    if(ageText === "O18" || ageText.includes("SENIOR") || ageText.includes("OVER 18"))return age >= 18;

    if(ageText.includes("CADET")){
        return age >= 12 && age <= 14;
    }

    if(ageText.includes("JUNIOR")){
        return age >= 15 && age <= 17;
    }

    if(ageText.includes("U14") || ageText.includes("UNDER 14")){
        return age <= 14;
    }

    if(ageText.includes("U17") || ageText.includes("UNDER 17")){
        return age <= 17;
    }

    if(ageText.includes("O17") || ageText.includes("OVER 17")){
        return age > 17;
    }

    if(ageText.includes("U30") || ageText.includes("UNDER 30")){
        return age <= 30;
    }

    if(ageText.includes("U40") || ageText.includes("UNDER 40")){
        return age <= 40;
    }

    if(ageText.includes("U50") || ageText.includes("UNDER 50")){
        return age <= 50;
    }

    if(ageText.includes("U60") || ageText.includes("UNDER 60")){
        return age <= 60;
    }

    if(ageText.includes("U65") || ageText.includes("UNDER 65")){
        return age <= 65;
    }

    if(ageText.includes("O65") || ageText.includes("OVER 65")){
        return age > 65;
    }

    return true;
}


function renderInformationList(){
    const table = document.getElementById("infoListTable");
    if(!table) return;

    const search = normalizeUpper(document.getElementById("infoSearch")?.value || "");
    const nocFilter = normalizeUpper(document.getElementById("infoNocFilter")?.value || "");
    const genderFilter = normalizeUpper(document.getElementById("infoGenderFilter")?.value || "");
    const ageFilter = String(document.getElementById("infoAgeFilter")?.value || "").trim();
    const eventFilter = String(document.getElementById("infoEventFilter")?.value || "").trim();
    const categoryFilter=String(document.getElementById('infoCategoryFilter')?.value||'');
    const weightFilter=infoWeightKey(document.getElementById('infoWeightFilter')?.value||'');


    let rows = [...INFORMATION_ROWS];

    rows = rows.filter(r => {
        const allText = normalizeUpper(Object.values(r || {}).join(" "));
        const noc = normalizeUpper(getRowAny(r, ["NOC", "noc", "Country", "country", "Team", "team"]));
        const gender = normalizeUpper(getRowAny(r, ["Gender", "gender"]));

        if(search && !allText.includes(search)) return false;
        if(nocFilter && noc !== nocFilter) return false;
        if(genderFilter && gender !== genderFilter) return false;
        if(ageFilter && !rowHasSelectedAge(r, ageFilter)) return false;
        if(categoryFilter || eventFilter || weightFilter){
            const events=getInfoRowEvents(r);
            if(!events.some(ev=>infoDivisionMatches(ev,eventFilter,categoryFilter) &&
                    (!weightFilter || infoIndividualWeights(ev).includes(weightFilter))))return false;
        }

        return true;
    });

    if(!rows.length){
        table.innerHTML = `<tr><td class="empty">Không có dữ liệu phù hợp</td></tr>`;
        return;
    }

    // Fixed requested order; Event_1 through Event_12 always stay adjacent.
    // Missing columns are retained with blank cells, never silently reordered.
    const preferred = [
        'NOC', 'Country', 'GAL/ID ATHLETE', 'Full name', 'DOB',
        'Gender', 'Position',
        ...Array.from({length:12}, (_,i)=>'Event_'+(i+1))
    ];
    const cols = [...preferred];
    // Preserve all other Supabase columns AFTER the requested block.
    const hiddenColumns = [
        'Gen. Weigh-in 1',
        'Gen. Weigh-in 2',
        'Random Weigh-in'
    ];

    const additional = [...new Set(rows.flatMap(r=>Object.keys(r||{})))].filter(c =>
        !cols.includes(c) &&
        !String(c).startsWith('_') &&
        !['id','idx'].includes(String(c).toLowerCase()) &&
        !hiddenColumns.some(h => h.toLowerCase() === String(c).trim().toLowerCase())
    ).sort((a,b)=>String(a).localeCompare(String(b)));
    cols.push(...additional);

    table.innerHTML =
        `<thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead>` +
        `<tbody>${rows.map(r => `
            <tr>
                ${cols.map(c => `<td>${esc(r[c] ?? "")}</td>`).join("")}
            </tr>
        `).join("")}</tbody>`;
}

async function loadRankingSummary(){
    const box = document.getElementById("rankingCards");
    if(box){
        box.innerHTML = `<div class="empty">Đang tổng hợp Ranking...</div>`;
    }

    // Ranking bắt buộc lấy division từ file Sigma JSON
    // tức là lấy từ /api/menu -> FLAT
    try{
        const menuData = await sigmaFetch("/api/menu").then(r => r.json());
        FLAT = menuData.flat || [];
    }catch(e){
        console.error("Load sigma divisions error:", e);
        FLAT = [];
    }

    try{
        ALL_RESULTS_ROWS = await sigmaFetch("/api/all-results").then(r => r.json());
    }catch(e){
        console.error("Load all results error:", e);
        ALL_RESULTS_ROWS = [];
    }

    buildRankingCards();
    renderRankingSummary();
}

function getResultAthleteBlue(row){
    return String(getRowAny(row, [
        "Athlete Blue",
        "Athlete_Blue",
        "Full name Blue",
        "Full_name_Blue",
        "Full name",
        "Full_name",
        "Athlete",
        "athlete"
    ])).trim();
}

function getResultAthleteRed(row){
    return String(getRowAny(row, [
        "Athlete Red",
        "Athlete_Red",
        "Full name Red",
        "Full_name_Red"
    ])).trim();
}

function getResultWinnerSide(row){
    const result = normalizeUpper(getRowAny(row, ["Result", "result", "Winner", "winner"]));
    const nocBlue = normalizeUpper(getResultNocBlue(row));
    const nocRed = normalizeUpper(getResultNocRed(row));

    // CHEONG = BLUE
    if(result.includes("CHEONG") || result.includes("BLUE")){
        return "blue";
    }

    // HONG = RED
    if(result.includes("HONG") || result.includes("RED")){
        return "red";
    }

    if(nocBlue && result.includes(nocBlue)){
        return "blue";
    }

    if(nocRed && result.includes(nocRed)){
        return "red";
    }

    return "";
}

function getWinnerCompetitor(row){
    const side = getResultWinnerSide(row);

    if(side === "blue"){
        return {
            noc:getResultNocBlue(row),
            name:getResultAthleteBlue(row)
        };
    }

    if(side === "red"){
        return {
            noc:getResultNocRed(row),
            name:getResultAthleteRed(row)
        };
    }

    return {
        noc:"",
        name:""
    };
}

function getLoserCompetitor(row){
    const side = getResultWinnerSide(row);

    if(side === "blue"){
        return {
            noc:getResultNocRed(row),
            name:getResultAthleteRed(row)
        };
    }

    if(side === "red"){
        return {
            noc:getResultNocBlue(row),
            name:getResultAthleteBlue(row)
        };
    }

    return {
        noc:"",
        name:""
    };
}

function getMatchNumberByPrefix(row, prefix){
    const match = getResultMatch(row);
    if(!match || !prefix) return 0;

    const n = Number(match.replace(prefix.toUpperCase(), ""));
    return Number.isNaN(n) ? 0 : n;
}

function getRowsForDivision(meta){
    const prefix = getMetaMatchPrefix(meta).toUpperCase();

    if(!prefix) return [];

    return (ALL_RESULTS_ROWS || []).filter(r => {
        const match = getResultMatch(r);
        return match && match.startsWith(prefix);
    });
}

function isCutoffMeta(meta, rows){
    const type = String(meta?.type || "").toLowerCase();

    if(type.includes("freestyle")) return true;
    if(type.includes("demonstration")) return true;
    if(type.includes("demon")) return true;

    return (rows || []).some(r => {
        const method = String(getRowAny(r, ["Method", "method"])).toLowerCase();
        return method.includes("cut");
    });
}

function getScoreCompetitor(row){
    return {
        noc:getResultNocBlue(row),
        name:getResultAthleteBlue(row),
        score:getRowScore(row)
    };
}

function getCutoffMedalists(meta, rows){
    const finalRows = rows.filter(r => {
        const phase = getPhaseName(r).toLowerCase();
        return phase.includes("final");
    });

    const useRows = finalRows.length ? finalRows : rows;

    const byCompetitor = {};

    useRows.forEach(r => {
        const c = getScoreCompetitor(r);

        if(!c.noc && !c.name) return;

        const key = `${normalizeUpper(c.noc)}|${normalizeUpper(c.name)}`;

        if(!byCompetitor[key] || c.score > byCompetitor[key].score){
            byCompetitor[key] = c;
        }
    });

    const ranked = Object.values(byCompetitor)
        .filter(x => x.noc || x.name)
        .sort((a, b) => b.score - a.score)
        .slice(0, 4);

    return [
        {
            medal:"gold",
            label:"🥇 Gold",
            noc:ranked[0]?.noc || "",
            name:ranked[0]?.name || "",
            score:ranked[0]?.score ?? ""
        },
        {
            medal:"silver",
            label:"🥈 Silver",
            noc:ranked[1]?.noc || "",
            name:ranked[1]?.name || "",
            score:ranked[1]?.score ?? ""
        },
        {
            medal:"bronze",
            label:"🥉 Bronze",
            noc:ranked[2]?.noc || "",
            name:ranked[2]?.name || "",
            score:ranked[2]?.score ?? ""
        },
        {
            medal:"bronze",
            label:"🥉 Bronze",
            noc:ranked[3]?.noc || "",
            name:ranked[3]?.name || "",
            score:ranked[3]?.score ?? ""
        }
    ];
}

function findSingleEliminationFinal(rows, prefix){
    const valid = rows
        .map(r => ({
            row:r,
            no:getMatchNumberByPrefix(r, prefix)
        }))
        .filter(x => x.no > 0)
        .sort((a, b) => b.no - a.no);

    return valid[0]?.row || null;
}

function makeEmptyMedals(){
    return [
        {medal:"gold", label:"🥇 Gold", noc:"", name:""},
        {medal:"silver", label:"🥈 Silver", noc:"", name:""},
        {medal:"bronze", label:"🥉 Bronze", noc:"", name:""},
        {medal:"bronze", label:"🥉 Bronze", noc:"", name:""}
    ];
}

function getMatchRowByNo(rows, prefix, matchNo){
    const fullMatch = `${prefix}${String(matchNo).padStart(3, "0")}`.toUpperCase();

    return (rows || []).find(r => {
        const match = getResultMatch(r);
        return match === fullMatch;
    }) || null;
}

function getSingleEliminationMedalists(meta, rows){
    const medals = makeEmptyMedals();
    const prefix = getMetaMatchPrefix(meta).toUpperCase();

    const competitorCount = Number(
        meta.competitor_count ||
        meta.competitorCount ||
        meta.team_count ||
        meta.count ||
        0
    );

    // Single elimination: tổng trận thật = số VĐV/đội - 1
    const finalNo = competitorCount - 1;

    if(!prefix || finalNo < 1){
        return medals;
    }

    const semi1No = finalNo - 2;
    const semi2No = finalNo - 1;

    const semi1Row = semi1No > 0 ? getMatchRowByNo(rows, prefix, semi1No) : null;
    const semi2Row = semi2No > 0 ? getMatchRowByNo(rows, prefix, semi2No) : null;
    const finalRow = getMatchRowByNo(rows, prefix, finalNo);

    // Chưa có kết quả trận chung kết thì tuyệt đối không lấy trận nhỏ hơn làm Gold/Silver
    if(!finalRow){
        return medals;
    }

    const gold = getWinnerCompetitor(finalRow);
    const silver = getLoserCompetitor(finalRow);

    medals[0].noc = gold.noc;
    medals[0].name = gold.name;

    medals[1].noc = silver.noc;
    medals[1].name = silver.name;

    // HCĐ là 2 người/đội thua bán kết
    if(semi1Row){
        const bronze1 = getLoserCompetitor(semi1Row);
        medals[2].noc = bronze1.noc;
        medals[2].name = bronze1.name;
    }

    if(semi2Row){
        const bronze2 = getLoserCompetitor(semi2Row);
        medals[3].noc = bronze2.noc;
        medals[3].name = bronze2.name;
    }

    return medals;
}

function buildRankingCards(){
    const cards = [];

    // Chỉ lấy các division đã có trong file Sigma JSON
    // FLAT được tạo từ /api/menu, /api/menu lại đọc file Sigma JSON.
    (FLAT || []).filter(meta=>meta.type !== 'Kyeorugi').forEach(meta => {
        const rows = getRowsForDivision(meta);

        let medals = [
            {medal:"gold", label:"🥇 Gold", noc:"", name:""},
            {medal:"silver", label:"🥈 Silver", noc:"", name:""},
            {medal:"bronze", label:"🥉 Bronze", noc:"", name:""},
            {medal:"bronze", label:"🥉 Bronze", noc:"", name:""}
        ];

        if(rows.length){
            const isCut = isCutoffMeta(meta, rows);

            medals = isCut
                ? getCutoffMedalists(meta, rows)
                : getSingleEliminationMedalists(meta, rows);
        }

        cards.push({
            key:meta.key,
            type:meta.type || "",
            title:meta.key,
            count:meta.count || 0,
            competitor_count:meta.competitor_count || meta.count || 0,
            hasResult:rows.length > 0,
            medals:medals
        });
    });

    RANKING_CARDS = cards;
}

function medalClass(medal){
    if(medal === "gold") return "medal-gold";
    if(medal === "silver") return "medal-silver";
    return "medal-bronze";
}

function renderRankingSummary(){
    const box = document.getElementById("rankingCards");
    if(!box) return;

    const search = normalizeUpper(document.getElementById("rankingSearch")?.value || "");
    const typeFilter = String(document.getElementById("rankingTypeFilter")?.value || "").trim();
    const medalFilter = String(document.getElementById("rankingMedalFilter")?.value || "").trim();

    let cards = [...(RANKING_CARDS || [])];

    if(typeFilter){
        cards = cards.filter(c => String(c.type || "") === typeFilter);
    }

    if(search){
        cards = cards.filter(c => {
            const text = [
                c.title,
                c.type,
                ...(c.medals || []).flatMap(m => [m.noc, m.name])
            ].join(" ");

            return normalizeUpper(text).includes(search);
        });
    }

    if(medalFilter){
        cards = cards.map(c => ({
            ...c,
            medals:(c.medals || []).filter(m => m.medal === medalFilter)
        }));
    }

    if(!cards.length){
        box.innerHTML = `<div class="empty">Chưa có division nào trong file Sigma</div>`;
        return;
    }

    box.innerHTML = cards.map(card => `
        <div class="ranking-division-card">
            <div class="ranking-division-title">
                ${esc(card.title)}
                <div style="font-size:12px;color:#cfe6ff;margin-top:4px">
                    ${card.hasResult ? "Result loaded" : "No result yet"} · ${card.competitor_count || card.count || 0} athletes/teams
                </div>
            </div>

            <table class="ranking-medal-table">
                <thead>
                    <tr>
                        <th style="width:105px">Medal</th>
                        <th style="width:90px">NOC</th>
                        <th>Full name</th>
                    </tr>
                </thead>

                <tbody>
                    ${(card.medals || []).map(m => `
                        <tr>
                            <td class="medal-cell ${medalClass(m.medal)}">${esc(m.label)}</td>
                            <td class="${m.noc ? "" : "ranking-empty-cell"}">${esc(m.noc || "-")}</td>
                            <td class="${m.name ? "" : "ranking-empty-cell"}">${esc(m.name || "-")}</td>
                        </tr>
                    `).join("")}
                </tbody>
            </table>
        </div>
    `).join("");
}

async function loadMedalStandings(){
    const table = document.getElementById("medalStandingsTable");
    if(table){
        table.innerHTML = `<tr><td class="empty">Đang tổng hợp Medal Standings...</td></tr>`;
    }

    try{
        const menuData = await sigmaFetch("/api/menu").then(r => r.json());
        FLAT = menuData.flat || [];
    }catch(e){
        console.error("Load menu for medal standings error:", e);
        FLAT = [];
    }

    try{
        ALL_RESULTS_ROWS = await sigmaFetch("/api/all-results").then(r => r.json());
    }catch(e){
        console.error("Load all results for medal standings error:", e);
        ALL_RESULTS_ROWS = [];
    }

    try{
        INFORMATION_ROWS = await sigmaFetch("/api/information-list").then(async r => {
            const data=await r.json();
            if(!r.ok) throw new Error(data.error || 'HTTP '+r.status);
            return data;
        });
    }catch(e){
        console.error("Load information list for medal standings error:", e);
        INFORMATION_ROWS = [];
    }

    // Đọc riêng danh sách division thực sự đã có match trong Kyeorugi Matchs.
    // Lỗi API được hiển thị, không âm thầm xóa bảng Poomsae.
    try{
        const response = await sigmaFetch('/api/kyeorugi/ranking');
        const payload = await response.json();
        if(!response.ok) throw new Error(payload.error || 'HTTP '+response.status);
        MEDAL_KYEORUGI_CARDS = (payload.cards || []).map(card => ({
            key:'Kyeorugi - ' + card.title,
            title:'Kyeorugi - ' + card.title,
            type:'Kyeorugi',
            medals:card.medals || [],
            count:card.matches || 0,
            competitor_count:card.matches || 0,
            hasResult:(card.completed || 0)>0
        }));
        MEDAL_KYEORUGI_ERROR = '';
    }catch(error){
        MEDAL_KYEORUGI_CARDS = [];
        MEDAL_KYEORUGI_ERROR = String(error);
        console.error('Load Kyeorugi Medal Standings:',error);
    }
    buildRankingCards();
    buildMedalStandingsRows();
    renderMedalStandings();
}

function getDelegationStatsByNoc(){
    const stats = {};

    (INFORMATION_ROWS || []).forEach(row => {
        const noc = String(getRowAny(row, [
            "NOC",
            "noc",
            "Country",
            "country",
            "Team",
            "team"
        ])).trim();

        if(!noc) return;

        const key = normalizeUpper(noc);

        if(!stats[key]){
            stats[key] = {
                noc:noc,

                // Tổng toàn bộ người trong đoàn / tham gia
                total:0,

                // VĐV
                athletes:0,
                maleAthletes:0,
                femaleAthletes:0,

                // Coach
                coaches:0,

                // Người còn lại không phải VĐV/Coach
                otherOfficials:0,

                // Giữ lại để tương thích code cũ
                otherAthletes:0,
                delegation:0
            };
        }

        stats[key].total += 1;
        stats[key].delegation += 1;

        const position = normalizeUpper(getRowAny(row, [
            "Position",
            "position",
            "Role",
            "role",
            "Duty",
            "duty",
            "Function",
            "function"
        ]));

        const gender = normalizeUpper(getRowAny(row, [
            "Gender",
            "gender",
            "Sex",
            "sex"
        ]));

        const isCoach =
            position.includes("COACH") ||
            position.includes("TRAINER") ||
            position.includes("HLV");

        const isAthlete =
            position.includes("ATHLETE") ||
            position.includes("PLAYER") ||
            position.includes("COMPETITOR") ||
            position === "";

        if(isCoach){
            stats[key].coaches += 1;
            return;
        }

        if(isAthlete){
            stats[key].athletes += 1;

            if(gender.includes("MALE") && !gender.includes("FEMALE")){
                stats[key].maleAthletes += 1;
            }else if(gender.includes("FEMALE")){
                stats[key].femaleAthletes += 1;
            }else{
                stats[key].otherAthletes += 1;
            }

            return;
        }

        stats[key].otherOfficials += 1;
    });

    return stats;
}

function makeMedalStandingEmptyRow(noc, delegationStats){
    const key = normalizeUpper(noc);
    const stat = delegationStats[key] || {};

    return {
        noc:noc,
        noc_key:key,
        byDivision:{},

        totalGold:0,
        totalSilver:0,
        totalBronze:0,
        totalMedal:0,

        total:Number(stat.total || stat.delegation || 0),
        athletes:Number(stat.athletes || 0),
        maleAthletes:Number(stat.maleAthletes || 0),
        femaleAthletes:Number(stat.femaleAthletes || 0),
        coaches:Number(stat.coaches || 0),
        otherAthletes:Number(stat.otherAthletes || 0),
        otherOfficials:Number(stat.otherOfficials || 0),
        delegation:Number(stat.delegation || stat.total || 0),

        ranking:0
    };
}

let MEDAL_KYEORUGI_CARDS = [];
let MEDAL_KYEORUGI_ERROR = '';
function buildMedalStandingsRows(){
    const delegationStats = getDelegationStatsByNoc();
    const typeFilter = String(document.getElementById("medalStandingTypeFilter")?.value || "").trim();

    let cards = [...(RANKING_CARDS || []), ...(MEDAL_KYEORUGI_CARDS || [])];

    if(typeFilter){
        cards = cards.filter(c => String(c.type || "") === typeFilter);
    }

    cards = prepareMedalStandingDivisions(
        cards.map(c => ({
            ...c,
            title:c.title,
            key:c.key
        }))
    );

    MEDAL_STANDINGS_DIVISIONS = cards.map(c => ({
        key:c.key,
        title:c.title,
        type:c.type || "",
        typeLabel:c.typeLabel,
        divisionLabel:c.divisionLabel,
        genderLabel:c.genderLabel,
        ageLabel:c.ageLabel,
        genderAgeLabel:c.genderAgeLabel
    }));

    const rowsMap = {};

    Object.values(delegationStats).forEach(stat => {
        const key = normalizeUpper(stat.noc);

        if(!rowsMap[key]){
            rowsMap[key] = makeMedalStandingEmptyRow(stat.noc, delegationStats);
        }
    });

    cards.forEach(card => {
        const divisionKey = card.key;

        (card.medals || []).forEach(m => {
            const noc = String(m.noc || "").trim();
            if(!noc) return;

            const key = normalizeUpper(noc);

            if(!rowsMap[key]){
                rowsMap[key] = makeMedalStandingEmptyRow(noc, delegationStats);
            }

            if(!rowsMap[key].byDivision[divisionKey]){
                rowsMap[key].byDivision[divisionKey] = {
                    gold:0,
                    silver:0,
                    bronze:0
                };
            }

            if(m.medal === "gold"){
                rowsMap[key].byDivision[divisionKey].gold += 1;
                rowsMap[key].totalGold += 1;
            }else if(m.medal === "silver"){
                rowsMap[key].byDivision[divisionKey].silver += 1;
                rowsMap[key].totalSilver += 1;
            }else if(m.medal === "bronze"){
                rowsMap[key].byDivision[divisionKey].bronze += 1;
                rowsMap[key].totalBronze += 1;
            }
        });
    });

    let rows = Object.values(rowsMap);

    rows.forEach(row => {
        row.totalMedal =
            Number(row.totalGold || 0) +
            Number(row.totalSilver || 0) +
            Number(row.totalBronze || 0);
    });

    rows.sort((a, b) => {
        // 1. Vàng
        if(b.totalGold !== a.totalGold) return b.totalGold - a.totalGold;

        // 2. Bạc
        if(b.totalSilver !== a.totalSilver) return b.totalSilver - a.totalSilver;

        // 3. Đồng
        if(b.totalBronze !== a.totalBronze) return b.totalBronze - a.totalBronze;

        // 4. Tổng toàn bộ huy chương
        if(b.totalMedal !== a.totalMedal) return b.totalMedal - a.totalMedal;

        // 5. Đoàn đông nhất
        if(b.total !== a.total) return b.total - a.total;
        if(b.athletes !== a.athletes) return b.athletes - a.athletes;
        if(b.delegation !== a.delegation) return b.delegation - a.delegation;

        return String(a.noc).localeCompare(String(b.noc));
    });

    rows.forEach((row, index) => {
        row.ranking = index + 1;
    });

    MEDAL_STANDINGS_ROWS = rows;
}

function parseMedalDivisionTitle(title){
    const raw = String(title || "").trim();

    // Kyeorugi - Individual Male U12 +38 kg | Kyeorugi - Team 3 Female Cadet
    // Nhóm hiển thị lấy trực tiếp từ Division của Kyeorugi Matchs.
    if(/^Kyeorugi\s*-\s*/i.test(raw)){
        const rest=raw.replace(/^Kyeorugi\s*-\s*/i,'').trim();
        const division=/\bteam\b/i.test(rest)?'Team':'Individual';
        const gender=/\bfemale\b/i.test(rest)?'Female':/\bmale\b/i.test(rest)?'Male':/\bmixed\b/i.test(rest)?'Mixed':'Open';
        const ageMatch=rest.match(/\b(U9|U12|U14|U17|O18|Cadet|Junior|Senior)\b/i);
        const ageMap={U9:'U9',U12:'U12',U14:'Cadet',U17:'Junior',O18:'Senior',CADET:'Cadet',JUNIOR:'Junior',SENIOR:'Senior'};
        const age=ageMatch?(ageMap[ageMatch[1].toUpperCase()] || ageMatch[1]):'Open';
        const w=rest.match(/([+-])\s*(\d+(?:[.,]\d+)?)\s*(?:kg)?/i);
        const weight=w?(w[1]+w[2].replace(',','.')+'kg'):'';
        const team=rest.match(/\bteam\s*([345])\b/i);
        const kind=team?'Team '+team[1]:/\bmixed\s*team\b/i.test(rest)?'Mixed Team':'';
        const label=[gender,age,weight || kind].filter(Boolean).join(' ');
        return {type:'Kyeorugi',division,gender,age:age+(weight?' '+weight:''),
                genderAge:label,sortWeight:w?Number(w[2].replace(',','.')):9999,
                sortWeightSign:w?w[1]:'', sortAge:age,sortGender:gender};
    }

    let type = "Other";
    let rest = raw;

    if(raw.includes(" - ")){
        const parts = raw.split(" - ");
        type = String(parts[0] || "Other").trim();
        rest = parts.slice(1).join(" - ").trim();
    }else{
        const knownTypes = ["Recognized", "Freestyle", "Demonstration", "Mixer"];

        for(const t of knownTypes){
            if(raw.toLowerCase().startsWith(t.toLowerCase())){
                type = t;
                rest = raw.slice(t.length).trim();
                break;
            }
        }
    }

    if(/demo|demon/i.test(type)){
        type = "Demonstration";
    }

    let division = "Other";

    if(/\bIndividual\b/i.test(rest)){
        division = "Individual";
    }else if(/\bPair\b/i.test(rest)){
        division = "Pair";
    }else if(/\bTeam\b/i.test(rest)){
        division = "Team";
    }

    let gender = "Open";

    if(/\bMale\b/i.test(rest)){
        gender = "Male";
    }else if(/\bFemale\b/i.test(rest)){
        gender = "Female";
    }else if(/\bMixed\b/i.test(rest)){
        gender = "Mixed";
    }

    let age = rest
        .replace(/\bIndividual\b/ig, "")
        .replace(/\bPair\b/ig, "")
        .replace(/\bTeam\b/ig, "")
        .replace(/\bMale\b/ig, "")
        .replace(/\bFemale\b/ig, "")
        .replace(/\bMixed\b/ig, "")
        .replace(/\s+/g, " ")
        .trim();

    if(!age){
        age = "Open";
    }

    const genderAge = gender === "Open"
        ? age
        : `${gender} ${age}`;

    return {
        type:type,
        division:division,
        gender:gender,
        age:age,
        genderAge:genderAge
    };
}

function medalTypeOrder(type){
    const t = String(type || "").toLowerCase();

    if(t.includes("recognized")) return 1;
    if(t.includes("freestyle")) return 2;
    if(t.includes("demonstration") || t.includes("demon")) return 3;
    if(t.includes("mixer") || t.includes("mixed")) return 4;
    if(t.includes("kyeorugi")) return 5;

    return 99;
}

function medalDivisionOrder(division){
    const d = String(division || "").toLowerCase();

    if(d.includes("individual")) return 1;
    if(d.includes("pair")) return 2;
    if(d.includes("team")) return 3;

    return 99;
}

function medalGenderOrder(gender){
    const g = String(gender || "").toLowerCase();

    if(g.includes("male")) return 1;
    if(g.includes("female")) return 2;
    if(g.includes("mixed")) return 3;

    return 99;
}

function medalAgeOrder(age){
    const a = String(age || "").toUpperCase();

    if(a.includes("U12")) return 5;
    if(a.includes("CADET")) return 10;
    if(a.includes("JUNIOR")) return 20;
    if(a.includes("U17")) return 25;
    if(a.includes("O17")) return 30;
    if(a.includes("U30")) return 40;
    if(a.includes("U40")) return 50;
    if(a.includes("U50")) return 60;
    if(a.includes("U60")) return 70;
    if(a.includes("U65")) return 80;
    if(a.includes("O65")) return 90;
    if(a.includes("OPEN")) return 100;

    const num = a.match(/\d+/);
    if(num){
        return 200 + Number(num[0]);
    }

    return 999;
}

function prepareMedalStandingDivisions(rawDivisions){
    return [...(rawDivisions || [])]
        .map(d => {
            const parsed = parseMedalDivisionTitle(d.title);

            return {
                ...d,
                typeLabel:parsed.type,
                divisionLabel:parsed.division,
                genderLabel:parsed.gender,
                ageLabel:parsed.age,
                genderAgeLabel:parsed.genderAge,
                sortWeight:parsed.sortWeight ?? 9999,
                sortWeightSign:parsed.sortWeightSign || '',
                sortAge:parsed.sortAge || parsed.age
            };
        })
        .sort((a, b) => {
            const ta = medalTypeOrder(a.typeLabel);
            const tb = medalTypeOrder(b.typeLabel);
            if(ta !== tb) return ta - tb;

            const da = medalDivisionOrder(a.divisionLabel);
            const db = medalDivisionOrder(b.divisionLabel);
            if(da !== db) return da - db;

            // Chỉ Kyeorugi sắp theo: Tuổi → Hạng cân → Giới tính.
            // Tiêu đề vẫn là Giới tính – Tuổi – Hạng cân.
            if(a.typeLabel === 'Kyeorugi' && b.typeLabel === 'Kyeorugi'){
                const ageOrder={U9:0,U12:1,Cadet:2,Junior:3,Senior:4};
                const aa=ageOrder[a.sortAge] ?? medalAgeOrder(a.sortAge || a.ageLabel);
                const ab=ageOrder[b.sortAge] ?? medalAgeOrder(b.sortAge || b.ageLabel);
                if(aa!==ab) return aa-ab;
                if(a.sortWeight!==b.sortWeight) return a.sortWeight-b.sortWeight;
                if(a.sortWeightSign!==b.sortWeightSign)
                    return a.sortWeightSign==='-'?-1:1;
                const ga=medalGenderOrder(a.genderLabel);
                const gb=medalGenderOrder(b.genderLabel);
                if(ga!==gb) return ga-gb;
            } else {
                const ga = medalGenderOrder(a.genderLabel);
                const gb = medalGenderOrder(b.genderLabel);
                if(ga !== gb) return ga - gb;
                const aa = medalAgeOrder(a.ageLabel);
                const ab = medalAgeOrder(b.ageLabel);
                if(aa !== ab) return aa - ab;
            }
            return String(a.genderAgeLabel).localeCompare(String(b.genderAgeLabel));
        });
}

function buildGroupedMedalStandingHeader(divisions){
    const firstRow = [];
    const secondRow = [];
    const thirdRow = [];

    let i = 0;

    while(i < divisions.length){
        const currentType = divisions[i].typeLabel;
        let count = 0;

        while(
            i + count < divisions.length &&
            divisions[i + count].typeLabel === currentType
        ){
            count++;
        }

        firstRow.push(`
            <th class="medal-standing-head-type" colspan="${count}">
                ${esc(currentType)}
            </th>
        `);

        i += count;
    }

    i = 0;

    while(i < divisions.length){
        const currentType = divisions[i].typeLabel;
        const currentDivision = divisions[i].divisionLabel;
        let count = 0;

        while(
            i + count < divisions.length &&
            divisions[i + count].typeLabel === currentType &&
            divisions[i + count].divisionLabel === currentDivision
        ){
            count++;
        }

        secondRow.push(`
            <th class="medal-standing-head-division" colspan="${count}">
                ${esc(currentDivision)}
            </th>
        `);

        i += count;
    }

    divisions.forEach(d => {
        thirdRow.push(`
            <th class="medal-standing-head-age">
                ${esc(d.genderAgeLabel)}
            </th>
        `);
    });

    return `
        <thead>
            <tr>
                <th class="medal-standing-fixed-head" rowspan="3" style="min-width:170px">
                    Unit / NOC
                </th>

                ${firstRow.join("")}

                <th class="medal-standing-summary-head" rowspan="3">Total Gold</th>
                <th class="medal-standing-summary-head" rowspan="3">Total Silver</th>
                <th class="medal-standing-summary-head" rowspan="3">Total Bronze</th>
                <th class="medal-standing-summary-head" rowspan="3">Total Medal</th>
                <th class="medal-standing-summary-head" rowspan="3">Ranking</th>
            </tr>

            <tr>
                ${secondRow.join("")}
            </tr>

            <tr>
                ${thirdRow.join("")}
            </tr>
        </thead>
    `;
}

function medalStandingDivisionCell(row, divisionKey){
    const d = row.byDivision[divisionKey] || {
        gold:0,
        silver:0,
        bronze:0
    };

    if(!d.gold && !d.silver && !d.bronze){
        return `<span class="ranking-empty-cell">-</span>`;
    }

    const icons = [];

    for(let i = 0; i < d.gold; i++){
        icons.push(`<span class="medal-icon-item medal-standing-gold"><span class="medal-icon-symbol">🥇</span></span>`);
    }

    for(let i = 0; i < d.silver; i++){
        icons.push(`<span class="medal-icon-item medal-standing-silver"><span class="medal-icon-symbol">🥈</span></span>`);
    }

    for(let i = 0; i < d.bronze; i++){
        icons.push(`<span class="medal-icon-item medal-standing-bronze"><span class="medal-icon-symbol">🥉</span></span>`);
    }

    return `
        <div class="medal-division-cell">
            <div class="medal-icon-wrap">
                ${icons.join("")}
            </div>
        </div>
    `;
}

function renderMedalStandings(){
    const table = document.getElementById("medalStandingsTable");
    if(!table) return;

    const search = normalizeUpper(document.getElementById("medalStandingSearch")?.value || "");

    buildMedalStandingsRows();

    let rows = [...(MEDAL_STANDINGS_ROWS || [])];
    const divisions = prepareMedalStandingDivisions(MEDAL_STANDINGS_DIVISIONS || []);

    if(search){
        rows = rows.filter(r => {
            const divisionText = divisions
                .map(d => `${d.typeLabel} ${d.divisionLabel} ${d.genderAgeLabel}`)
                .join(" ");

            const text = [
                r.noc,
                divisionText,
                r.total,
                r.athletes,
                r.maleAthletes,
                r.femaleAthletes,
                r.coaches,
                r.otherAthletes,
                r.otherOfficials,
                r.totalGold,
                r.totalSilver,
                r.totalBronze,
                r.totalMedal,
                r.ranking
            ].join(" ");

            return normalizeUpper(text).includes(search);
        });
    }

    if(!rows.length){
        table.innerHTML = `<tr><td class="empty">Chưa có dữ liệu tổng sắp huy chương</td></tr>`;
        return;
    }

    table.innerHTML = `
        ${buildGroupedMedalStandingHeader(divisions)}

        <tbody>
            ${rows.map(r => `
                <tr>
                    <td class="medal-standing-unit-cell">
                        <span class="medal-unit-main">${esc(r.noc)}</span>

                        <span class="medal-unit-sub">
                            <span class="medal-unit-total">Total: ${r.total || 0}</span><br>
                            <span class="medal-unit-coach">Coach: ${r.coaches || 0}</span><br>
                            <span class="medal-unit-male">Male Ath: ${r.maleAthletes || 0}</span><br>
                            <span class="medal-unit-female">Female Ath: ${r.femaleAthletes || 0}</span><br>
                            <span class="medal-unit-other">Official: ${(r.otherAthletes || 0) + (r.otherOfficials || 0)}</span>
                        </span>
                    </td>

                    ${divisions.map(d => `
                        <td>${medalStandingDivisionCell(r, d.key)}</td>
                    `).join("")}

                    <td class="medal-standing-total medal-standing-gold">
                        ${r.totalGold}
                    </td>

                    <td class="medal-standing-total medal-standing-silver">
                        ${r.totalSilver}
                    </td>

                    <td class="medal-standing-total medal-standing-bronze">
                        ${r.totalBronze}
                    </td>

                    <td class="medal-standing-total">
                        ${r.totalMedal || 0}
                    </td>

                    <td class="medal-standing-rank">${r.ranking}</td>
                </tr>
            `).join("")}
        </tbody>
    `;

    setTimeout(setupDragScroll, 50);
}


function getCurrentMatchPrefix(){
    const meta = (FLAT || []).find(x => x.key === CURRENT_KEY);
    if(!meta) return "";

    const typeMap = {
        "Recognized":"R",
        "Freestyle":"F",
        "Demonstration":"D",
        "Mixer":"M"
    };

    const divMap = {
        "Individual":"I",
        "Pair":"P",
        "Team":"T"
    };

    const genderMap = {
        "Male":"M",
        "Female":"F"
    };

    const t = typeMap[meta.type] || "";
    const d = divMap[meta.division] || "";
    const g = genderMap[meta.gender] || "";

    let ageRaw = String(meta.age || "").trim().toUpperCase();

    const ageMap = {
        "CADET": "U14",
        "JUNIOR": "U17",
        "UNDER 14": "U14",
        "U 14": "U14",
        "U14": "U14",
        "UNDER 17": "U17",
        "U 17": "U17",
        "U17": "U17"
    };

    let age = ageMap[ageRaw] || ageRaw.replace(/\s+/g, "");

    return `${t}${d}${g}${age}`;
}

function esc(s){
  return String(s ?? "").replace(/[&<>'"]/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'
  }[c]));
}


async function loadMenu(){
  const root = document.getElementById("menu");
  let data;
  try {
    const response = await sigmaFetch("/api/menu");
    data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  } catch (error) {
    console.error("Load Division failed", error);
    if (root) root.innerHTML = `<div class="empty">Không tải được Division. Kiểm tra Terminal và thử tải lại trang.</div>`;
    return;
  }
  FLAT = data.flat || [];
  const menu = data.menu || {};
  if(!root) return;
  root.innerHTML = "";

  // Exclusive accordion: only ONE branch of the Division tree may remain open.
  // Keep ancestors of the selected button open, but collapse other groups,
  // including independent top-level .group containers (Kyeorugi/Poomsae).
  function closeOtherDivisionBranches(currentButton){
      root.querySelectorAll('button.groupHead, button.subHead, button.genderHead, button.ageBtn')
          .forEach(otherButton => {
              const otherPanel = otherButton.nextElementSibling;
              // Leaf buttons have no matching nested menu panel.
              if(!otherPanel || otherPanel.tagName !== 'DIV' ||
                 otherPanel.classList.contains('hidden')) return;
              if(otherButton === currentButton || otherPanel.contains(currentButton)) return;
              otherPanel.classList.add('hidden');
              otherButton.textContent = String(otherButton.textContent || '')
                  .replace(/^\s*[▸▾]\s*/, '▸ ');
          });
  }

  // Keep the opened Division/submenu visible in the SIDEBAR viewport.
  // scrollIntoView() can move the entire page; scroll the sidebar explicitly.
  function focusExpandedMenu(button, panel){
      try{button.focus({preventScroll:true});}catch(_){button.focus();}
      const sidebar=button.closest('.sidebar');
      if(!sidebar)return;
      const s=sidebar.getBoundingClientRect();
      const b=button.getBoundingClientRect();
      const p=panel?.getBoundingClientRect();
      const margin=12;
      let delta=0;
      if(p && !panel.classList.contains('hidden') && p.height+b.height < s.height-2*margin){
          if(p.bottom>s.bottom-margin)delta=p.bottom-(s.bottom-margin);
          if(b.top-delta<s.top+margin)delta=b.top-(s.top+margin);
      }else{
          if(b.top<s.top+margin)delta=b.top-(s.top+margin);
          else if(b.bottom>s.bottom-margin)delta=b.bottom-(s.bottom-margin);
          // Very long expanded menus: place the clicked heading near the top.
          if(p && !panel.classList.contains('hidden') && p.bottom>s.bottom && b.top>s.top+margin)
              delta=b.top-(s.top+margin);
      }
      if(Math.abs(delta)>1)sidebar.scrollTo({top:Math.max(0,sidebar.scrollTop+delta),behavior:'smooth'});
  }
  function toggleAccordion(button, body, buttonClass, label){
      const willOpen = body.classList.contains("hidden");

      if(willOpen) closeOtherDivisionBranches(button);

      if(willOpen){
          body.classList.remove("hidden");
          button.textContent = `▾ ${label}`;
          requestAnimationFrame(()=>focusExpandedMenu(button,body));
      }else{
          body.classList.add("hidden");
          button.textContent = `▸ ${label}`;
          requestAnimationFrame(()=>focusExpandedMenu(button,null));
      }
  }

  // Kyeorugi has a different hierarchy and is driven solely by Kyeorugi Matchs.
  const kItems = FLAT.filter(item => item.type === 'Kyeorugi');
  if(kItems.length){
      const rootGroup = document.createElement('div'); rootGroup.className='group';
      const kHead = document.createElement('button'); kHead.className='groupHead'; kHead.textContent='▸ Kyeorugi';
      const kBody = document.createElement('div'); kBody.className='hidden';
      kHead.onclick=()=>toggleAccordion(kHead,kBody,'groupHead','Kyeorugi');
      rootGroup.append(kHead,kBody);root.appendChild(rootGroup);
      function layer(parent,label,css){
          const btn=document.createElement('button');btn.className=css;btn.textContent='▸ '+label;
          const body=document.createElement('div');body.className='hidden';
          btn.onclick=()=>toggleAccordion(btn,body,css,label);
          parent.append(btn,body);return body;
      }
      const categories=[['Individuals', 'Individuals'],['Team','Team']];
      for(const [category,title] of categories){
          const items=kItems.filter(item=>item.group===category);
          if(!items.length)continue;
          const categoryBody=layer(kBody,title,'subHead');
          const types=category==='Team'?['Team 3','Team 4','Team 5','Mixed Team']:[''];
          for(const teamType of types){
              const subset=items.filter(item=>(item.team_kind||'')===teamType);
              if(!subset.length)continue;
              const typeBody=teamType?layer(categoryBody,teamType,'subHead'):categoryBody;
              for(const gender of ['Male','Female']){
                  const genderItems=subset.filter(item=>item.gender===gender);
                  if(!genderItems.length)continue;
                  const genderBody=layer(typeBody,gender,'genderHead');
                  const ages=[...new Set(genderItems.map(item=>item.age))];
                  const order={'U9':0,'U12':1,'Cadet (U14)':2,'Junior (U17)':3,'Senior (O18)':4};
                  ages.sort((a,b)=>(order[a]??99)-(order[b]??99));
                  for(const age of ages){
                      const ageItems=genderItems.filter(item=>item.age===age);
                      if(category==='Team'){
                          const btn=document.createElement('button');btn.className='ageBtn';
                          btn.textContent=age;
                          btn.onclick=()=>{selectDivision(ageItems[0].key,btn);closeSidebarOnMobile();};
                          genderBody.appendChild(btn);
                      }else{
                          const ageBody=layer(genderBody,age,'ageBtn');
                          ageItems.sort((a,b)=>parseInt(a.weight.replace(/[^0-9]/g,''))-parseInt(b.weight.replace(/[^0-9]/g,'')));
                          for(const item of ageItems){
                              const btn=document.createElement('button');btn.className='ageBtn';
                              btn.textContent=item.weight;
                              btn.onclick=()=>{selectDivision(item.key,btn);closeSidebarOnMobile();};
                              ageBody.appendChild(btn);
                          }
                      }
                  }
              }
          }
      }
  }
  Object.keys(menu).filter(type=>type!=='Kyeorugi').forEach(type => {
    const group = document.createElement("div");
    group.className = "group";

    const head = document.createElement("button");
    head.className = "groupHead";
    head.textContent = `▸ ${type}`;

    const body = document.createElement("div");
    body.className = "hidden";

    head.onclick = () => toggleAccordion(head, body, "groupHead", type);

    group.appendChild(head);
    group.appendChild(body);

    Object.keys(menu[type]).forEach(division => {
      const sub = document.createElement("button");
      sub.className = "subHead";
      sub.textContent = `▸ ${division}`;

      const subBody = document.createElement("div");
      subBody.className = "hidden";

      sub.onclick = () => toggleAccordion(sub, subBody, "subHead", division);

      body.appendChild(sub);
      body.appendChild(subBody);

      Object.keys(menu[type][division]).forEach(gender => {
          const genderBtn = document.createElement("button");
          genderBtn.className = "genderHead";
          genderBtn.textContent = `▸ ${gender}`;

          const genderBody = document.createElement("div");
          genderBody.className = "hidden";

          genderBtn.onclick = () => toggleAccordion(genderBtn, genderBody, "genderHead", gender);

          subBody.appendChild(genderBtn);
          subBody.appendChild(genderBody);

          Object.keys(menu[type][division][gender]).forEach(age => {
              const metas = menu[type][division][gender][age] || [];
              let target = genderBody;
              if(type === 'Kyeorugi'){
                  const ageHead = document.createElement('button');
                  ageHead.className = 'ageBtn';
                  ageHead.textContent = '▸ ' + age;
                  const ageBody = document.createElement('div');
                  ageBody.className = 'hidden';
                  ageHead.onclick = () => ageBody.classList.toggle('hidden');
                  genderBody.appendChild(ageHead);
                  genderBody.appendChild(ageBody);
                  target = ageBody;
              }
              metas.sort((a,b) => Number((a.weight||'').replace(/[^0-9.]/g,'')) - Number((b.weight||'').replace(/[^0-9.]/g,'')))
                  .forEach(meta => {
                  const b = document.createElement('button');
                  b.className = 'ageBtn';
                  b.textContent = type === 'Kyeorugi'
                      ? `${meta.weight || meta.division} (${meta.count})`
                      : `${age} (${meta.count})`;
                  b.onclick = () => {selectDivision(meta.key, b);closeSidebarOnMobile();};
                  target.appendChild(b);
              });
          });
      });
    });

    root.appendChild(group);
  });
}

async function selectDivision(key, btn){
  showMainDivisionViews();

  CURRENT_KEY = key;
  document.querySelectorAll(".ageBtn").forEach(x => x.classList.remove("active"));
  if(btn) btn.classList.add("active");

  const selectedTitle = document.getElementById("selectedTitle");
  if(selectedTitle) selectedTitle.textContent = key;

  await Promise.all([loadAthletes(key), loadResults(key), loadKyeorugiOfficialNumbers(key)]);
}

async function reloadCurrent(){
  if(CURRENT_KEY) await selectDivision(CURRENT_KEY);
}

function renderTable(id, rows){
  const table = document.getElementById(id);
  if(!table) return;

  if(!rows || !rows.length){
    table.innerHTML = `<tr><td class="empty">Chưa có dữ liệu</td></tr>`;
    return;
  }

  // Kyeorugi has an independent roster, so preserve its original columns.
  const kyeorugiAthletes = id === "athleteTable" && CURRENT_KEY && CURRENT_KEY.startsWith('Kyeorugi - ');
  if(id === "athleteTable"){
    table.classList.toggle('kyeorugi-athletes', Boolean(kyeorugiAthletes));
    table.classList.toggle('poomsae-athletes', !kyeorugiAthletes);
  }

  let cols = [];

  if(id === "athleteTable"){
    cols = kyeorugiAthletes
      ? ["No.Random", "NOC", "Country", "Full name", "Weigh-in"]
      : ["No.", "NOC", "Country", "Full name"];

    if(!kyeorugiAthletes){
      // Only Pair/Team/Mixed Team are grouped by drawn No.; Individual stays 1 athlete per row.
      const isGroupDivision = /\b(pair|team|mixed team)\b/i.test(CURRENT_KEY || '');
      const sorted = [...rows].sort((a,b) => {
        const left = String(a['No.'] ?? '').trim();
        const right = String(b['No.'] ?? '').trim();
        const ln = Number(left), rn = Number(right);
        if(left !== '' && right !== '' && Number.isFinite(ln) && Number.isFinite(rn)) return ln-rn;
        return left.localeCompare(right, undefined, {numeric:true});
      });
      const groups = new Map();
      for(const r of sorted){
        const no = String(r['No.'] ?? '').trim();
        const noc = String(r.NOC ?? '').trim();
        const country = String(r.Country ?? '').trim();
        const name = String(r['Full name'] ?? '').trim();
        const gender = String(r.Gender ?? '').trim().toUpperCase();
        const suffix = gender === 'MALE' || gender === 'M' ? ' (M)' :
                       gender === 'FEMALE' || gender === 'F' ? ' (F)' : '';
        const member = name ? name + suffix : '';
        // Do not merge missing draw numbers or different NOCs into one team.
        const groupKey = isGroupDivision && no !== '' ? `${no}\u001f${noc.toUpperCase()}` : Symbol();
        if(!groups.has(groupKey)){
          groups.set(groupKey, {'No.':no, NOC:noc, Country:country, 'Full name':'', _members:[]});
        }
        const entry = groups.get(groupKey);
        if(!entry.Country && country) entry.Country = country;
        if(member && !entry._members.includes(member)) entry._members.push(member);
      }
      const displayed = [...groups.values()].map(r => ({
        'No.': r['No.'], NOC:r.NOC, Country:r.Country,
        'Full name':r._members.join(', ')
      }));
      table.innerHTML =
        `<thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>` +
        `<tbody>${displayed.map(r => `<tr>${cols.map(c => `<td>${esc(r[c] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody>`;
      return;
    }
  }else{
    const preferred = [
      "Match","Court","Phase","Division","No.","NOC","Full name",
      "Athlete Blue","Athlete Red","Poomsae 1","Poomsae 2",
      "Total","Result","Action"
    ];

    cols = preferred.filter(c =>
      rows.some(r => r[c] !== undefined && r[c] !== null && r[c] !== "")
    );

    Object.keys(rows[0]).forEach(c => {
      if(!cols.includes(c) && !String(c).startsWith("_")) cols.push(c);
    });
  }

  table.innerHTML =
    `<thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead>` +
    `<tbody>${rows.map(r => `
      <tr>
        ${cols.map(c => `<td>${esc(r[c] ?? "")}</td>`).join("")}
      </tr>
    `).join("")}</tbody>`;
}

async function loadAthletes(key){
  const rows = await sigmaFetch("/api/athletes?key=" + encodeURIComponent(key)).then(r => r.json());
  window.currentAthletes = rows || [];

  const sigmaView = document.getElementById("sigmaView");
  const athleteCard = document.querySelector(".athlete-card");
  const layout = document.querySelector(".layout");
  const selectedBox = document.querySelector(".selectedBox");
  const sidebar = document.querySelector(".sidebar");

  if(layout) layout.classList.remove("result-mode");
  document.body.classList.remove("sigma-result-mode");
  document.body.classList.remove("sidebar-open");
  if(layout) layout.classList.remove("menu-open");
  if(sidebar) sidebar.classList.remove("hidden");
  if(selectedBox) selectedBox.classList.remove("hidden");
  if(athleteCard) athleteCard.classList.remove("hidden");
  if(sigmaView) sigmaView.classList.add("hidden");

  renderTable("athleteTable", rows);
}

async function loadResults(key){
  CURRENT_RESULTS = [];
  CURRENT_POOMSAE_MATCHES = [];
  CURRENT_POOMSAE_MATCH_ERROR = '';
  if(!String(key || '').startsWith('Kyeorugi - ')){
    try{
      const officialRes = await sigmaFetch('/api/poomsae/matches?key=' + encodeURIComponent(key), {cache:'no-store'});
      const officialData = await officialRes.json();
      if(!officialRes.ok || !Array.isArray(officialData)){
        CURRENT_POOMSAE_MATCH_ERROR = String(officialData?.error || `HTTP ${officialRes.status}`);
        console.warn('[POOMSAE MATCH NUMBERS]', CURRENT_POOMSAE_MATCH_ERROR);
      } else {
        CURRENT_POOMSAE_MATCHES = officialData;
        if(!officialData.length) CURRENT_POOMSAE_MATCH_ERROR = 'Không tìm thấy trận của Division trong Poomsae Matchs (kiểm tra Poomsae Type / Division).';
      }
    }catch(error){
      CURRENT_POOMSAE_MATCH_ERROR = String(error);
      console.warn('[POOMSAE MATCH NUMBERS]', error);
    }
  }
  const response = await sigmaFetch("/api/results?key=" + encodeURIComponent(key), {cache:'no-store'});
  const rows = await response.json();
  if(!response.ok || !Array.isArray(rows)) {
      const msg = rows?.error || ('HTTP ' + response.status);
      console.error('[SIGMA RESULTS]', msg);
      alert('Không tải được kết quả trận đấu: ' + msg);
      return;
  }
  CURRENT_RESULTS = rows;

  const table = document.getElementById("resultTable");
  if(table) renderTable("resultTable", rows);

  const sigmaView = document.getElementById("sigmaView");
  if(sigmaView && !sigmaView.classList.contains("hidden")){
      if(isCutOffDivision()){
          buildCutoffResult(window.currentAthletes || []);
      }else{
          buildSigmaBracket(window.currentAthletes || []);
      }
  }
}

function buildSeedOrder(size){
  if(size === 4) return [1,4,2,3];
  if(size === 8) return [1,8,4,5,2,7,3,6];
  if(size === 16) return [1,16,8,9,4,13,5,12,2,15,7,10,3,14,6,11];
  if(size === 32){
    return [1,32,16,17,8,25,9,24,4,29,13,20,5,28,12,21,2,31,15,18,7,26,10,23,3,30,14,19,6,27,11,22];
  }
  if(size === 64){
    return [
      1,64,32,33,16,49,17,48,8,57,25,40,9,56,24,41,
      4,61,29,36,13,52,20,45,5,60,28,37,12,53,21,44,
      2,63,31,34,15,50,18,47,7,58,26,39,10,55,23,42,
      3,62,30,35,14,51,19,46,6,59,27,38,11,54,22,43
    ];
  }
  return Array.from({length:size}, (_, i) => i + 1);
}

function getBracketSize(n){
  if(n <= 2) return 2;
  if(n <= 4) return 4;
  if(n <= 8) return 8;
  if(n <= 16) return 16;
  if(n <= 32) return 32;
  return 64;
}

function shortenName(fullName){
  const text = String(fullName || "").trim().toUpperCase();
  if(!text) return "";

  const parts = text.split(/\s+/).filter(Boolean);
  if(parts.length <= 1) return text;

  const last = parts[parts.length - 1];
  const initials = parts.slice(0, -1).map(p => p[0]).join(".");
  return `${initials}.${last}`;
}


function getTeamFullNames(row){
  const names = [];

  if(CURRENT_KEY && CURRENT_KEY.startsWith('Kyeorugi - ')){
    return String(row?.['Full name'] || '');
  }
  Object.keys(row || {}).forEach(k => {
    const key = String(k || "").toLowerCase().trim();
    const value = String(row[k] || "").trim();

    if(!value) return;

    const isNameField =
      key === "full name" ||
      key.startsWith("full name") ||
      key === "fullname" ||
      key.startsWith("fullname") ||
      key === "athlete" ||
      key.startsWith("athlete") ||
      key.includes("full_name") ||
      key.includes("full name");

    if(isNameField && !names.includes(value)){
      names.push(value);
    }
  });

  if(!names.length && row["Full name"]){
    names.push(String(row["Full name"]).trim());
  }

  return names.join(" / ");
}

function getNameLines(fullName){
  const raw = String(fullName || "").trim();
  if(!raw) return [""];

  const names = raw
    .split(/\s*(?:\/|\\|\||;|\n|\r|,)+\s*/)
    .map(x => shortenName(x))
    .filter(Boolean);

  const lines = [];
  let current = "";

  names.forEach(name => {
    const next = current ? `${current} | ${name}` : name;

    if(next.length > 36){
      if(current) lines.push(current);
      current = name;
    }else{
      current = next;
    }
  });

  if(current) lines.push(current);

  return lines.length ? lines : [""];
}

function svgEl(tag, attrs = {}){
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
  return el;
}

function drawText(svg, x, y, text, cls, anchor = "middle"){
  const t = svgEl("text", {
    x, y,
    class: cls,
    "text-anchor": anchor,
    "dominant-baseline": "middle"
  });
  t.textContent = text;
  svg.appendChild(t);
  return t;
}

function drawTextLines(svg, x, y, lines, cls, anchor = "start"){
  const lineH = 16;
  const totalH = (lines.length - 1) * lineH;
  lines.forEach((line, idx) => {
    drawText(svg, x, y - totalH / 2 + idx * lineH, line, cls, anchor);
  });
}

function drawLine(svg, x1, y1, x2, y2){
  // Straight segments only: keep a line under the match-number boxes.
  svg.appendChild(svgEl("line", {
    x1:x1, y1:y1, x2:x2, y2:y2, class:"sigma-svg-line"
  }));
}

function drawGuideLine(layer, x, y1, y2){
  layer.appendChild(svgEl("line", {
    x1:x, y1:y1, x2:x, y2:y2,
    class:"sigma-svg-guide"
  }));
}

function drawPhaseHeader(layer, x, y, label){
  layer.appendChild(svgEl("rect", {
    x:x - 26,
    y:y - 15,
    width:52,
    height:30,
    rx:3,
    class:"sigma-svg-phase-box"
  }));

  drawText(layer, x, y, label, "sigma-svg-phase-text");
}

function isByePlayer(p){
  return String(p?.name || "").trim().toUpperCase() === "BYE";
}

// Keep cards fixed-height. Wrap long country names first; shrink only when needed.
function drawFittedCompetitorLabel(svg, x, y, content, cls, anchor, maxWidth, maxHeight){
  const text = String(content || '').replace(/\s+/g,' ').trim();
  const words = text.split(' ').filter(Boolean);
  const el = drawText(svg, x, y, '', cls, anchor);
  const makeLines = n => {
    if(n === 1 || words.length <= 1) return [text];
    let best=[text], score=Infinity;
    for(let k=1;k<words.length;k++){
      const left=words.slice(0,k).join(' '), right=words.slice(k).join(' ');
      const diff=Math.abs(left.length-right.length);
      if(diff<score){score=diff;best=[left,right];}
    }
    return best;
  };
  const render = (lines, size) => {
    el.replaceChildren();
    el.style.fontSize=size+'px';
    const step=size*1.12;
    lines.forEach((line,i)=>{
      const t=svgEl('tspan',{x,y:y+(i-(lines.length-1)/2)*step});
      t.textContent=line;
      el.appendChild(t);
    });
    el.removeAttribute('dominant-baseline');
    el.setAttribute('dominant-baseline','middle');
  };
  let fontSize=15;
  let lines=makeLines(1);
  render(lines,fontSize);
  if(el.getBBox().width>maxWidth){lines=makeLines(2);render(lines,fontSize);}
  while(fontSize>7){
    const r=el.getBBox();
    if(r.width<=maxWidth && r.height<=maxHeight)break;
    fontSize-=0.5;
    render(lines,fontSize);
  }
  // One unbreakable word also stays inside the unchanged box.
  return el;
}

function drawPlayer(svg, p, x, y, side, cfg){
  const isBye = isByePlayer(p);
  const { playerW, playerH, seedW, nocW } = cfg;
  const actualH = playerH;
  if(isBye){
    return side === "left"
      ? { outX:x + playerW, y, player:p, hidden:true }
      : { outX:x, y, player:p, hidden:true };
  }
  const role=p.role || 'blue';
  const boxClass=role==='red'?'sigma-svg-box player-red':'sigma-svg-box player-blue';
  const seedClass=role==='red'?'sigma-svg-seed-red':'sigma-svg-seed-blue';
  const nocClass=role==='red'?'sigma-svg-noc-red':'sigma-svg-noc-blue';
  const nameClass=role==='red'?'sigma-svg-name-red':'sigma-svg-name-blue';
  svg.appendChild(svgEl('rect',{
    x,y:y-actualH/2,width:playerW,height:actualH,rx:8,
    class:boxClass,fill:role==='red'?'#C93C4A':'#0B63CE',
    stroke:role==='red'?'#7A1F2B':'#003B8E','stroke-width':3
  }));
  const available=playerW-seedW-nocW-16;
  if(side==='left'){
    drawLine(svg,x+seedW,y-actualH/2,x+seedW,y+actualH/2);
    drawLine(svg,x+seedW+nocW,y-actualH/2,x+seedW+nocW,y+actualH/2);
    drawText(svg,x+seedW/2,y,p.no,seedClass);
    drawText(svg,x+seedW+nocW/2,y,p.noc,nocClass);
    drawFittedCompetitorLabel(svg,x+seedW+nocW+8,y,p.name,nameClass,'start',available,actualH-7);
    return {outX:x+playerW,y,player:p,hidden:false};
  }
  drawLine(svg,x+playerW-seedW,y-actualH/2,x+playerW-seedW,y+actualH/2);
  drawLine(svg,x+playerW-seedW-nocW,y-actualH/2,x+playerW-seedW-nocW,y+actualH/2);
  drawText(svg,x+playerW-seedW/2,y,p.no,seedClass);
  drawText(svg,x+playerW-seedW-nocW/2,y,p.noc,nocClass);
  drawFittedCompetitorLabel(svg,x+playerW-seedW-nocW-8,y,p.name,nameClass,'end',available,actualH-7);
  return {outX:x,y,player:p,hidden:false};
}

function getWinnerInfoByMatchNo(matchNo){
    const prefix = getCurrentMatchPrefix();

    const rawNumber = String(matchNo || '').trim();
    const fullMatch = CURRENT_KEY.startsWith('Kyeorugi - ')
        ? rawNumber
        : (/^\d{5}$/.test(rawNumber) ? rawNumber : prefix + rawNumber.padStart(3, '0'));

    const row = (CURRENT_RESULTS || []).find(r => {
        const rm = String(
            r["Match"] ||
            r["match"] ||
            ""
        ).trim().toUpperCase();

        return rm === fullMatch.toUpperCase();
    });

    if(!row){
        return {
            noc:"",
            side:""
        };
    }

    const result = String(
        row["Result"] ||
        row["result"] ||
        ""
    ).toUpperCase();

    const nocBlue = String(
        row["NOC Blue"] ||
        row["NOC_Blue"] ||
        ""
    ).trim();

    const nocRed = String(
        row["NOC Red"] ||
        row["NOC_Red"] ||
        ""
    ).trim();

    // CHEONG = BLUE / xanh
    if(result.includes("CHEONG") || result.includes("BLUE")){
        return {
            noc:nocBlue,
            side:"blue"
        };
    }

    // HONG = RED / đỏ
    if(result.includes("HONG") || result.includes("RED")){
        return {
            noc:nocRed,
            side:"red"
        };
    }

    if(nocBlue && result.includes(nocBlue.toUpperCase())){
        return {
            noc:nocBlue,
            side:"blue"
        };
    }

    if(nocRed && result.includes(nocRed.toUpperCase())){
        return {
            noc:nocRed,
            side:"red"
        };
    }

    return {
        noc:"",
        side:""
    };
}

function getWinnerNocByMatchNo(matchNo){
    return getWinnerInfoByMatchNo(matchNo).noc || "";
}

function getWinnerRole(winnerNoc, topPlayer, bottomPlayer, finalSide = ""){

    const win = String(winnerNoc || "").trim().toUpperCase();
    if(!win) return "";

    if(finalSide === "blue") return "blue";
    if(finalSide === "red") return "red";
    if(finalSide === "left") return "blue";
    if(finalSide === "right") return "red";

    const topNoc = String(topPlayer?.noc || "").trim().toUpperCase();
    const bottomNoc = String(bottomPlayer?.noc || "").trim().toUpperCase();

    if(topNoc && win === topNoc) return "blue";
    if(bottomNoc && win === bottomNoc) return "red";

    return "blue";
}

function drawMatchCode(
    svg,
    x,
    y,
    text,
    cfg,
    topPlayer = null,
    bottomPlayer = null,
    finalSide = "",
    isChampion = false
){
    const resultInfo = getWinnerInfoByMatchNo(text);
    const winnerNoc = resultInfo.noc || "";
    const showText = winnerNoc || String(text || "").trim();
    const boxW = Math.max(42, 18 + String(showText).length * 8);
    const resultSide = resultInfo.side;
    const cls = resultSide === 'red' ? 'sigma-svg-code-box winner-red' :
                resultSide === 'blue' ? 'sigma-svg-code-box winner-blue' : 'sigma-svg-code-box';
    const textCls = resultSide === 'red' ? 'sigma-svg-winner-code-red' :
                    resultSide === 'blue' ? 'sigma-svg-winner-code-blue' : 'sigma-svg-code';

    svg.appendChild(svgEl("rect", {
        x:x - boxW / 2,
        y:y - cfg.codeH / 2,
        width:boxW,
        height:cfg.codeH,
        rx:2,
        class:cls
    }));

    drawText(svg, x, y, showText, textCls);
    return showText;
}

function drawSvgSide(svg, slots, side, matchNoMap, cfg){
  const { svgW, finalX, finalPad, playerW, startY, rowGap, jointOffset, codeGap, playerGap } = cfg;

  const rounds = Math.log2(slots.length);
  const firstX = side === "left" ? 0 : svgW - playerW;
  const dir = side === "left" ? 1 : -1;
  const startOutX = side === "left" ? firstX + playerW : firstX;
  const targetX = side === "left" ? finalX - finalPad : finalX + finalPad;
  const roundGap = Math.max(70, Math.abs(targetX - startOutX) / Math.max(rounds, 1));

  let current = [];

  for(let i = 0; i < slots.length; i++){
      const y = startY + i * rowGap;
      current.push(drawPlayer(svg, slots[i], firstX, y, side, cfg));
  }

  let roundIndex = 0;

  while(current.length > 1){
    const next = [];

    for(let i = 0; i < current.length; i += 2){
      const a = current[i];
      const b = current[i + 1];

      const aBye = isByePlayer(a.player);
      const bBye = isByePlayer(b.player);
      // Cứ vào mỗi trận mới là ép lại màu theo vị trí thật của trận đó
      if(a.player && !aBye){
          a.player.role = "blue";
      }

      if(b.player && !bBye){
          b.player.role = "red";
      }

      // QUAN TRỌNG:
      // Mỗi trận mới phải xác nhận lại:
      // người / nhánh phía trên = xanh
      // người / nhánh phía dưới = đỏ
      if(a.player && !aBye){
          a.player.role = "blue";
      }

      if(b.player && !bBye){
          b.player.role = "red";
      }

      const midY = (a.y + b.y) / 2;
      const jointX = a.outX + dir * jointOffset;
      const outX = a.outX + dir * roundGap;

      if(aBye && bBye){
        next.push({
            outX,
            y:midY,
            player:{ name:"BYE" },
            hidden:true
        });
        continue;
      }

      if(aBye || bBye){
          const winner = aBye ? b : a;

          drawLine(svg, winner.outX, winner.y, jointX, winner.y);
          drawLine(svg, jointX, winner.y, jointX, midY);
          drawLine(svg, jointX, midY, outX, midY);

          // Qua BYE thì chưa quyết màu vòng sau.
          // Vòng sau sẽ xét lại theo vị trí trên/dưới.
          next.push({
              outX,
              y:midY,
              player:{
                  ...winner.player,
                  role:""
              },
              hidden:false
          });

          continue;
      }

      drawLine(svg, a.outX, a.y, jointX, a.y);
      drawLine(svg, b.outX, b.y, jointX, b.y);
      drawLine(svg, jointX, a.y, jointX, b.y);

      const codeX = jointX + dir * codeGap;
      const lineBreak = cfg.codeW / 2 + playerGap;

      if(side === "left"){
        drawLine(svg, jointX, midY, codeX - lineBreak, midY);
        drawLine(svg, codeX + lineBreak, midY, outX, midY);
      }else{
        drawLine(svg, jointX, midY, codeX + lineBreak, midY);
        drawLine(svg, codeX - lineBreak, midY, outX, midY);
      }

      const pairIndex = Math.floor(i / 2);
      const matchNo = matchNoMap[`${side}-${roundIndex}-${pairIndex}`] || "";
      const winnerNoc = matchNo ? getWinnerNocByMatchNo(matchNo) : "";

      const winnerRole = getWinnerRole(
          winnerNoc,
          a.player,
          b.player
      );

      let nextRole = "";

      const nextIndex = Math.floor(i / 2);

      if(current.length === 2){
          nextRole = side === "left" ? "blue" : "red";
      }else{
          nextRole = (nextIndex % 2 === 0) ? "blue" : "red";
      }

      drawMatchCode(
          svg,
          codeX,
          midY,
          matchNo,
          cfg,
          a.player,
          b.player,
          nextRole
      );

      next.push({
          outX,
          y:midY,
          player:{
              no:"",
              noc:winnerNoc || "",
              name:winnerNoc ? winnerNoc : "WINNER",
              role:""
          },
          hidden:false
      });
    }

    current = next;
    roundIndex++;
  }

  return current[0] || null;
}

function getPseudoWinnerForNumbering(a, b){
    const aBye = isByePlayer(a);
    const bBye = isByePlayer(b);

    if(aBye && bBye){
        return { name:"BYE" };
    }

    if(aBye) return b;
    if(bBye) return a;

    return { name:"WINNER" };
}

let KYEORUGI_OFFICIAL_MATCHES = {};

async function loadKyeorugiOfficialNumbers(key){
    KYEORUGI_OFFICIAL_MATCHES = {};
    if(!String(key || '').startsWith('Kyeorugi - ')) return;
    try{
        const res = await sigmaFetch('/api/kyeorugi/official-match-numbers?key=' + encodeURIComponent(key));
        const data = await res.json();
        if(!res.ok){
            console.warn('[KYEORUGI MATCH NUMBERS]', data.error || res.status);
            return;
        }
        KYEORUGI_OFFICIAL_MATCHES = data.numbers || {};
    }catch(err){
        console.warn('[KYEORUGI MATCH NUMBERS]', err);
    }
}

function buildRoundMatchNumberMap(leftSlots, rightSlots){
    const map = {};
    let counter = 1;

    let leftCurrent = leftSlots.map(p => ({...p}));
    let rightCurrent = rightSlots.map(p => ({...p}));

    let roundIndex = 0;

    while(leftCurrent.length > 1 || rightCurrent.length > 1){

        const processSide = (sideName, current) => {
            const next = [];

            for(let i = 0; i < current.length; i += 2){
                const a = current[i];
                const b = current[i + 1];

                const pairIndex = Math.floor(i / 2);

                const aBye = isByePlayer(a);
                const bBye = isByePlayer(b);

                if(!aBye && !bBye){
                    map[`${sideName}-${roundIndex}-${pairIndex}`] =
                        String(counter).padStart(3, "0");

                    counter++;
                }

                next.push(getPseudoWinnerForNumbering(a, b));
            }

            return next;
        };

        if(leftCurrent.length > 1){
            leftCurrent = processSide("left", leftCurrent);
        }

        if(rightCurrent.length > 1){
            rightCurrent = processSide("right", rightCurrent);
        }

        roundIndex++;
    }

    const leftFinalBye = isByePlayer(leftCurrent[0]);
    const rightFinalBye = isByePlayer(rightCurrent[0]);

    if(!leftFinalBye && !rightFinalBye){
        map["final"] = String(counter).padStart(3, "0");
    }

    if(!String(CURRENT_KEY || '').startsWith('Kyeorugi - ')){
        // Read *actual* Make Matchs codes. Never manufacture Match IDs from positions.
        // A saved match may use the opposite Blue/Red orientation, or a Winner <Match>
        // reference for an earlier match. Resolve bracket nodes from leaves to Final.
        const size = leftSlots.length + rightSlots.length;
        const totalRounds = Math.log2(size);
        const phases = ['Final', 'Semi Final', 'Quarter Final', 'Round of 16',
                        'Round of 32', 'Round of 64'];
        const norm = x=>String(x??'').trim().toUpperCase().replace(/\s+/g,' ');
        const phaseNorm = x=>{
            const raw=norm(x).replace(/[^A-Z0-9]/g,'');
            const aliases={R64:'ROUNDOF64',R32:'ROUNDOF32',R16:'ROUNDOF16',
                QF:'QUARTERFINAL',QUAR:'QUARTERFINAL',QUARTERFINALS:'QUARTERFINAL',
                SF:'SEMIFINAL',SEMI:'SEMIFINAL',SEMIFINALS:'SEMIFINAL',
                FINALS:'FINAL'};
            return aliases[raw]||raw;
        };
        const schedule = (CURRENT_POOMSAE_MATCHES||[])
            .filter(r=>/^\d{5}$/.test(String(r.Match||'').trim()));
        const used = new Set(), official = {};
        const makeNode = p=>({
            nocs:(!p || isByePlayer(p)) ? new Set() : new Set([norm(p.noc)].filter(Boolean)),
            sources:new Set()
        });
        const merged = (a,b)=>({
            nocs:new Set([...(a?.nocs||[]),...(b?.nocs||[])]),
            sources:new Set([...(a?.sources||[]),...(b?.sources||[])])
        });
        const getSide=(r, side)=>({
            noc:norm(r['NOC '+side]),athlete:norm(r['Athlete '+side])
        });
        function eligible(side,node){
            if(!node) return false;
            const values=[side.noc,side.athlete].filter(Boolean);
            for(const code of (node.sources||[])){
                if(values.some(v=>v.replace(/[^A-Z0-9]/g,'')==='WINNER'+code)) return true;
            }
            return values.some(v=>node.nocs.has(v));
        }
        let left=leftSlots.map(makeNode), right=rightSlots.map(makeNode);
        function resolveNode(a,b,phase,slot){
            if(!a?.nocs?.size && !a?.sources?.size) return b;
            if(!b?.nocs?.size && !b?.sources?.size) return a;
            const candidates=schedule.filter(r=>{
                const code=String(r.Match).trim();
                if(used.has(code) || phaseNorm(r.Phase)!==phaseNorm(phase)) return false;
                const blue=getSide(r,'Blue'), red=getSide(r,'Red');
                return (eligible(blue,a)&&eligible(red,b)) ||
                       (eligible(red,a)&&eligible(blue,b));
            });
            const next=merged(a,b);
            if(candidates.length===1){
                const code=String(candidates[0].Match).trim();
                used.add(code);
                official[slot]=code;
                next.sources=new Set([code]);
            }else{
                // More than one candidate: ambiguous; no invented number.
                official[slot]='—';
            }
            return next;
        }
        for(let stage=0;stage<totalRounds-1;stage++){
            const phase=phases[totalRounds-1-stage];
            const advance=(arr,side)=>{
                const next=[];
                for(let j=0;j<arr.length;j+=2){
                    next.push(resolveNode(arr[j],arr[j+1],phase,`${side}-${stage}-${j/2}`));
                }
                return next;
            };
            left=advance(left,'left');
            right=advance(right,'right');
        }
        if(left[0] && right[0]) resolveNode(left[0],right[0],'Final','final');
        const mapped=Object.values(official).filter(v=>/^\d{5}$/.test(v)).length;
        if(schedule.length && mapped===0){
            console.warn('[POOMSAE MATCH NUMBERS] No safely matched bracket nodes:',{
                division:CURRENT_KEY, schedule:schedule.length, bracketSize:size,
                sample:schedule.slice(0,3)
            });
        }
        return official;
    }
    if(String(CURRENT_KEY || '').startsWith('Kyeorugi - ')){
        const fixed = {};
        Object.keys(map).forEach(slot => {
            fixed[slot] = KYEORUGI_OFFICIAL_MATCHES[slot] || map[slot];
        });
        return fixed;
    }
    return map;
}

function drawSvgFinal(svg, leftEnd, rightEnd, matchNoMap, cfg){
  const { finalX, finalPad, codeW, playerGap } = cfg;
  let finalY = 270;
  if(leftEnd && rightEnd) finalY = (leftEnd.y + rightEnd.y) / 2;

  const lineBreak = codeW / 2 + playerGap;

  if(leftEnd){
    drawLine(svg, leftEnd.outX, leftEnd.y, finalX - finalPad, finalY);
    drawLine(svg, finalX - finalPad, finalY, finalX - lineBreak, finalY);
  }

  if(rightEnd){
    drawLine(svg, rightEnd.outX, rightEnd.y, finalX + finalPad, finalY);
    drawLine(svg, finalX + finalPad, finalY, finalX + lineBreak, finalY);
  }

  const matchNo = matchNoMap["final"] || "";

  drawMatchCode(
      svg,
      finalX,
      finalY,
      matchNo,
      cfg,
      leftEnd ? leftEnd.player : null,
      rightEnd ? rightEnd.player : null,
      "",
      true
  );
}

function getDisplayNames(row){
  const names = [];

  const ignoreKeys = [
    "idx","id","no.","no","noc","country","gender","position",
    "event_1","event_2","event_3","event_4","event_5","event_6","event_7","event_8","event_9","event_10"
  ];

  if(CURRENT_KEY && CURRENT_KEY.startsWith('Kyeorugi - ')){
    return String(row?.['Full name'] || '');
  }
  Object.keys(row || {}).forEach(k => {
    const key = String(k || "").toLowerCase().trim();
    const value = String(row[k] || "").trim();

    if(!value) return;
    if(ignoreKeys.includes(key)) return;
    if(key.startsWith("event")) return;

    const isNameField =
      key.includes("name") ||
      key.includes("athlete") ||
      key.includes("member") ||
      key.includes("team") ||
      key.includes("pair") ||
      key.includes("player");

    const looksLikePersonName =
      value.split(/\s+/).length >= 2 &&
      !/^(MALE|FEMALE|VIE|THA|PHI|INA|SIN|KOR|IND|ITA|MAS|LAO|CAM|TPE)$/i.test(value);

    if((isNameField || looksLikePersonName) && !names.includes(value)){
      names.push(value);
    }
  });

  return names.join(" | ");
}

function updateSigmaDivisionTitle(){

    const titleEl = document.getElementById("sigmaDivisionTitle");
    if(!titleEl) return;

    titleEl.textContent =
        String(CURRENT_KEY || "")
            .replace(/\s*-\s*/g, " ");
}

function isCutOffDivision(){
  const key = String(CURRENT_KEY || "").toLowerCase();

  if(key.includes("freestyle")) return true;
  if(key.includes("demonstration")) return true;
  if(key.includes("demon")) return true;

  const results = CURRENT_RESULTS || [];
  return results.some(r => {
    const match = String(r["Match"] || r["match"] || "").trim().toUpperCase();
    const prefix = getCurrentMatchPrefix().toUpperCase();
    const method = String(r["Method"] || r["method"] || "").toLowerCase();

    return (!prefix || match.startsWith(prefix)) && method.includes("cut");
  });
}

function getResultScore(row){
  const fields = [
    "Total Point Blue",
    "Blue Point 1",
    "Total",
    "Score",
    "score",
    "_score"
  ];

  for(const f of fields){
    const v = row[f];
    if(v !== undefined && v !== null && v !== ""){
      const n = Number(v);
      if(!Number.isNaN(n)) return n;
    }
  }

  return 0;
}
function getResultPhase(row){
  return String(
    row["Phase"] ||
    row["phase"] ||
    row["Round"] ||
    row["round"] ||
    ""
  ).trim();
}

function getCutoffCompetitorCount(athletes){
  const nos = new Set();

  (athletes || []).forEach(a => {
    const no = String(a["No."] || "").trim();
    if(no) nos.add(no);
  });

  return nos.size;
}

function buildCutoffResult(athletes){
  const summary=document.getElementById('sigmaRoundSummary');
  if(summary) summary.textContent=`${getCutoffCompetitorCount(athletes)} participants | Cut-off`; 
  const box = document.getElementById("sigmaBracket");
  if(!box) return;

  const totalAthletes = getCutoffCompetitorCount(athletes);

  const phases = [];

  if(totalAthletes > 32){
    phases.push({name:"Round of 64", size:totalAthletes});
    phases.push({name:"Round of 32", size:32});
    phases.push({name:"Round of 16", size:16});
  }else if(totalAthletes > 16){
    phases.push({name:"Round of 32", size:totalAthletes});
    phases.push({name:"Round of 16", size:16});
  }else if(totalAthletes > 8){
    phases.push({name:"Round of 16", size:totalAthletes});
  }

  phases.push({name:"Final", size:Math.min(8, totalAthletes)});

  const phaseRows = {};
  phases.forEach(p => phaseRows[p.name] = []);

  (CURRENT_RESULTS || []).forEach(r => {
    const match = String(r["Match"] || r["match"] || "").trim().toUpperCase();
    const prefix = getCurrentMatchPrefix().toUpperCase();

    if(prefix && !match.startsWith(prefix)) return;

    const phase = getResultPhase(r) || phases[0].name;
    if(!phaseRows[phase]) return;

    phaseRows[phase].push({
      noc:String(r["NOC Blue"] || r["NOC_Blue"] || "").trim(),
      name:String(r["Athlete Blue"] || r["Athlete_Blue"] || "").trim(),
      score:getResultScore(r)
    });
  });

  let html = "";

  phases.forEach(phase => {
    const rows = phaseRows[phase.name] || [];
    rows.sort((a,b) => b.score - a.score);

    const tableRows = [];

    for(let i = 1; i <= phase.size; i++){
      const r = rows[i - 1];

      tableRows.push(`
        <tr>
          <td>${i}</td>
          <td>${r ? esc(r.noc) : ""}</td>
          <td>${r ? esc(shortenName(r.name)) : ""}</td>
          <td>${r ? r.score.toFixed(3) : ""}</td>
        </tr>
      `);
    }

    html += `
      <div class="cutoff-phase-box" id="cutoffPhaseBox_${phases.indexOf(phase)}" data-phase-title="${esc(phase.name)}">
        <div class="cutoff-phase-title-row">
            <div class="cutoff-phase-title">${esc(phase.name)}</div>

            <div class="cutoff-phase-actions">
                <button class="cutoff-print-btn" type="button" onclick="printCutoffPhase(${phases.indexOf(phase)}, 'print')">
                    🖨
                </button>

                <button class="cutoff-print-btn" type="button" onclick="printCutoffPhase(${phases.indexOf(phase)}, 'pdf')">
                    📄
                </button>
            </div>
        </div>

        <table class="cutoff-table">
          <thead>
            <tr>
              <th style="width:70px">Rank</th>
              <th style="width:120px">NOC</th>
              <th>Full name</th>
              <th style="width:120px">Score</th>
            </tr>
          </thead>
          <tbody>
            ${tableRows.join("")}
          </tbody>
        </table>
      </div>
    `;
  });

  box.innerHTML = `<div class="cutoff-result-wrap">${html}</div>`;
}

function computeVisibleRoles(slots){
    const roles = new Array(slots.length).fill("");

    function isByeSlot(p){
        return String(p?.name || "").trim().toUpperCase() === "BYE";
    }

    function walk(start, end){
        if(end - start === 1){
            if(isByeSlot(slots[start])) return null;
            return {
                unresolved:[start]
            };
        }

        const mid = Math.floor((start + end) / 2);
        const left = walk(start, mid);
        const right = walk(mid, end);

        if(!left && !right) return null;
        if(left && !right) return left;
        if(!left && right) return right;

        // Khi 2 nhánh gặp nhau thành trận thật:
        // nhánh trên/trái = xanh, nhánh dưới/phải = đỏ
        (left.unresolved || []).forEach(idx => {
            if(!roles[idx]) roles[idx] = "blue";
        });

        (right.unresolved || []).forEach(idx => {
            if(!roles[idx]) roles[idx] = "red";
        });

        // Sau khi đã thành trận thật thì không kéo màu lên vòng sau nữa
        return {
            unresolved:[]
        };
    }

    walk(0, slots.length);

    return roles;
}

function buildSigmaBracket(athletes){
  const box = document.getElementById("sigmaBracket");
  if(!box) return;

  const grouped = {};
  const currentMeta=(FLAT||[]).find(m=>m.key===CURRENT_KEY)||{};
  const isSolo=/\bindividual\b/i.test(String(currentMeta.division||CURRENT_KEY)) &&
      !/\b(team|pair|mixed team)\b/i.test(String(currentMeta.division||CURRENT_KEY));
  const nocCountries = new Map();
  (INFORMATION_ROWS||[]).forEach(row=>{
    const noc=String(row.NOC||'').trim().toUpperCase();
    if(noc && row.Country && !nocCountries.has(noc))nocCountries.set(noc,String(row.Country).trim());
  });

  [...athletes].forEach(a => {
    const no = Number(a["No."] || 0);
    if(!no) return;

    const key = String(no);
    const name = getDisplayNames(a);

    if(!grouped[key]){
      grouped[key] = {
        no:no,
        noc:a["NOC"] || "",
        country:String(a["Country"] || a.country || nocCountries.get(String(a["NOC"]||'').trim().toUpperCase()) || '').trim(),
        names:[],
        gender:a["Gender"] || ""
      };
    }

    if(!grouped[key].country && (a.Country||a.country)) grouped[key].country=String(a.Country||a.country).trim();
    if(name && !grouped[key].names.includes(name)){
      grouped[key].names.push(name);
    }
  });

  const players = Object.values(grouped)
    .sort((a,b) => a.no - b.no)
    .map(p => ({
      no:p.no,
      noc:p.noc,
      name:isSolo ? p.names.join(" | ") : (p.country || nocCountries.get(String(p.noc).toUpperCase()) || p.noc),
      gender:p.gender
    }));

  const bracketSize = getBracketSize(players.length);
  const summary=document.getElementById('sigmaRoundSummary');
  if(summary) summary.textContent=`${players.length} participants | ${bracketSize === 2 ? "Final" : "Round of " + bracketSize}`;
  const halfCount = bracketSize / 2;
  const seedMap = {};
  players.forEach(p => { if(p.no) seedMap[p.no] = p; });

  const slots = buildSeedOrder(bracketSize).map(seed => {
    return seedMap[seed] || { no:"", noc:"", name:"BYE", gender:"" };
  });

  // Resolve first ACTUAL match for each athlete, skipping BYE slots.
  // R64 -> Semi: the upper route is blue and the lower route is red.
  // If an athlete enters only at Final, left is blue and right is red.
  const leftHalf = slots.slice(0, halfCount);
  const rightHalf = slots.slice(halfCount);
  const leftRoles = computeVisibleRoles(leftHalf);
  const rightRoles = computeVisibleRoles(rightHalf);

  const leftSlots = leftHalf.map((p, i) => ({
    ...p,
    role: isByePlayer(p) ? "" : (leftRoles[i] || "blue")
  }));

  const rightSlots = rightHalf.map((p, i) => ({
    ...p,
    role: isByePlayer(p) ? "" : (rightRoles[i] || "red")
  }));

  const svgW = 1650;
  const ystep = bracketSize <= 16 ? 78 : bracketSize <= 32 ? 64 : 56;
  // Header at top of SVG; first athlete immediately below it.
  const headerY = 36;
  const gridStart = 58;
  const topY = bracketSize === 2 ? 132 : 112;
  const svgH = topY + (halfCount - 1) * ystep + (bracketSize === 2 ? 132 : 110);
  const gridEnd = svgH - 28;

  // Compact athlete cards leave six equally spaced phase columns per side.
  const cfg = {
    playerW: 310,
    playerH: 42,
    seedW: 42,
    nocW: 56,
    codeH: 23
  };

  const leftX = 30;
  const rightX = svgW - 30 - cfg.playerW;
  const centerX = svgW / 2;

  const firstEdge = leftX + cfg.playerW;
  const gap = (centerX - firstEdge) / 6;
  const leftPhaseX = [1,2,3,4,5].map(i => firstEdge + i * gap);
  const rightPhaseX = leftPhaseX.map(x => svgW - x);
  const phaseLabels = ["R64", "R32", "R16", "Quar", "Semi"];

  box.innerHTML = `<svg id="sigmaSvg" viewBox="0 0 ${svgW} ${svgH}" class="sigma-svg"></svg>`;
  const svg = document.getElementById("sigmaSvg");

  const guideLayer = svgEl("g");
  const lineLayer = svgEl("g");
  const playerLayer = svgEl("g");
  const codeLayer = svgEl("g");
  const headerLayer = svgEl("g");

  svg.appendChild(guideLayer);
  svg.appendChild(lineLayer);
  svg.appendChild(playerLayer);
  svg.appendChild(codeLayer);
  svg.appendChild(headerLayer);

  function slotY(idx){
    return topY + idx * ystep;
  }

  function spanMid(lo, hi){
    return (slotY(lo) + slotY(hi)) / 2;
  }

  // Xác định bracket hiện tại dùng từ round nào:
  // 64 => hiện R64, R32, R16, Quar, Semi
  // 32 => hiện R32, R16, Quar, Semi
  // 16 => hiện R16, Quar, Semi
  // 8  => hiện Quar, Semi
  // 4  => hiện Semi
  const totalRounds = Math.log2(bracketSize);          // 64=6, 32=5, 16=4, 8=3, 4=2
  const firstVisiblePhaseIndex = Math.max(0, 6 - totalRounds);

  for(let i = 0; i < phaseLabels.length; i++){
    const visible = i >= firstVisiblePhaseIndex;

    if(visible){
      drawPhaseHeader(headerLayer, leftPhaseX[i], headerY, phaseLabels[i]);
      drawPhaseHeader(headerLayer, rightPhaseX[i], headerY, phaseLabels[i]);
      drawGuideLine(guideLayer, leftPhaseX[i], gridStart, gridEnd);
      drawGuideLine(guideLayer, rightPhaseX[i], gridStart, gridEnd);
    }
  }

  drawPhaseHeader(headerLayer, centerX, headerY, "Final");
  drawGuideLine(guideLayer, centerX, gridStart, gridEnd);

  const matchNoMap = buildRoundMatchNumberMap(leftSlots, rightSlots);

  function winner(a, b){
    if(!a || isByePlayer(a)) return b;
    if(!b || isByePlayer(b)) return a;
    return { no:"", noc:"", name:"WINNER", gender:"" };
  }

  function renderHalf(side, halfSlots){
    const x0 = side === "left" ? leftX : rightX;
    const phaseX = side === "left" ? leftPhaseX : rightPhaseX;
    const prevStartX = side === "left" ? (x0 + cfg.playerW) : x0;

    let current = [];

    for(let i = 0; i < halfSlots.length; i++){
      const y = slotY(i);
      // Upper position = blue; lower position = red on both bracket halves.
      // Recreate the player so previous draw/state cannot override its color.
      // R64 -> Semi: position within EACH pairing determines color.
      // Top = blue, bottom = red (same on both halves, even when other slot is BYE).
      // Final is the sole exception: left finalist = blue, right finalist = red.
      const firstRoundPlayer = {
        ...halfSlots[i],
        role: halfSlots[i].role
      };
      drawPlayer(playerLayer, firstRoundPlayer, x0, y, side, cfg);
      current.push({
        y:y,
        player:firstRoundPlayer,
        lo:i,
        hi:i
      });
    }

    let prevX = prevStartX;
    const roundCount = Math.log2(bracketSize);

    for(let stage = 0; stage < roundCount - 1; stage++){
      const phaseIndex = 6 - roundCount + stage;
      const jointX = phaseX[phaseIndex];
      const next = [];

      for(let j = 0; j < current.length; j += 2){
        const a = current[j];
        const b = current[j + 1];

        const spanLo = Math.min(a.lo, b.lo);
        const spanHi = Math.max(a.hi, b.hi);
        const gridY = spanMid(spanLo, spanHi);

        const aBye = !a.player || isByePlayer(a.player);
        const bBye = !b.player || isByePlayer(b.player);

        if(aBye && bBye){
          next.push({
            y:gridY,
            player:{ name:"BYE" },
            lo:spanLo,
            hi:spanHi
          });
          continue;
        }

        if(aBye || bBye){
          const active = aBye ? b : a;
          drawLine(lineLayer, prevX, active.y, jointX, active.y);

          next.push({
            y:active.y,
            player:active.player,
            lo:spanLo,
            hi:spanHi
          });
          continue;
        }

        drawLine(lineLayer, prevX, a.y, jointX, a.y);
        drawLine(lineLayer, prevX, b.y, jointX, b.y);
        drawLine(lineLayer, jointX, a.y, jointX, b.y);

        const pairIndex = Math.floor(j / 2);
        const matchNo = matchNoMap[`${side}-${stage}-${pairIndex}`] || "";

        drawMatchCode(
          codeLayer,
          jointX,
          gridY,
          matchNo,
          cfg,
          a.player,
          b.player
        );

        next.push({
          y:gridY,
          player:winner(a.player, b.player),
          lo:spanLo,
          hi:spanHi
        });
      }

      current = next;
      prevX = jointX;
    }

    return {
      x:prevX,
      y:current[0].y,
      player:current[0].player
    };
  }

  const leftEnd = renderHalf("left", leftSlots);
  const rightEnd = renderHalf("right", rightSlots);
  const finalY = spanMid(0, halfCount - 1);

  // Two competitors: one Final only, with horizontal connectors
  // reaching the central match box at the same Y coordinate.
  const finalMatchCode = matchNoMap["final"] || "";
  const finalInfo = getWinnerInfoByMatchNo(finalMatchCode);
  const finalLabel = finalInfo.noc || String(finalMatchCode).trim();
  const finalBoxHalf = Math.max(42, 18 + finalLabel.length * 8) / 2;
  if(bracketSize === 2){
    if(leftEnd?.player && !isByePlayer(leftEnd.player))
      drawLine(lineLayer, leftEnd.x, finalY, centerX - finalBoxHalf, finalY);
    if(rightEnd?.player && !isByePlayer(rightEnd.player))
      drawLine(lineLayer, centerX + finalBoxHalf, finalY, rightEnd.x, finalY);
  } else {
    if(leftEnd?.player && !isByePlayer(leftEnd.player))
      drawLine(lineLayer, leftEnd.x, leftEnd.y, centerX, finalY);
    if(rightEnd?.player && !isByePlayer(rightEnd.player))
      drawLine(lineLayer, rightEnd.x, rightEnd.y, centerX, finalY);
  }

  if(
    leftEnd?.player && !isByePlayer(leftEnd.player) &&
    rightEnd?.player && !isByePlayer(rightEnd.player)
  ){
    drawMatchCode(
      codeLayer,
      centerX,
      finalY,
      matchNoMap["final"] || "",
      cfg,
      leftEnd.player,
      rightEnd.player,
      "",
      true
    );
  }
}

function getSigmaPrintEventTitle(){
    const title = document.querySelector(".header-title")?.textContent || "";
    const date = document.querySelector(".header-date")?.textContent || "";
    const location = document.querySelector(".header-location")?.textContent || "";

    const logoImg = document.querySelector(".logoBox img");
    let logo = "";

    if(logoImg){
        logo = logoImg.getAttribute("src") || "";
        if(logo && !logo.startsWith("http") && !logo.startsWith("data:")){
            logo = new URL(logo, window.location.href).href;
        }
    }

    return {
        title:String(title || "").trim(),
        date:String(date || "").trim(),
        location:String(location || "").trim(),
        logo:String(logo || "").trim()
    };
}

function getSigmaCurrentDivisionTitle(){
    const title = document.getElementById("sigmaDivisionTitle")?.textContent || CURRENT_KEY || "";
    return String(title || "").trim();
}

function makePrintDocumentHtml(printTitle, contentHtml, printMode){
    const event = getSigmaPrintEventTitle();

    return `
<!doctype html>
<html lang="vi">
<head>
<link rel="icon" type="image/png" sizes="any" href="/portal-logo?v=20261118" />
<link rel="apple-touch-icon" href="/portal-logo?v=20261118" />
<meta charset="utf-8">
<title>${esc(printTitle)}</title>

<style>
    *{
        box-sizing:border-box;
    }

    @page{
        size:A4 landscape;
        margin:10mm;
    }

    body{
        margin:0;
        background:white;
        color:#001b33;
        font-family:Arial, Helvetica, sans-serif;
        -webkit-print-color-adjust:exact!important;
        print-color-adjust:exact!important;
    }

    .print-page{
        width:100%;
        padding:0;
        background:white;
    }

    .print-header{
        display:grid;
        grid-template-columns:110px minmax(0,1fr) 110px;
        align-items:center;
        gap:12px;
        border-bottom:4px solid #ffd34d;
        padding:8px 0 10px;
        margin-bottom:12px;
    }

    .print-logo-box{
        display:flex;
        align-items:center;
        justify-content:center;
    }

    .print-logo-box img{
        max-width:120px;
        max-height:82px;
        object-fit:contain;
        background:white;
    }

    .print-title-box{
        text-align:center;
        min-width:0;
    }

    .print-event-title{
        color:#ff8f0f;
        font-size:26px;
        font-weight:900;
        text-transform:uppercase;
        letter-spacing:.8px;
        line-height:1.12;
    }

    .print-event-date{
        color:#003b8e;
        font-size:14px;
        font-weight:900;
        margin-top:4px;
    }

    .print-event-location{
        color:#005f8f;
        font-size:14px;
        font-weight:900;
        margin-top:3px;
    }

    .print-main-title{
        text-align:center;
        color:#003b8e;
        font-size:24px;
        font-weight:900;
        letter-spacing:3px;
        margin:10px 0 4px;
    }

    .print-division-title{
        text-align:center;
        color:#001b33;
        font-size:17px;
        font-weight:900;
        margin-bottom:12px;
    }

    .print-round-summary{ text-align:center; font-size:13px; font-weight:800; margin:-5px 0 9px; color:#003b8e; }
    .print-mode{
        text-align:right;
        color:#666;
        font-size:11px;
        font-weight:700;
        margin-bottom:5px;
    }

    .print-content{
        width:100%;
        overflow:visible;
    }

    svg{
        width:100%!important;
        height:auto!important;
        max-height:165mm;
        display:block;
        background:white!important;
        border:2px solid #2f6fa3!important;
        border-radius:8px;
    }

    .sigma-svg{
        background:white!important;
    }

    .sigma-svg-line{
        stroke:#1b5e8f!important;
        stroke-width:1.8!important;
    }
    /* Bracket header styles must be repeated in the separate print page. */
    .sigma-svg-phase-box{
        fill:#f5f8fc!important;
        stroke:#cbd5e1!important;
        stroke-width:1.2!important;
    }
    .sigma-svg-phase-text{
        fill:#17324d!important;
        font-size:12px!important;
        font-weight:800!important;
    }
    .sigma-svg-guide{
        stroke:#9db7cb!important;
        stroke-width:1!important;
        stroke-dasharray:4 6!important;
        opacity:.65!important;
    }
    svg *{
        -webkit-print-color-adjust:exact!important;
        print-color-adjust:exact!important;
    }

    .sigma-svg-box{
        fill:white!important;
        stroke:#2f6fa3!important;
        stroke-width:2!important;
    }

    .sigma-svg-box.player-blue{
        fill:#0B63CE!important;
        stroke:#003B8E!important;
    }

    .sigma-svg-box.player-red{
        fill:#C93C4A!important;
        stroke:#7A1F2B!important;
    }

    .sigma-svg-name-blue,
    .sigma-svg-noc-blue,
    .sigma-svg-seed-blue,
    .sigma-svg-name-red,
    .sigma-svg-noc-red,
    .sigma-svg-seed-red{
        fill:white!important;
        font-weight:900!important;
    }

    .sigma-svg-code-box{
        fill:white!important;
        stroke:#e5b900!important;
        stroke-width:2!important;
    }

    .sigma-svg-code{
        fill:#111111!important;
        stroke:none!important;
        font-weight:900!important;
    }

    .sigma-svg-code-box.winner-blue{
        fill:#0B63CE!important;
        stroke:#003B8E!important;
    }

    .sigma-svg-code-box.winner-red{
        fill:#C93C4A!important;
        stroke:#7A1F2B!important;
    }

    /* ===== Champion Final Box - Print/PDF ===== */
    .sigma-svg-code-box.champion{
        fill:#f6d56b!important;
        stroke:#ffbf00!important;
        stroke-width:3.5!important;
    }

    .sigma-svg-winner-code-champion{
        fill:#ffffff!important;
        font-size:15px!important;
        font-weight:900!important;
        paint-order:stroke!important;
        stroke:#5b4100!important;
        stroke-width:2px!important;
        stroke-linejoin:round!important;
    }

    .sigma-svg-champion-cup{
        font-size:42px!important;
        font-weight:900!important;
    }

    .sigma-svg-winner-code-blue,
    .sigma-svg-winner-code-red{
        fill:white!important;
        font-weight:900!important;
    }

    .cutoff-phase-box{
        border:2px solid #2f6fa3;
        border-radius:12px;
        overflow:hidden;
        background:white;
        page-break-inside:avoid;
    }

    .cutoff-phase-title-row,
    .cutoff-phase-title{
        background:#123c67!important;
        color:#ffd34d!important;
        text-align:center!important;
        font-size:22px!important;
        font-weight:900!important;
        padding:12px!important;
    }

    .cutoff-phase-actions{
        display:none!important;
    }

    .cutoff-table{
        width:100%;
        border-collapse:collapse;
        background:white;
    }

    .cutoff-table th{
        background:#0f2e4e!important;
        color:white!important;
        font-size:13px;
        padding:9px;
        border:1px solid #2f6fa3;
        text-align:center;
    }

    .cutoff-table td{
        color:#001b33!important;
        font-size:13px;
        font-weight:800;
        padding:9px;
        border:1px solid #aac5df;
        text-align:center;
    }

    .cutoff-table td:first-child{
        color:#003B8E!important;
        font-weight:900;
    }

    table{
        width:100%;
        border-collapse:collapse;
    }

    @media print{
        .no-print{
            display:none!important;
        }

        body{
            background:white!important;
        }
    }

/* Khóa lỗi sidebar rớt xuống dưới header khi đóng menu trong Sigma */
.layout.result-mode:not(.menu-open) .sidebar{
    display:none!important;
}

.layout.result-mode.menu-open .sidebar{
    display:block!important;
}

</style>
</head>

<body>
    <div class="print-page">
        <div class="print-header">
            <div class="print-logo-box">
                ${event.logo ? `<img src="${esc(event.logo)}">` : ""}
            </div>

            <div class="print-title-box">
                <div class="print-event-title">${esc(event.title)}</div>
                <div class="print-event-date">${esc(event.date)}</div>
                <div class="print-event-location">${esc(event.location)}</div>
            </div>

            <div></div>
        </div>

        <div class="print-division-title">${esc(printTitle)}</div>
        <div class="print-round-summary">${esc(document.getElementById('sigmaRoundSummary')?.textContent || '')}</div>

        <div class="print-content">
            ${contentHtml}
        </div>
    </div>
</body>
</html>
    `;
}

function openSigmaPrintWindow(printTitle, contentHtml, mode){
    const win = window.open("", "_blank", "width=1200,height=800");

    if(!win){
        alert("Trình duyệt đang chặn popup. Ken cho phép popup để in/xuất PDF nhé.");
        return;
    }

    win.document.open();
    win.document.write(makePrintDocumentHtml(printTitle, contentHtml, mode));
    win.document.close();

    win.onload = () => {
        setTimeout(() => {
            win.focus();
            win.print();
        }, 350);
    };
}

function printSigmaCurrent(mode){
    const divisionTitle = getSigmaCurrentDivisionTitle();

    if(isCutOffDivision()){
        const box = document.getElementById("sigmaBracket");
        if(!box){
            alert("Chưa có nội dung Sigma để in.");
            return;
        }

        openSigmaPrintWindow(
            divisionTitle,
            box.innerHTML,
            mode
        );

        return;
    }

    const svg = document.getElementById("sigmaSvg");

    if(!svg){
        alert("Chưa có sơ đồ Sigma để in.");
        return;
    }

    const svgHtml = svg.outerHTML;

    openSigmaPrintWindow(
        divisionTitle,
        svgHtml,
        mode
    );
}

function printCutoffPhase(index, mode){
    const phaseBox = document.getElementById(`cutoffPhaseBox_${index}`);

    if(!phaseBox){
        alert("Không tìm thấy khung round để in.");
        return;
    }

    const clone = phaseBox.cloneNode(true);

    clone.querySelectorAll(".cutoff-phase-actions").forEach(el => el.remove());

    const phaseTitle = phaseBox.getAttribute("data-phase-title") || "Cutoff Round";
    const divisionTitle = getSigmaCurrentDivisionTitle();

    openSigmaPrintWindow(
        `${divisionTitle} - ${phaseTitle}`,
        clone.outerHTML,
        mode
    );
}

document.getElementById("btnResult")?.addEventListener("click", async () => {
  const sigmaView = document.getElementById("sigmaView");
  const athleteCard = document.querySelector(".athlete-card");
  const sidebar = document.querySelector(".sidebar");
  const layout = document.querySelector(".layout");
  const selectedBox = document.querySelector(".selectedBox");

  if(!window.currentAthletes || !window.currentAthletes.length){
    alert("Chưa có danh sách VĐV để vẽ Sigma");
    return;
  }

  if(layout) layout.classList.add("result-mode");
  document.body.classList.add("sigma-result-mode");
  if(sidebar) sidebar.classList.add("hidden");
  if(athleteCard) athleteCard.classList.add("hidden");
  if(selectedBox) selectedBox.classList.add("hidden");
  if(sigmaView) sigmaView.classList.remove("hidden");

  updateSigmaDivisionTitle();
  // Load the shared athlete registration table for official Country labels.
  // Event draw data is not modified; failure simply falls back to existing NOC.
  const meta=(FLAT||[]).find(m=>m.key===CURRENT_KEY)||{};
  const single=/\bindividual\b/i.test(String(meta.division||CURRENT_KEY)) &&
       !/\b(pair|team)\b/i.test(String(meta.division||CURRENT_KEY));
  if(!single && (!INFORMATION_ROWS || !INFORMATION_ROWS.length)){
    try{
      const response=await sigmaFetch('/api/information-list',{cache:'no-store'});
      if(response.ok){const data=await response.json();if(Array.isArray(data))INFORMATION_ROWS=data;}
    }catch(error){console.warn('Country lookup unavailable:',error);}
  }
  if(isCutOffDivision()){
      buildCutoffResult(window.currentAthletes);
  }else{
      buildSigmaBracket(window.currentAthletes);
  }
});

document.getElementById("btnReturn")?.addEventListener("click", () => {
  const sigmaView = document.getElementById("sigmaView");
  const athleteCard = document.querySelector(".athlete-card");
  const sidebar = document.querySelector(".sidebar");
  const layout = document.querySelector(".layout");
  const selectedBox = document.querySelector(".selectedBox");

  if(layout){
      layout.classList.remove("result-mode");
      layout.classList.remove("menu-open");
  }

  document.body.classList.remove("sigma-result-mode");
  document.body.classList.remove("sidebar-open");

  if(sidebar) sidebar.classList.remove("hidden");
  if(athleteCard) athleteCard.classList.remove("hidden");
  if(selectedBox) selectedBox.classList.remove("hidden");
  if(sigmaView) sigmaView.classList.add("hidden");
});

function isMobileView(){
    return window.matchMedia("(max-width:900px)").matches;
}

function closeSidebarOnMobile(){
    const layout = document.querySelector(".layout");
    const body = document.body;
    const sidebar = document.querySelector(".sidebar");

    if(!layout) return;

    // Trong Sigma Result, chọn division xong thì đóng panel menu lại
    if(layout.classList.contains("result-mode")){
        layout.classList.remove("menu-open");
        body.classList.remove("sidebar-open");

        if(sidebar){
            sidebar.classList.add("hidden");
        }

        return;
    }

    if(!isMobileView()) return;

    layout.classList.remove("menu-open");
    body.classList.remove("sidebar-open");
}

function toggleSidebar(){
    const layout = document.querySelector(".layout");
    const body = document.body;
    const sidebar = document.querySelector(".sidebar");

    if(!layout) return;

    // Khi đang ở Sigma Result:
    // bấm ☰ mở menu thì bỏ hidden
    // bấm ☰ lần nữa đóng menu thì add hidden lại
    if(layout.classList.contains("result-mode")){
        const willOpen = !layout.classList.contains("menu-open");

        if(willOpen){
            if(sidebar){
                sidebar.classList.remove("hidden");
            }

            layout.classList.add("menu-open");
            body.classList.add("sidebar-open");
        }else{
            layout.classList.remove("menu-open");
            body.classList.remove("sidebar-open");

            if(sidebar){
                sidebar.classList.add("hidden");
            }
        }

        return;
    }

    if(isMobileView()){
        layout.classList.toggle("menu-open");
        body.classList.toggle("sidebar-open", layout.classList.contains("menu-open"));
        return;
    }

    const collapsed = layout.classList.toggle("sidebar-collapsed");
    body.classList.toggle("sidebar-collapsed-desktop", collapsed);

    setTimeout(setupDragScroll, 80);
}

function setupSidebarToggle(){
    const btn = document.getElementById("btnToggleSidebar");
    const backdrop = document.getElementById("sidebarBackdrop");

    if(btn){
        btn.addEventListener("click", toggleSidebar);
    }

    if(backdrop){
        backdrop.addEventListener("click", closeSidebarOnMobile);
    }

    window.addEventListener("resize", () => {
        const layout = document.querySelector(".layout");

        if(!layout) return;

        if(!isMobileView()){
            layout.classList.remove("menu-open");
            document.body.classList.remove("sidebar-open");
        }else{
            layout.classList.remove("sidebar-collapsed");
            document.body.classList.remove("sidebar-collapsed-desktop");
        }
    });
}

function setupDragScroll(){
    document.querySelectorAll(".table-scroll").forEach(scroller => {
        if(scroller.dataset.dragReady === "1") return;
        scroller.dataset.dragReady = "1";

        let isDown = false;
        let startX = 0;
        let startY = 0;
        let scrollLeft = 0;
        let scrollTop = 0;

        scroller.addEventListener("mousedown", e => {
            isDown = true;
            scroller.classList.add("dragging");
            startX = e.pageX - scroller.offsetLeft;
            startY = e.pageY - scroller.offsetTop;
            scrollLeft = scroller.scrollLeft;
            scrollTop = scroller.scrollTop;
        });

        scroller.addEventListener("mouseleave", () => {
            isDown = false;
            scroller.classList.remove("dragging");
        });

        scroller.addEventListener("mouseup", () => {
            isDown = false;
            scroller.classList.remove("dragging");
        });

        scroller.addEventListener("mousemove", e => {
            if(!isDown) return;

            e.preventDefault();

            const x = e.pageX - scroller.offsetLeft;
            const y = e.pageY - scroller.offsetTop;

            const walkX = x - startX;
            const walkY = y - startY;

            scroller.scrollLeft = scrollLeft - walkX;
            scroller.scrollTop = scrollTop - walkY;
        });

        // Laptop: giữ Shift + lăn chuột để chạy ngang
        scroller.addEventListener("wheel", e => {
            if(e.shiftKey){
                e.preventDefault();
                scroller.scrollLeft += e.deltaY;
            }
        }, {passive:false});
    });
}

document.getElementById("btnSigmaPrint")?.addEventListener("click", () => {
    printSigmaCurrent("print");
});

document.getElementById("btnSigmaPdf")?.addEventListener("click", () => {
    printSigmaCurrent("pdf");
});

document.getElementById("btnOpenInfoList")?.addEventListener("click", () => {
    showInfoListView();
    closeSidebarOnMobile();
});

document.getElementById("btnOpenRanking")?.addEventListener("click", () => {
    showRankingView();
    closeSidebarOnMobile();
});

document.getElementById('btnOpenKyeorugiRanking')?.addEventListener('click',()=>{
    showKyeorugiRankingView();closeSidebarOnMobile();
});

document.getElementById("btnOpenMedalStandings")?.addEventListener("click", () => {
    showMedalStandingsView();
    closeSidebarOnMobile();
    setTimeout(setupDragScroll, 80);
});

setupSidebarToggle();

loadMenu().then(() => {
    setupDragScroll();
});

setInterval(() => {
    if(CURRENT_KEY) loadResults(CURRENT_KEY);
}, 5000);

</script>
</body>
</html>
"""

@app.route("/")
def index():
    # /api/menu populates Division asynchronously; do not query all matches here.
    # Doing it here previously doubled the expensive Supabase reads on each page load.
    setup = load_setup()
    event_name = setup.get("event") or setup.get("event_name") or "Taekwondo Event"

    date_from = setup.get("date_from", "")
    date_to = setup.get("date_to", "")

    event_date = format_event_date_range(date_from, date_to)

    return render_template_string(
        INDEX_HTML,
        event_name=event_name,
        event_date=event_date,
        event_location=setup.get("location", ""),
        logo_file=url_for('portal_logo'),
        event_prefix=getattr(g, 'event_prefix', ''),
        registration_url=getattr(g, 'event_prefix', '') + '/registration',
    )

def convert_dict(d):
    if isinstance(d, defaultdict):
        d = dict(d)

    if isinstance(d, dict):
        return {k: convert_dict(v) for k, v in d.items()}

    return d


# Cache only the public Division menu. Entries are isolated by event_code.
# A short TTL prevents repeated reads while preserving timely match updates.
_SIGMA_MENU_CACHE = {}
_SIGMA_MENU_CACHE_TTL = 12.0

@app.route("/api/menu")
def api_menu():
    import time as _time
    key = _request_event_code()
    now = _time.monotonic()
    entry = _SIGMA_MENU_CACHE.get(key)
    if entry is not None and now - entry[0] < _SIGMA_MENU_CACHE_TTL:
        return jsonify(entry[1])
    try:
        event_name, menu, flat = build_menu()
        payload = {"event_name": event_name, "menu": menu, "flat": flat}
        _SIGMA_MENU_CACHE[key] = (now, payload)
        return jsonify(payload)
    except Exception as exc:
        app.logger.error("Sigma Division menu failed event=%s: %s", key, type(exc).__name__)
        # Do not return stale data from another event or pretend it is empty.
        return jsonify({"error": "Không tải được Division; kiểm tra kết nối Supabase ở Terminal."}), 503

# NOC / Country reference for the Poomsae Team/Athlete List only.
# Cache lookup data briefly so switching divisions does not repeatedly download the registry.
_POOMSAE_COUNTRY_CACHE = {'until': 0.0, 'by_noc': {}, 'event_code': None}

def _poomsae_country_lookup():
    import time
    now = time.monotonic()
    if (now < _POOMSAE_COUNTRY_CACHE['until'] and
            _POOMSAE_COUNTRY_CACHE['event_code'] == _request_event_code()):
        return _POOMSAE_COUNTRY_CACHE['by_noc']
    by_noc = defaultdict(set)
    for row in get_information_list_rows():
        noc = str(row.get('NOC') or '').strip().upper()
        country = str(row.get('Country') or '').strip()
        if noc and country:
            by_noc[noc].add(country)
    # Never assign a potentially incorrect country when the same NOC has conflicting names.
    resolved = {noc: next(iter(names)) for noc, names in by_noc.items() if len(names) == 1}
    _POOMSAE_COUNTRY_CACHE.update(until=now + 300, by_noc=resolved, event_code=_request_event_code())
    return resolved


@app.route("/api/athletes")
def api_athletes():
    key = request.args.get("key", "").strip()
    if key.startswith('Kyeorugi - '):
        try:
            selected = next((x for x in _kyeorugi_divisions() if x['key'] == key), None)
            if not selected:
                return jsonify([])
            # The Registration desktop application writes draw results here.
            # Draw numbers, not athlete registration order, determine Sigma slots.
            draw_file = os.path.join(str(_PROJECT_ROOT), 'kyeorugi data', 'Random Sigma.json')
            if _request_event_code() != get_legacy_event_code() or not os.path.isfile(draw_file):
                return jsonify([])
            with open(draw_file, 'r', encoding='utf-8') as fh:
                stored_draws = json.load(fh).get('draws', {})
            age_codes = {'U9': 'U9', 'U12': 'U12', 'Cadet (U14)': 'U14',
                         'Junior (U17)': 'U17', 'Senior (O18)': 'O18'}
            age = age_codes.get(selected['age'], selected['age'])
            style = ('Individual' if selected['group'] == 'Individuals'
                     else selected['team_kind'])
            weight = selected['weight']
            division_key = (style, age, selected['gender'])
            candidate = None
            for _, draw in stored_draws.items():
                if not isinstance(draw, dict) or not isinstance(draw.get('participants'), list):
                    continue
                if (str(draw.get('style', '')).strip(), str(draw.get('age', '')).strip(),
                        str(draw.get('gender', '')).strip()) != division_key:
                    continue
                if style == 'Individual':
                    if re.sub(r'\s*kg\s*$', '', str(draw.get('weight', '')).strip(), flags=re.I).replace(' ', '') != re.sub(r'\s*kg\s*$', '', weight, flags=re.I).replace(' ', ''):
                        continue
                if candidate is None or str(draw.get('created_at', '')) > str(candidate.get('created_at', '')):
                    candidate = draw
            if not candidate:
                return jsonify([])
            # Obtain current official weigh-ins by athlete ID; never rely on a stale
            # weight snapshot saved with the random draw.
            info = {}
            try:
                for a in _pages(K_INFO):
                    athlete_id = str(a.get('GAL/ID ATHLETE') or '').strip()
                    if athlete_id:
                        info[athlete_id] = a
            except Exception as exc:
                print('[KYEORUGI ATHLETES] Supabase weigh-in lookup:', type(exc).__name__)
            result = []
            for p in candidate['participants']:
                try:
                    no = int(p.get('random_number'))
                except (ValueError, TypeError):
                    continue
                athlete_ids = p.get('athlete_codes') or [p.get('key')]
                person = info.get(str(athlete_ids[0]), {}) if athlete_ids else {}
                def fmt_weight(value):
                    if value is None or str(value).strip() == '':
                        return ''
                    try:
                        return format(Decimal(str(value).replace(',', '.')), '.3f').replace('.', ',')
                    except (InvalidOperation, ValueError):
                        return ''
                w1 = fmt_weight(person.get('Gen. Weigh-in 1', p.get('_weigh1')))
                w2 = fmt_weight(person.get('Gen. Weigh-in 2', p.get('_weigh2')))
                weights = ' | '.join(x for x in (w1, w2) if x)
                result.append({'No.': no, 'No.Random': no,
                               'NOC': person.get('NOC') or p.get('noc') or '',
                               'Country': person.get('Country') or p.get('country') or '',
                               'Full name': person.get('Full name') or p.get('names') or '',
                               'Gender': person.get('Gender') or p.get('gender') or '',
                               'Weigh-in': weights or '—'})
            result.sort(key=lambda r: r['No.'])
            return jsonify(result)
        except Exception as exc:
            return _api_error(exc, 503)
    data = load_event_json()
    athletes = data.get("divisions", {}).get(key, [])
    if not athletes:
        return jsonify([])
    # Respect Country stored in Sigma. Fill only missing values from the ONE shared registry.
    # No write to Supabase or local JSON; these are response copies.
    athletes = [dict(a) for a in athletes]
    if any(not str(a.get('Country') or '').strip() for a in athletes):
        try:
            countries = _poomsae_country_lookup()
            for athlete in athletes:
                if not str(athlete.get('Country') or '').strip():
                    athlete['Country'] = countries.get(str(athlete.get('NOC') or '').strip().upper(), '')
        except Exception as exc:
            print('[POOMSAE COUNTRY LOOKUP]', type(exc).__name__)
    return jsonify(athletes)


@app.route("/api/kyeorugi/official-match-numbers")
def api_kyeorugi_official_match_numbers():
    """Read authoritative official numbering from match_numbers.json.

    Validate the draw fingerprint. Never assign display-only sequential codes
    when no official match number has been set.
    """
    import hashlib
    import math
    key = request.args.get('key', '').strip()
    if not key.startswith('Kyeorugi - '):
        return jsonify({})
    try:
        selected = next((x for x in _kyeorugi_divisions() if x['key'] == key), None)
        if not selected:
            return jsonify({})
        age_codes = {'U9': 'U9', 'U12': 'U12', 'Cadet (U14)': 'U14',
                     'Junior (U17)': 'U17', 'Senior (O18)': 'O18'}
        age = age_codes.get(selected['age'], selected['age'])
        style = ('Individual' if selected['group'] == 'Individuals'
                 else selected['team_kind'])
        weight = selected['weight'] if style == 'Individual' else ''
        event_key = '|'.join((style, age, selected['gender'], weight))

        # First priority: Supabase official schedule, not the desktop-only JSON.
        # Stable Sigma Key is derived exactly as in match_editor._stable_key.
        def stable_key(side, stage, pair):
            raw = json.dumps([event_key, side, stage, pair], ensure_ascii=False)
            return hashlib.sha256(raw.encode('utf-8')).hexdigest()

        matched_rows = []
        all_rows = _pages('Kyeorugi Matchs', columns='"Division","Match","Sigma Key","Local Match","Draw Hash"')
        expected_div = str(selected['schedule_division']).strip().casefold()
        for row in all_rows:
            if str(row.get('Division') or '').strip().casefold() == expected_div:
                matched_rows.append(row)

        # The same schedule can exist on another machine. Display it even if
        # this Flask host does not have Random Sigma.json or match_numbers.json.
        approved = {}
        for row in matched_rows:
            code = str(row.get('Match') or '').strip()
            key_value = str(row.get('Sigma Key') or '').strip()
            if key_value and re.fullmatch(r'\d{5}', code):
                approved[key_value] = code

        # Infer slots from the saved draw when available, while checking its
        # fingerprint before accepting locally stored official codes.
        home = str(_PROJECT_ROOT)
        candidates = ([
            os.path.join(home, 'kyeorugi data', 'Random Sigma.json'),
            os.path.join(home, 'Random Sigma.json'),
        ] if _request_event_code() == get_legacy_event_code() else [])
        saved_draw = {}
        for path in candidates:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    draws = json.load(f).get('draws') or {}
                saved_draw = draws.get(event_key) or {}
                if not saved_draw:
                    # Registration may save the same category with spaces in '-49 kg'.
                    # Use the actual saved event key for Sigma stable hashes.
                    target = [style.casefold(), age.casefold(),
                              selected['gender'].casefold(),
                              re.sub(r'\s+', '', weight).casefold()]
                    for candidate_key, draw in draws.items():
                        parts = str(candidate_key).split('|')
                        if len(parts) != 4:
                            continue
                        norm = [parts[0].strip().casefold(), parts[1].strip().casefold(),
                                parts[2].strip().casefold(),
                                re.sub(r'\s+', '', parts[3]).casefold()]
                        if norm == target and isinstance(draw, dict):
                            event_key, saved_draw = candidate_key, draw
                            break
                if saved_draw:
                    break
            except (OSError, ValueError, TypeError):
                continue
        participants = saved_draw.get('participants') or []
        n = len(participants)
        if n < 2:
            # Fallback: use match Local Match labels if random draw isn't on host.
            # These are displayed as local identifiers only, never fake 5-digit codes.
            mapped = {}
            for row in matched_rows:
                code = str(row.get('Match') or '').strip()
                local = row.get('Local Match')
                if local and re.fullmatch(r'\d{5}', code):
                    mapped['local-' + str(local)] = code
            return jsonify({'numbers': mapped, 'source': 'supabase',
                            'warning': 'Chưa tìm thấy Random Sigma.json trên máy chủ.'})

        size = 2
        while size < n:
            size *= 2
        if size > 64:
            return jsonify({'numbers': {}, 'error': 'Bracket larger than 64'})
        rounds = size.bit_length() - 1
        digest_data = json.dumps(sorted((str(p.get('key', '')), int(p.get('random_number')))
                                             for p in participants), ensure_ascii=False,
                                 separators=(',', ':'))
        digest = hashlib.sha256(digest_data.encode('utf-8')).hexdigest()

        # Discard stale Supabase numbers from a DIFFERENT draw, when the row
        # has an explicit draw fingerprint.
        approved = {str(r.get('Sigma Key')): str(r.get('Match'))
                    for r in matched_rows
                    if re.fullmatch(r'\d{5}', str(r.get('Match') or ''))
                    and r.get('Sigma Key')
                    and (not r.get('Draw Hash') or r['Draw Hash'] == digest)}
        mapped = {}
        for stage in range(rounds - 1):
            count_per_side = size // (4 * (2 ** stage))
            for side in ('left', 'right'):
                for pair in range(count_per_side):
                    code = approved.get(stable_key(side, stage, pair), '')
                    if code:
                        mapped[f'{side}-{stage}-{pair}'] = code
        code = approved.get(stable_key('final', rounds - 1, 0), '')
        if code:
            mapped['final'] = code

        # Local numbered matches may be available before they sync to Supabase.
        if not mapped:
            number_paths = ([os.path.join(home, 'kyeorugi data', 'match_numbers.json'),
                            os.path.join(home, 'match_numbers.json')]
                            if _request_event_code() == get_legacy_event_code() else [])
            for path in number_paths:
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        entry = (json.load(f).get(event_key) or {})
                    if entry.get('draw_hash') != digest:
                        continue
                    official = entry.get('official') or {}
                    for stage in range(rounds - 1):
                        for side in ('left', 'right'):
                            for pair in range(size // (4 * (2 ** stage))):
                                code = str(official.get(stable_key(side, stage, pair), ''))
                                if re.fullmatch(r'\d{5}', code):
                                    mapped[f'{side}-{stage}-{pair}'] = code
                    code = str(official.get(stable_key('final', rounds - 1, 0), ''))
                    if re.fullmatch(r'\d{5}', code):
                        mapped['final'] = code
                    break
                except (OSError, ValueError, TypeError):
                    continue
        return jsonify({'numbers': mapped, 'source': 'supabase / verified local cache',
                        'set_count': len(mapped)})
    except Exception as exc:
        return _api_error(exc, 503)

@app.route("/api/poomsae/matches")
def api_poomsae_official_matches():
    """Return official match codes from public."Poomsae Matchs" (all courts).

    Some historical court rows have NULL "Poomsae Type" after migration.
    Filter by exact Division, accept blank type ONLY if not ambiguous with another
    explicitly typed division. Never guess from Court or generate a Match ID.
    """
    key = str(request.args.get("key") or "").strip()
    if not key or len(key) > 180 or " - " not in key:
        return jsonify({"error": "Division không hợp lệ."}), 400
    poomsae_type, division = key.split(" - ", 1)
    if poomsae_type not in ("Recognized", "Freestyle", "Demonstration", "Mixer"):
        return jsonify({"error": "Poomsae Type không hợp lệ."}), 400
    division = re.sub(r"\s*\((?:Natl|Inter)\)", "", division, flags=re.I).strip()
    if supabase is None:
        return jsonify({"error": "Chưa kết nối Supabase Poomsae."}), 503
    try:
        rows, offset = [], 0
        fields = '"Match","Phase","Division","Poomsae Type","NOC Blue","NOC Red","Athlete Blue","Athlete Red","Method","Result"'
        while True:
            # Do not require Poomsae Type at database level: migration may
            # preserve NULL types, which otherwise makes the entire draw blank.
            batch = (_scoped_query(supabase, POOMSAE_MATCH_TABLE, fields)
                     .eq("Division", division)
                     .range(offset, offset + 999).execute().data or [])
            rows.extend(batch)
            if len(batch) < 1000:
                break
            offset += 1000
        explicit = {str(r.get("Poomsae Type") or "").strip().casefold()
                    for r in rows if str(r.get("Poomsae Type") or "").strip()}
        requested = poomsae_type.casefold()
        if len(explicit) <= 1 and (not explicit or requested in explicit):
            matches = [r for r in rows if not str(r.get("Poomsae Type") or "").strip()
                       or str(r.get("Poomsae Type") or "").strip().casefold() == requested]
        else:
            matches = [r for r in rows if str(r.get("Poomsae Type") or "").strip().casefold() == requested]
            ignored = len(rows) - len(matches)
            if ignored:
                app.logger.warning('[POOMSAE MATCH] Excluded %d ambiguous/type-mismatched rows for %s', ignored, key)
        phases = {"round of 64":0,"round of 32":1,"round of 16":2,
                  "quarter final":3,"quarterfinal":3,"semi final":4,"semifinal":4,"final":5}
        matches.sort(key=lambda r:(phases.get(str(r.get("Phase") or "").strip().lower(),90),
                                   int(str(r.get("Match"))) if str(r.get("Match") or "").isdigit() else 999999,
                                   str(r.get("Match") or "")))
        app.logger.info('[POOMSAE MATCH] %s: %d schedule rows (of %d division rows)', key, len(matches), len(rows))
        return jsonify(matches)
    except Exception as exc:
        app.logger.error('Poomsae official match lookup failed: %s', type(exc).__name__)
        return jsonify({"error":"Không thể đọc bảng Poomsae Matchs. Kiểm tra Terminal và quyền SELECT Supabase."}),503


@app.route("/api/results")
def api_results():
    key = request.args.get("key", "").strip()
    if key.startswith('Kyeorugi - '):
        try:
            selected = next((d for d in _kyeorugi_divisions() if d['key'] == key), None)
            if not selected:
                return jsonify([])
            target = str(selected['schedule_division']).strip().casefold()
            # Read the authoritative schedule/result, not Poomsae Result.
            # Restrict returned columns to fields needed for Sigma rendering.
            columns = ','.join('"' + x + '"' for x in (
                'Match', 'Division', 'Phase', 'NOC Blue', 'NOC Red',
                'Athlete Blue', 'Athlete Red', 'Result', 'Sigma Key', 'Draw Hash'))
            records = _pages('Kyeorugi Matchs', columns=columns)
            return jsonify([row for row in records
                            if str(row.get('Division') or '').strip().casefold() == target])
        except Exception as exc:
            print('[KYEORUGI RESULTS]', type(exc).__name__, str(exc))
            return _api_error('Không thể tải kết quả Kyeorugi Matchs: ' + str(exc), 503)
    rows = get_results_for_division(key)
    return jsonify(rows)

def _kyeorugi_match_winner(row):
    """Only finalized Blue/Red outcomes. Never infer a winner from points."""
    result = str(row.get('Result') or '').strip().lower()
    blue = str(row.get('NOC Blue') or '').strip()
    red = str(row.get('NOC Red') or '').strip()
    if not result:
        return None
    if any(token in result for token in ('cheong', 'blue')):
        side = 'Blue'
    elif any(token in result for token in ('hong', 'red')):
        side = 'Red'
    elif blue and result == blue.lower():
        side = 'Blue'
    elif red and result == red.lower():
        side = 'Red'
    else:
        return None
    loser = 'Red' if side == 'Blue' else 'Blue'
    def person(which):
        noc = str(row.get('NOC '+which) or '').strip()
        name = str(row.get('Athlete '+which) or '').strip()
        if noc.lower().startswith('winner ') or name.lower().startswith('winner '):
            return {'noc': '', 'name': ''}
        return {'noc': noc, 'name': name}
    return person(side), person(loser)


def _weighting_configuration():
    """Discover age/gender weights; old files may only describe the old event."""
    paths = [
        os.path.join(str(_PROJECT_ROOT), 'weighting.json'),
        os.path.join(str(_PROJECT_ROOT), 'weghting.json'),
        os.path.join(str(_PROJECT_ROOT), 'kyeorugi data', 'weighting.json'),
        os.path.join(str(_PROJECT_ROOT), 'weghting.json'),
        os.path.join(str(_PROJECT_ROOT), 'Setup', 'weighting.json'),
        os.path.join(str(_PROJECT_ROOT), 'weghting.json'),
    ]
    config = {}
    seen = set()
    def insert(age, gender, token):
        m = re.fullmatch(r'([+-])\s*(\d{1,3})(?:\s*kg)?', str(token or '').strip(), re.I)
        if not m or age not in ('U9','U12','U14','U17','O18') or gender not in ('Male','Female'):
            return
        config.setdefault(age+'|'+gender, set()).add(m.group(1)+str(int(m.group(2)))+' kg')
    def traverse(obj, age='', gender=''):
        if isinstance(obj, dict):
            value_age = obj.get('age', obj.get('Age', ''))
            value_gender = obj.get('gender', obj.get('Gender', ''))
            if value_age: age = parse_age(value_age) or age
            if value_gender: gender = parse_gender(value_gender) or gender
            for key,value in obj.items():
                next_age = parse_age(key) or age
                next_gender = parse_gender(key) or gender
                if isinstance(value, (dict,list,tuple)):
                    traverse(value, next_age, next_gender)
                elif isinstance(value,str):
                    if re.fullmatch(r'[+-]\s*\d{1,3}\s*(?:kg)?', key, re.I):insert(next_age,next_gender,key)
                    for part in re.split(r'[,;|]',value):insert(next_age,next_gender,part)
        elif isinstance(obj,(list,tuple)):
            for item in obj:
                if isinstance(item,str):insert(age,gender,item)
                else:traverse(item,age,gender)
    def parse_age(x):
        x=str(x or '').lower()
        match=re.search(r'\b(u9|u12|u14|u17|o18|cadet|junior|senior)\b',x)
        if not match:return ''
        return {'cadet':'U14','junior':'U17','senior':'O18'}.get(match.group(1),match.group(1).upper())
    def parse_gender(x):
        x=str(x or '').lower()
        if re.search(r'\bfemale|women|girls\b',x):return 'Female'
        if re.search(r'\bmale|men|boys\b',x):return 'Male'
        return ''
    source='schedule'
    if _request_event_code() != get_legacy_event_code():
        paths = []  # Do not reuse the prior tournament's local weight categories.
    for path in paths:
        if not os.path.isfile(path):continue
        with open(path,encoding='utf-8') as stream: data=json.load(stream)
        traverse(data)
        source=path
        break
    # Always include divisions already scheduled, even if a file omits them.
    try:
        scheduled = _kyeorugi_divisions()
    except Exception:
        scheduled = []
    for item in scheduled:
        if item.get('group')!='Individuals':continue
        insert(parse_age(item['age']),item['gender'],item['weight'])
    return {'source':source,'categories':{key:sorted(values,key=lambda x:(int(re.search(r'\d+',x).group()),x)) for key,values in config.items()}}

@app.route('/api/kyeorugi/weight-categories')
def api_kyeorugi_weight_categories():
    try:return jsonify(_weighting_configuration())
    except Exception as exc:return _api_error(exc,503)

@app.route('/api/kyeorugi/ranking')
def api_kyeorugi_ranking():
    try:
        divisions = {}
        for row in _pages('Kyeorugi Matchs'):
            name = str(row.get('Division') or '').strip()
            if not name:
                continue
            divisions.setdefault(name, []).append(row)
        cards = []
        try:
            country_by_noc = {str(r.get('NOC') or '').upper().strip(): str(r.get('Country') or '').strip()
                              for r in get_information_list_rows() if r.get('NOC') and r.get('Country')}
        except Exception:
            country_by_noc = {}
        phases = {'final': 'Final', 'semi': 'Semi'}
        medals_template = [('gold','🥇 Gold'),('silver','🥈 Silver'),
                           ('bronze','🥉 Bronze'),('bronze','🥉 Bronze')]
        for division, rows in sorted(divisions.items(), key=lambda x:x[0].casefold()):
            def phase(row):
                return str(row.get('Phase') or '').strip().lower()
            final = [r for r in rows if phase(r) in ('final','f')]
            semis = sorted((r for r in rows if phase(r) in ('semi', 'semifinal', 'semi-final')),
                           key=lambda r: str(r.get('Match') or ''))
            finish = _kyeorugi_match_winner(final[0]) if len(final)==1 else None
            bronze = [_kyeorugi_match_winner(r) for r in semis]
            medalists = [finish[0] if finish else {}, finish[1] if finish else {},
                         bronze[0][1] if len(bronze)>0 and bronze[0] else {},
                         bronze[1][1] if len(bronze)>1 and bronze[1] else {}]
            # Avoid displaying bronze before BOTH semifinal results are known.
            if len(semis)!=2 or any(x is None for x in bronze):
                medalists[2:] = [{},{}]
            is_team = bool(re.search(r'\bteam\b', division, re.I))
            cards.append({'title':division, 'matches':len(rows), 'is_team':is_team,
                          'completed':sum(bool(_kyeorugi_match_winner(r)) for r in rows),
                          'medals':[{'medal':medal, 'label':label,
                                     'noc':p.get('noc',''),
                                     'name':(country_by_noc.get(str(p.get('noc','')).upper().strip()) or p.get('noc',''))
                                            if is_team and p.get('noc') else p.get('name','')}
                                    for (medal,label),p in zip(medals_template,medalists)]})
        return jsonify({'cards': cards})
    except Exception as exc:
        return _api_error(exc, 503)


@app.route("/api/information-list")
def api_information_list():
    try:
        return jsonify(get_information_list_rows())
    except Exception as exc:
        return _api_error(exc, 503)


@app.route("/api/all-results")
def api_all_results():
    try:
        rows = []
        for offset in range(0, 50000, 500):
            batch = (_scoped_query(supabase, RESULT_TABLE)
                     .range(offset, offset + 499).execute().data or [])
            rows.extend(batch)
            if len(batch) < 500: break

        for r in rows:
            r["_score"] = safe_float(
                r.get("Total")
                or r.get("total")
                or r.get("Score")
                or r.get("score")
                or r.get("Total Point Blue")
                or r.get("Blue Point 1")
            )

        return jsonify(rows)

    except Exception as e:
        print("All results load error:", e)
        return jsonify([])

# Registration Form is isolated from score/ranking functions.
# All writes go through the server, never through browser-side Supabase credentials.
# Registration Form: fully integrated below in this single script.
"""Self-contained registration page and vetted server-side registration APIs.

Install with install_registration_routes(app, db_getter, year_getter, weights_getter).
Do not expose a service-role Supabase key in HTML or JavaScript.
"""

from datetime import date
from functools import wraps
from secrets import token_urlsafe
import os
import re
import threading
import time
import unicodedata

from flask import jsonify, render_template_string, request, session

UNIT_TABLE = 'Team Information'
INFO_TABLE = 'Information Lists'
EVENT_FIELDS = [f'Event_{n}' for n in range(1, 13)]
EVENT_RULES = {
    'Kyorugi Individual': (1, 'Kyorugi', 'Individual'),
    'Kyorugi Team': (2, 'Kyorugi', 'Team'),
    'Kyorugi Mixed Team': (3, 'Kyorugi', 'Mixed Team'),
    'Recognized Individual': (4, 'Poomsae', 'Recognized Individual'),
    'Recognized Pair': (5, 'Poomsae', 'Recognized Pair'),
    'Recognized Team': (6, 'Poomsae', 'Recognized Team'),
    'Freestyle Individual': (7, 'Poomsae', 'Freestyle Individual'),
    'Freestyle Pair': (8, 'Poomsae', 'Freestyle Pair'),
    'Freestyle Team': (9, 'Poomsae', 'Freestyle Team'),
    'Freestyle Mixed Team': (10, 'Poomsae', 'Freestyle Mixed Team'),
    'Breaking Individual': (11, 'Poomsae', 'Breaking Individual'),
    'Demonstration': (12, 'Poomsae', 'Demonstration'),
}
POSITIONS = {'Official', 'Head of Delegation', 'Head Coach', 'Coach',
             'Athlete', 'Doctor', 'Masseur', 'Referee', 'Manager', 'Caregiver'}
WEIGHTS_SENIOR = {
    'Male': ['-54', '-58', '-63', '-68', '-74', '-80', '-87', '+87'],
    'Female': ['-46', '-49', '-53', '-57', '-62', '-67', '-73', '+73']
}
_MAX_ROWS = 25
_lock = threading.Lock()
_hits = {}


def _normal(value):
    return re.sub(r'\s+', ' ', str(value or '').strip()).casefold()


def _clean(value, maxlen=150):
    s = re.sub(r'\s+', ' ', str(value or '').strip())
    if not s or len(s) > maxlen or any(ord(c) < 32 for c in s):
        raise ValueError('Thông tin bắt buộc bị thiếu hoặc quá dài.')
    return s


def _limit(action, max_hits=20, period=60):
    # Defense in depth only. For multi-worker production add Redis / proxy rate limit.
    key = ((request.remote_addr or 'unknown'), action)
    now = time.monotonic()
    with _lock:
        entries = [t for t in _hits.get(key, []) if now-t < period]
        if len(entries) >= max_hits:
            return False
        entries.append(now)
        _hits[key] = entries
    return True


def _ages(dob, year):
    try:
        parsed = date.fromisoformat(dob)
    except (TypeError, ValueError):
        raise ValueError('Ngày sinh không hợp lệ, phải theo định dạng YYYY-MM-DD.')
    age = year - parsed.year
    if not 4 <= age <= 85:
        raise ValueError('Năm sinh không phù hợp với giải đấu.')
    if age <= 9:
        code = 'U9'
    elif age <= 12:
        code = 'U12'
    elif age <= 14:
        code = 'U14'
    elif age <= 17:
        code = 'U17'
    else:
        code = 'O18'
    return code, age


def _event_value(rule, detail):
    slot, discipline, style = EVENT_RULES[rule]
    if rule == 'Kyorugi Individual':
        raw = _clean(detail, 12).replace(' ', '').lower().replace('kg', '')
        if not re.fullmatch(r'[+-](?:\d{1,3})(?:[.,]\d)?', raw):
            raise ValueError('Hạng cân phải có dạng -38kg hoặc +45kg.')
        return slot, raw.replace(',', '.') + 'kg'
    if rule == 'Kyorugi Team':
        if detail not in ('Team 3', 'Team 4', 'Team 5'):
            raise ValueError('Đồng đội đối kháng phải chọn Team 3, Team 4 hoặc Team 5.')
        return slot, detail
    if rule == 'Kyorugi Mixed Team':
        return slot, 'Mixed Team'
    return slot, rule


def _api_fail(message, status=400):
    return jsonify({'error': str(message)}), status


def install_registration_routes(app, db_getter, year_getter, weights_getter=None, event_getter=None):
    def client():
        db = db_getter()
        if db is None:
            raise RuntimeError('Chưa kết nối Supabase để ghi đăng ký.')
        return db

    def token_required(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            token = request.headers.get('X-Registration-CSRF', '')
            if not token or token != session.get('registration_csrf'):
                return _api_fail('Phiên đăng ký hết hạn. Hãy tải lại trang.', 403)
            if not _limit(fn.__name__, 15 if fn.__name__ == 'save_athlete' else 30):
                return _api_fail('Thao tác quá nhanh, vui lòng thử lại.', 429)
            if request.content_length is not None and request.content_length > 25000:
                return _api_fail('Dữ liệu gửi vượt giới hạn.', 413)
            return fn(*args, **kwargs)
        return wrapper

    @app.get('/registration')
    def registration_form_page():
        session['registration_csrf'] = token_urlsafe(32)
        year = int(year_getter())
        metadata = event_getter() if event_getter else {}
        name = str(metadata.get('event') or metadata.get('event_name') or 'TAEKWONDO CHAMPIONSHIP')
        event_dates = format_event_date_range(metadata.get('date_from', ''), metadata.get('date_to', ''))
        venue = str(metadata.get('location') or '')
        return render_template_string(REGISTRATION_HTML,
            csrf=session['registration_csrf'], year=year,
            event_name=name, event_dates=event_dates, event_location=venue,
            event_prefix=getattr(g, 'event_prefix', ''),
            event_code=_request_event_code())

    def _directory_rows(db):
        """Read complete Team Information directory (Supabase defaults to 1000 rows)."""
        rows = []
        offset = 0
        while True:
            block = (db.table(UNIT_TABLE).select('id,NOC,Province,Club')
                     .order('id').range(offset, offset + 499).execute().data or [])
            rows.extend(block)
            if len(block) < 500:
                break
            offset += 500
            if offset > 30000:
                raise RuntimeError('Danh mục đơn vị vượt giới hạn kiểm tra.')
        return rows

    def _split_directory_label(value):
        """Return display name and bracketed code, preserving user-defined spelling."""
        raw = re.sub(r'\s+', ' ', str(value or '').strip())
        match = re.fullmatch(r'(.+?)\s*\(([A-Za-z0-9]{2,12})\)', raw)
        if match:
            return match.group(1).strip(), match.group(2).upper()
        return raw, ''

    def _directory_units(rows):
        units = []
        seen = set()
        for row in rows:
            for field in ('NOC', 'Province', 'Club'):
                value = row.get(field)
                if not value or not str(value).strip():
                    continue
                name, code = _split_directory_label(value)
                # Province/Club are Vietnamese delegation levels, not international NOC codes.
                noc = code if field == 'NOC' else 'VIE'
                if field == 'NOC' and not code:
                    continue   # Country entries must carry a grounded bracket code.
                if not name:
                    continue
                unique = (field, _normal(name))
                if unique in seen:
                    continue
                seen.add(unique)
                units.append({'id': int(row['id']), 'type': field,
                              'name': name, 'noc': noc, 'label': str(value).strip()})
        return units

    def _resolve_unit(db, unit_id, unit_type):
        if not isinstance(unit_id, int) or unit_id < 1 or unit_type not in ('NOC', 'Province', 'Club'):
            raise ValueError('Chọn Country, Province hoặc Club hợp lệ trước khi tiếp tục.')
        rows = (db.table(UNIT_TABLE).select('id,NOC,Province,Club')
                .eq('id', unit_id).limit(1).execute().data or [])
        if not rows:
            raise ValueError('Đơn vị không còn trong danh mục. Hãy tải lại.')
        units = [u for u in _directory_units(rows) if u['type'] == unit_type]
        if not units:
            raise ValueError('Đơn vị không hợp lệ hoặc danh mục đã thay đổi.')
        return units[0]

    def _same_registered_unit(row, unit):
        # Historical athlete rows store plain Country and their NOC separately.
        country_name, _ = _split_directory_label(row.get('Country'))
        return (_normal(country_name) == _normal(unit['name']) and
                str(row.get('NOC') or '').strip().upper() == unit['noc'])

    @app.get('/api/registration/units')
    def registration_units():
        try:
            rows = _directory_rows(client())
            return jsonify({'units': _directory_units(rows)})
        except Exception:
            app.logger.exception('Cannot load Team Information directory')
            return _api_fail('Không đọc được public."Team Information"; kiểm tra bảng và quyền SELECT.', 503)

    @app.post('/api/registration/units')
    @token_required
    def registration_add_unit():
        try:
            data = request.get_json(silent=True) or {}
            if data.get('type') != 'Club':
                raise ValueError('Chỉ được thêm Club mới. Country và Province đã có danh mục chuẩn.')
            raw_name = _clean(data.get('name'), 120)
            name, supplied_code = _split_directory_label(raw_name)
            if supplied_code and supplied_code != 'VIE':
                raise ValueError('Club mới chỉ được sử dụng mã (VIE).')
            if not name or len(name) > 115:
                raise ValueError('Tên Club không hợp lệ.')
            if os.environ.get('REGISTRATION_WRITE_ENABLED') != '1':
                raise ValueError('Chưa bật quyền ghi đăng ký trong .env.')
            db = client()
            rows = _directory_rows(db)
            known = _directory_units(rows)
            found = next((u for u in known if u['type'] == 'Club' and _normal(u['name']) == _normal(name)), None)
            if found:
                return jsonify({'unit': found, 'existing': True})
            # Schema has NOC NOT NULL; copy an existing Vietnam entry without inventing a new code.
            vietnam = next((r for r in rows if _split_directory_label(r.get('NOC'))[1] == 'VIE'), None)
            if not vietnam:
                raise ValueError('Danh mục Country chưa có Vietnam (VIE); không thể tạo Club an toàn.')
            label = f'{name} (VIE)'
            created = (db.table(UNIT_TABLE).insert({'NOC': vietnam['NOC'], 'Club': label})
                       .execute().data or [])
            if not created:
                raise RuntimeError('Không nhận được xác nhận thêm Club từ Supabase.')
            entry = {'id': int(created[0]['id']), 'type': 'Club', 'name': name,
                     'noc': 'VIE', 'label': label}
            return jsonify({'unit': entry, 'existing': False})
        except ValueError as exc:
            return _api_fail(exc)
        except Exception:
            app.logger.exception('Cannot add Club to Team Information')
            return _api_fail('Không thêm được Club vào Team Information; kiểm tra quyền INSERT và dữ liệu.', 503)

    @app.get('/api/registration/weights')
    def registration_weights():
        try:
            configured = weights_getter() if weights_getter else {}
            return jsonify({'categories': configured.get('categories', {}), 'senior': WEIGHTS_SENIOR})
        except Exception:
            return jsonify({'categories': {}, 'senior': WEIGHTS_SENIOR})

    def find_gal_record(db, athlete_id):
        # Case-sensitive schema field, quoted for PostgREST reserved '/' character.
        preview = _scoped_query(db, INFO_TABLE).limit(1).execute().data or []
        known = list(preview[0]) if preview else []
        col = next((c for c in known if re.sub(r'[^a-z0-9]', '', c.casefold()) == 'galidathlete'), None)
        col = col or 'GAL/ID ATHLETE'
        filter_col = '"' + col.replace('"', '""') + '"'
        rows = (_scoped_query(db, INFO_TABLE).eq(filter_col, athlete_id)
                .limit(3).execute().data or [])
        if len(rows) > 1:
            raise ValueError('GAL/ID đang có nhiều hồ sơ trùng trong Supabase. Liên hệ quản trị viên.')
        return col, filter_col, (rows[0] if rows else None)

    @app.get('/api/registration/roster')
    def registration_unit_roster():
        """Event-specific, selected-unit roster. No cross-event or cross-unit results."""
        try:
            db = client()
            unit_id = request.args.get('unit_id', type=int)
            unit_type = request.args.get('unit_type', '')
            unit = _resolve_unit(db, unit_id, unit_type)
            results, offset = [], 0
            while True:
                # Historical Club/Province registrations can carry an incorrect NOC
                # (e.g. the club has AFG in old rows while the directory assigns VIE).
                # For domestic units, Country contains the actual unit name and is
                # the roster identity; never silently rewrite historical NOC values.
                # For international Country delegations, NOC remains mandatory.
                query = (db.table(INFO_TABLE)
                         .select('*')
                         .eq('event_code', _request_event_code())
                         .eq('Country', unit['name']))
                if unit['type'] == 'NOC':
                    query = query.eq('NOC', unit['noc'])
                batch = (query.order('Full name')
                         .range(offset, offset + 499).execute().data or [])
                results.extend(batch)
                if len(batch) < 500:
                    break
                offset += 500
                if offset >= 30000:
                    raise RuntimeError('Danh sách đơn vị vượt giới hạn tải.')
            # Verify with the same mapping already used when writing registrations.
            roster = []
            for row in results:
                # The original strict check is still used by LOOKUP and SAVE.
                # For roster display only, match domestic units by registered
                # unit name, allowing legacy NOC anomalies to remain visible.
                country_name, _ = _split_directory_label(row.get('Country'))
                if _normal(country_name) != _normal(unit['name']):
                    continue
                if unit['type'] == 'NOC' and str(row.get('NOC') or '').strip().upper() != unit['noc']:
                    continue
                roster.append({
                    'full_name': str(row.get('Full name') or '').strip(),
                    'athlete_id': str(next((v for k, v in row.items() if re.sub(r'[^a-z0-9]', '', k.casefold()) == 'galidathlete'), '') or '').strip(),
                    'gender': str(row.get('Gender') or '').strip(),
                    'position': str(row.get('Position') or '').strip(),
                    'events': [str(row.get(field) or '').strip() for field in EVENT_FIELDS
                               if str(row.get(field) or '').strip()],
                })
            return jsonify({'unit': unit['name'], 'event_code': _request_event_code(),
                            'rows': roster})
        except ValueError as exc:
            return _api_fail(exc, 400)
        except Exception:
            app.logger.exception('Registration roster load failed')
            return _api_fail('Không đọc được danh sách đăng ký của đơn vị.', 503)

    @app.post('/api/registration/lookup')
    @token_required
    def registration_lookup():
        try:
            data = request.get_json(silent=True) or {}
            athlete_id = _clean(data.get('athlete_id'), 100)
            unit_id = data.get('unit_id')
            unit_type = data.get('unit_type')
            db = client()
            unit = _resolve_unit(db, unit_id, unit_type)
            _, _, row = find_gal_record(db, athlete_id)
            if row is None:
                return jsonify({'found': False, 'event_code': _request_event_code()})
            if not _same_registered_unit(row, unit):
                # Never disclose the other delegation's private athlete profile.
                return _api_fail('GAL/ID đã thuộc một đơn vị khác. Không được đăng ký trùng hoặc tự chuyển đơn vị.', 409)
            fields = {key: (str(row.get(key) or '').strip()) for key in EVENT_FIELDS}
            return jsonify({'found': True, 'event_code': _request_event_code(), 'athlete': {
                'id': athlete_id,
                'full_name': row.get('Full name') or '',
                'dob': str(row.get('DOB') or '')[:10],
                'gender': row.get('Gender') or '',
                'position': row.get('Position') or '',
                'events': fields
            }})
        except ValueError as exc:
            return _api_fail(exc, 409)
        except Exception:
            app.logger.exception('Registration lookup failed')
            return _api_fail('Không tra cứu được hồ sơ từ Supabase.', 503)

    @app.post('/api/registration/save')
    @token_required
    def save_athlete():
        try:
            payload = request.get_json(silent=True) or {}
            unit_id = payload.get('unit_id')
            unit_type = payload.get('unit_type')
            if os.environ.get('REGISTRATION_WRITE_ENABLED') != '1':
                raise ValueError('Chức năng ghi đăng ký chưa được bật trong .env (REGISTRATION_WRITE_ENABLED=1).')
            db = client()
            unit = _resolve_unit(db, unit_id, unit_type)
            athlete_id = _clean(payload.get('athlete_id'), 100)
            full_name = _clean(payload.get('full_name'), 160)
            dob = _clean(payload.get('dob'), 10)
            gender = _clean(payload.get('gender'), 6)
            position = _clean(payload.get('position'), 50)
            if gender not in ('Male', 'Female') or position not in POSITIONS:
                raise ValueError('Gender hoặc Position chưa hợp lệ.')
            code, age = _ages(dob, int(year_getter()))
            inputs = payload.get('events')
            if not isinstance(inputs, list) or len(inputs) > 12:
                raise ValueError('Chỉ được chọn tối đa 12 nội dung.')
            if position != 'Athlete' and inputs:
                raise ValueError('Chỉ Position Athlete mới được đăng ký nội dung thi đấu.')
            assignments = {}
            for item in inputs:
                if not isinstance(item, dict) or item.get('type') not in EVENT_RULES:
                    raise ValueError('Có nội dung đăng ký không hợp lệ.')
                event_type = item['type']
                slot, val = _event_value(event_type, item.get('detail', ''))
                column = f'Event_{slot}'
                if column in assignments and assignments[column] != val:
                    raise ValueError(f'{column}: không thể chọn hai nội dung khác nhau trong cùng một cột.')
                assignments[column] = val
            if len(assignments) != len(inputs):
                raise ValueError('Danh sách nội dung bị trùng, chỉ chọn một lần mỗi loại.')
            # Weight must come from tournament configuration for this gender/age.
            if 'Event_1' in assignments:
                config = weights_getter() if weights_getter else {}
                categories = config.get('categories', {}) if isinstance(config, dict) else {}
                allowed = set()
                for key, values in categories.items():
                    key_parts = str(key).upper()
                    if code in key_parts and gender.upper() in key_parts:
                        allowed.update(str(v).replace(' ', '').lower().replace('kg', '') for v in values)
                if not allowed and code == 'O18':
                    allowed.update(x.lower() for x in WEIGHTS_SENIOR[gender])
                candidate = assignments['Event_1'].lower().replace('kg','')
                if not allowed:
                    raise ValueError('Chưa cấu hình hạng cân cho nhóm tuổi/giới tính này; không thể đăng ký tự do.')
                if candidate not in allowed:
                    raise ValueError('Hạng cân không thuộc danh sách được phép của giải.')
            gal_column, gal_filter, old = find_gal_record(db, athlete_id)
            base = {'event_code': _request_event_code(), gal_column: athlete_id, 'Full name': full_name,
                    'DOB': dob, 'Gender': gender, 'Position': position,
                    'NOC': unit['noc'], 'Country': unit['name']}
            if old:
                if _normal(old.get('Full name')) != _normal(full_name) or str(old.get('DOB') or '')[:10] != dob:
                    raise ValueError('GAL/ID đã thuộc hồ sơ có tên hoặc DOB khác. Hãy tra cứu và dùng thông tin đã đăng ký.')
                if not _same_registered_unit(old, unit):
                    raise ValueError('GAL/ID đang thuộc đơn vị khác. Không tự chuyển đoàn.')
                if str(old.get('Gender') or '').casefold() != gender.casefold():
                    raise ValueError('GAL/ID đã có giới tính khác. Không ghi đè.')
                if _normal(old.get('Position')) != _normal(position):
                    raise ValueError('Không tự đổi Position của hồ sơ đã có. Liên hệ ban tổ chức.')
                expected = payload.get('expected_events')
                if not isinstance(expected, dict) or set(expected) != set(EVENT_FIELDS):
                    raise ValueError('GAL/ID đã tồn tại. Hãy tra cứu để tải nội dung cũ trước khi cập nhật.')
                if payload.get('edit_confirmed') is not True:
                    raise ValueError('Cần xác nhận thay đổi nội dung của hồ sơ đã đăng ký.')
                changes = {}
                for field in EVENT_FIELDS:
                    previous = str(old.get(field) or '').strip()
                    baseline = str(expected.get(field) or '').strip()
                    if previous != baseline:
                        raise ValueError('Nội dung đã được cập nhật ở nơi khác. Vui lòng tra cứu lại trước khi lưu.')
                    updated = assignments.get(field, '')
                    if previous != updated:
                        changes[field] = updated or None
                if not changes:
                    return jsonify({'ok': True, 'message': f'GAL/ID {athlete_id} đã đăng ký; nội dung không thay đổi.', 'updated': False})
                # Optimistic concurrency: write only if the previous Event values still match.
                query = db.table(INFO_TABLE).update(changes).eq('event_code', _request_event_code())
                if 'id' in old and old.get('id') is not None:
                    query = query.eq('id', old['id'])
                else:
                    query = query.eq(gal_filter, athlete_id)
                for field in changes:
                    previous = str(old.get(field) or '').strip()
                    query = query.eq(field, previous) if previous else query.is_(field, 'null')
                changed = query.execute().data or []
                if len(changed) != 1:
                    raise ValueError('Hồ sơ đã thay đổi hoặc chưa xác nhận cập nhật. Tải lại GAL/ID trước khi thử lại.')
                return jsonify({'ok': True, 'message': f'GAL/ID {athlete_id}: đã cập nhật {len(changes)} nội dung; không tạo mã mới.', 'updated': True})
            if payload.get('expected_events') is not None:
                raise ValueError('Không tìm thấy hồ sơ đã tải. Hãy tra cứu lại GAL/ID trước khi lưu.')
            db.table(INFO_TABLE).insert({**base, **assignments}).execute()
            return jsonify({'ok': True, 'message': f'Đã đăng ký GAL/ID {athlete_id} cho giải {_request_event_code()}.', 'event_code': _request_event_code(), 'updated': True})
        except ValueError as exc:
            return _api_fail(exc, 409)
        except Exception:
            app.logger.exception('Registration save failed')
            return _api_fail('Không thể lưu Supabase. Kiểm tra quyền ghi và cấu trúc Information Lists.', 503)


REGISTRATION_HTML = r'''<!doctype html>
<html lang="vi"><head>
<link rel="icon" type="image/png" sizes="any" href="/portal-logo?v=20261118" />
<link rel="apple-touch-icon" href="/portal-logo?v=20261118" /><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Registration Form — Taekwondo</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#07111f;color:#eef6ff;font-family:Arial,Helvetica,sans-serif}button,input,select{font:inherit}
.header{background:linear-gradient(100deg,#03152d,#07519a,#03152d);border-bottom:3px solid #ffd34d;padding:20px 5%;display:flex;align-items:center;justify-content:space-between;gap:20px}.header h1{margin:0;color:#ffb13c;font-size:clamp(23px,3vw,45px);text-shadow:0 3px #132a4c}.header p{margin:6px 0 0;color:#c5e6ff}.back{background:#183e64;color:white;border:1px solid #4278aa;border-radius:9px;padding:12px 16px;text-decoration:none}
.wrap{max-width:1230px;margin:26px auto;padding:0 18px}.panel{background:#0d2238;border:1px solid #2b5274;border-radius:16px;padding:22px;margin:0 0 20px;box-shadow:0 10px 30px #0003}.panel h2{color:#ffd34d;margin:0 0 18px;font-size:22px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.field{display:flex;flex-direction:column;gap:7px}.field label{font-size:14px;color:#b9d5ee;font-weight:bold}.field input,.field select,.event-cell select,.event-cell input{height:43px;border:1px solid #567b9d;border-radius:8px;background:white;color:#11253b;padding:8px 11px;width:100%;min-width:0}.field input:disabled,.field select:disabled,.event-cell select:disabled,.event-cell input:disabled{background:#bcc6d0;color:#596573;opacity:1}.field small{color:#93afc9}.current{color:#7df5b2;font-weight:bold}.table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;min-width:680px}th{text-align:left;background:#154d7a;color:#fff;padding:13px;border-bottom:1px solid #376d9b}td{padding:10px;border-bottom:1px solid #274867}td:nth-child(1){width:30%}td:nth-child(2){width:35%}td:nth-child(3){width:30%}td:nth-child(4){width:5%}.event-cell{min-width:0}.actions{display:flex;justify-content:space-between;gap:14px;margin-top:20px;flex-wrap:wrap}.btn{background:#1768bb;border:0;border-radius:9px;padding:12px 22px;color:white;font-weight:bold;cursor:pointer}.btn.green{background:#18834b}.btn.gold{background:#ffd34d;color:#13243a}.btn.red{background:#952a36}.btn:disabled{opacity:.45;cursor:not-allowed}.hint{color:#adc9e4;font-size:13px;line-height:1.5}.status{padding:11px 14px;border:1px solid #3a688e;border-radius:8px;background:#122e49;white-space:pre-wrap;line-height:1.5;margin-top:13px}.status.error{color:#ffc2c2;border-color:#b94a4a}.status.success{color:#a7f3c1;border-color:#18834b}
.backdrop{position:fixed;inset:0;background:#000b;z-index:40;display:flex;align-items:center;justify-content:center;padding:14px}.dialog{width:min(570px,100%);background:#102a46;border:1px solid #4a7faa;border-radius:16px;padding:24px;box-shadow:0 24px 70px #000b}.dialog h2{margin:0 0 8px;color:#ffd34d}.dialog .field{margin-top:16px}.matches{max-height:180px;overflow:auto;margin-top:5px;border:1px solid #325478;border-radius:8px}.match{width:100%;padding:10px;text-align:left;border:0;border-bottom:1px solid #365576;background:#173c60;color:white;cursor:pointer}.match:hover{background:#25639a}.hidden{display:none!important}
.registration-event-header{display:grid;grid-template-columns:minmax(0,1fr) auto;position:relative;min-height:145px;padding:17px 4% 21px}
.registration-event-header .event-heading{text-align:center;padding-left:110px;min-width:0}
.registration-event-header h1{font-size:clamp(22px,2.65vw,36px);line-height:1.2;text-transform:uppercase;color:#ffad35;letter-spacing:.6px}
.registration-event-header .event-dates,.registration-event-header .event-venue{margin:6px 0 0;color:#d8efff;font-size:clamp(13px,1.3vw,17px);font-weight:700;line-height:1.35}
.registration-event-header .event-venue{color:#9fe7ff}
.registration-title-panel{text-align:center;padding:18px 12px;background:#113a5d}
.registration-title-panel h2{font-size:clamp(27px,3.5vw,40px);letter-spacing:1px;margin:0;color:#ffd34d}
.registration-title-panel p{margin:6px 0 0;color:#bdd5e9;font-size:14px}
.division-panel.inactive{border-color:#405368}
.division-panel.inactive .hint{color:#b9c5d3}
.division-panel.inactive .table-scroll{opacity:.8}
.event-line button:disabled{background:#697583;color:#e2e5e9;cursor:not-allowed}
/* Shared Registration Form layout for EVERY event, without event-specific CSS. */
.wrap{max-width:none;width:min(98%,1920px);margin:22px auto;padding:0 12px}
.registration-workspace{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:16px;align-items:start}
.registration-editor{min-width:0}.registration-editor .panel{margin-bottom:16px}
.registration-roster{min-width:0;margin:0;position:sticky;top:12px;max-height:calc(100dvh - 24px);display:flex;flex-direction:column;overflow:hidden;padding:16px}
.roster-title{color:#ffd34d;font-size:22px;font-weight:900;display:flex;align-items:center;justify-content:space-between;gap:10px}
.roster-refresh{border:1px solid #4b81ad;background:#144269;color:#d8eeff;border-radius:9px;cursor:pointer;font-size:23px;width:37px;height:34px}
.roster-unit{color:#a9d7f3;font-weight:700;font-size:13px;margin:8px 0 12px;overflow-wrap:anywhere}
.roster-stats{display:flex;flex-direction:column;align-items:flex-start;gap:7px;margin-bottom:13px}
.roster-stat-row{display:flex;flex-wrap:wrap;align-items:center;gap:6px;width:100%}
.roster-pill{display:inline-flex;gap:5px;align-items:center;padding:7px 10px;border:1px solid #41709b;background:#164366;border-radius:999px;color:#eef6ff;font-size:12px;font-weight:800;white-space:nowrap}
.roster-pill strong{color:#ffd34d}.roster-pill.main{background:#165b77;border-color:#3a9bad}
.roster-search-wrap{margin-bottom:11px}.roster-search-wrap input{display:block;width:100%;height:42px;padding:10px 12px;background:#f7fbff;color:#122438;border:1px solid #729bbb;border-radius:10px;min-width:0}
.roster-table-scroll{width:100%;min-width:0;overflow:auto;max-height:calc(100dvh - 320px);border:1px solid #305573;border-radius:10px;scrollbar-gutter:stable;overscroll-behavior:contain}
.roster-table{width:max-content;min-width:100%;border-collapse:separate;border-spacing:0;font-size:12px}
.roster-table th,.roster-table td{padding:10px 9px;white-space:nowrap;overflow-wrap:normal;border-right:1px solid #254765;border-bottom:1px solid #254765;font-size:12px}
.roster-table thead th{position:sticky;top:0;z-index:3;background:#164d7a}
.roster-table th:nth-child(1),.roster-table td:nth-child(1){position:sticky;left:0;min-width:43px;width:43px;text-align:center;z-index:2;background:#102b45}
.roster-table th:nth-child(2),.roster-table td:nth-child(2){position:sticky;left:43px;width:130px;min-width:130px;max-width:130px;white-space:normal;overflow-wrap:anywhere;word-break:normal;z-index:2;background:#102b45}
.roster-table th:nth-child(3),.roster-table td:nth-child(3){position:sticky;left:173px;width:92px;min-width:92px;max-width:92px;white-space:normal;overflow-wrap:anywhere;word-break:normal;z-index:2;background:#102b45}
.roster-table thead th:nth-child(-n+3){z-index:5;background:#164d7a}
.roster-table td:nth-child(n+4){min-width:110px;max-width:260px;white-space:normal;overflow-wrap:anywhere}
.roster-table tbody tr:hover td{background:#1b405e}
.roster-footer{color:#9dc7e6;font-size:12px;margin-top:9px;min-height:18px}
@media(max-width:1100px){.registration-workspace{grid-template-columns:minmax(0,1fr)}.registration-roster{position:static;max-height:none}.roster-table-scroll{max-height:480px}}
@media(max-width:680px){.grid{grid-template-columns:1fr}.panel{padding:16px}.header{padding:15px}.back{font-size:13px}.registration-event-header{grid-template-columns:1fr;gap:12px}.registration-event-header .event-heading{padding-left:0}.registration-event-header .back{justify-self:end}.wrap{margin-top:12px}}
</style></head><body>
<header class="header registration-event-header">
  <div class="event-heading"><h1>{{event_name}}</h1>
    {% if event_dates %}<p class="event-dates">{{event_dates}}</p>{% endif %}
    {% if event_location %}<p class="event-venue">{{event_location}}</p>{% endif %}
  </div>
  <a id="backHome" class="back" href="/">← Sigma View</a>
</header>
<main class="wrap">
<section class="panel registration-title-panel"><h2>REGISTRATION FORM</h2></section>
<div class="registration-workspace"><div class="registration-editor">
<section class="panel"><h2>Team Informations</h2><div class="grid"><div class="field"><label>Team / Country</label><input id="selectedName" disabled></div><div class="field"><label>NOC</label><input id="selectedNoc" disabled></div></div><div class="actions"><button class="btn" type="button" onclick="openUnitDialog()">Đổi đơn vị</button><span class="current" id="selectedStatus"></span></div></section>
<section class="panel"><h2>Athlete Informations</h2><div class="grid">
<div class="field"><label>GAL / ID Athlete *</label><input id="athleteId" maxlength="100" autocomplete="off" placeholder="Ví dụ HV-000168"></div>
<div class="field"><label>Fullname *</label><input id="fullName" maxlength="160" placeholder="Nguyen Thien Phung"></div>
<div class="field"><label>DOB (Ngày sinh) *</label><input id="dob" type="date"><small id="ageLabel"></small></div>
<div class="field"><label>Gender *</label><select id="gender"><option value="">— Chọn —</option><option value="Male">Male</option><option value="Female">Female</option></select></div>
<div class="field"><label>Position *</label><select id="position"><option value="">— Chọn —</option><option>Athlete</option><option>Official</option><option>Head of Delegation</option><option>Head Coach</option><option>Coach</option><option>Doctor</option><option>Masseur</option><option>Referee</option><option>Manager</option><option>Caregiver</option></select></div>
</div></section>
<section id="divisionPanel" class="panel division-panel inactive"><h2>Divisions</h2><div class="table-scroll"><table><thead><tr><th>1. Discipline</th><th>2. Division</th><th>3. Weight / Team</th><th></th></tr></thead><tbody id="eventRows"></tbody></table></div>
<div class="actions"><button type="button" id="addLine" class="btn" onclick="addEventRow()">＋ Add Divition</button><button type="button" id="saveBtn" class="btn green" onclick="saveRegistration()">Save Registration</button></div><div class="status" id="resultStatus">Chọn đơn vị và điền thông tin trước khi lưu.</div></section>
</div>
<aside class="panel registration-roster" aria-label="Registered List">
  <div class="roster-title">REGISTERED LIST <button id="rosterRefresh" class="roster-refresh" type="button" onclick="loadRoster()" title="Refresh List">↻</button></div>
  <div id="rosterUnit" class="roster-unit">Chọn đơn vị để xem danh sách</div>
  <div id="rosterStats" class="roster-stats"></div>
  <div class="roster-search-wrap"><input id="rosterSearch" type="search" placeholder="Search Fullname / GAL / ID..." oninput="renderRoster()" aria-label="Search registered list"></div>
  <div class="roster-table-scroll" id="rosterScroll"><table class="roster-table" id="rosterTable"><tbody><tr><td>Chưa chọn đơn vị.</td></tr></tbody></table></div>
  <div class="roster-footer" id="rosterFooter"></div>
</aside></div>
</main>
<div class="backdrop hidden" id="noticeDialog" role="dialog" aria-modal="true" aria-labelledby="noticeTitle">
  <section class="dialog notice-dialog">
    <h2 id="noticeTitle">THÔNG BÁO ĐĂNG KÝ</h2>
    <p id="noticeMessage" class="notice-message"></p>
    <div class="actions" style="justify-content:flex-end">
      <button id="noticeCancel" class="btn" type="button" onclick="closeNotice(false)">Close</button>
      <button id="noticeOk" class="btn gold" type="button" onclick="closeNotice(true)">OK</button>
    </div>
  </section>
</div>
<div class="backdrop" id="unitDialog"><section class="dialog"><h2>Team Information</h2>
<div class="field"><label>Loại đơn vị *</label><select id="unitType" onchange="changeUnitType()"><option value="NOC">Country</option><option value="Province">Province</option><option value="Club" selected>Club</option></select></div>
<div class="field"><label id="unitNameLabel">Club *</label><input id="unitName" autocomplete="off" placeholder="Tìm hoặc nhập tên Club" oninput="searchUnits()"></div><div class="matches" id="unitMatches"></div>
<div class="field"><label>NOC *</label><input id="unitNoc" disabled placeholder="Tự lấy từ Country hoặc VIE"></div>
<div class="status" id="unitStatus">Chọn đơn vị có sẵn hoặc tạo Club mới.</div><div class="actions"><a class="back" id="cancelUnit" href="/">Back</a><button class="btn gold" type="button" onclick="confirmUnit()">Next</button></div></section></div>
<script>
const ROOT={{ event_prefix|tojson }};
const CSRF={{ csrf|tojson }};const YEAR={{ year|tojson }};
const TYPES={Kyorugi:['Individual','Team','Mixed Team'],Poomsae:['Recognized Individual','Recognized Pair','Recognized Team','Freestyle Individual','Freestyle Pair','Freestyle Team','Freestyle Mixed Team','Breaking Individual','Demonstration']};
let UNITS=[], CURRENT_UNIT=null, PICKED_UNIT=null, WEIGHT_CONFIG={categories:{},senior:{}};
let EXISTING_PROFILE=null, LOOKUP_SERIAL=0, EDIT_READY=false, LOOKUP_TIMER=null;
let ROSTER_ROWS=[], ROSTER_REQUEST=0;
function rosterPill(label,count,main=false){return `<span class="roster-pill ${main?'main':''}">${esc(label)} <strong>${count}</strong></span>`}
async function loadRoster(){
 const serial=++ROSTER_REQUEST, unit=CURRENT_UNIT;
 const table=$('rosterTable');
 if(!unit){ROSTER_ROWS=[];$('rosterUnit').textContent='Chọn đơn vị để xem danh sách';$('rosterStats').innerHTML='';table.innerHTML='<tbody><tr><td>Chưa chọn đơn vị.</td></tr></tbody>';$('rosterFooter').textContent='';return;}
 $('rosterUnit').textContent=unit.name+' · '+unit.noc;
 $('rosterFooter').textContent='Đang tải danh sách...';
 try{
   const data=await api('/api/registration/roster?unit_id='+encodeURIComponent(unit.id)+'&unit_type='+encodeURIComponent(unit.type));
   if(serial!==ROSTER_REQUEST)return;
   ROSTER_ROWS=Array.isArray(data.rows)?data.rows:[];
   renderRoster();
 }catch(err){if(serial!==ROSTER_REQUEST)return;ROSTER_ROWS=[];$('rosterStats').replaceChildren();table.innerHTML='<tbody><tr><td>Không tải được danh sách.</td></tr></tbody>';$('rosterFooter').textContent=err.message;}
}
function renderRoster(){
 const rows=ROSTER_ROWS;
 const counts=new Map();let totalMale=0,totalFemale=0,athleteMale=0,athleteFemale=0;
 for(const r of rows){
   const p=String(r.position||'').trim()||'Other';
   const isAthlete=p.toLowerCase()==='athlete';
   counts.set(p,(counts.get(p)||0)+1);
   const gender=String(r.gender||'').trim();
   if(/^m(ale)?$/i.test(gender)){totalMale++;if(isAthlete)athleteMale++;}
   else if(/^f(emale)?$/i.test(gender)){totalFemale++;if(isAthlete)athleteFemale++;}
 }
 const athletes=[...counts.entries()].filter(([p])=>p.toLowerCase()==='athlete').reduce((n,[,count])=>n+count,0);
 const totalRow='<div class="roster-stat-row">'+rosterPill('Total:',rows.length,true)+rosterPill('Male',totalMale)+rosterPill('Female',totalFemale)+'</div>';
 const athleteRow=athletes?'<div class="roster-stat-row">'+rosterPill('Athlete:',athletes,true)+rosterPill('Male',athleteMale)+rosterPill('Female',athleteFemale)+'</div>':'';
 const other=[...counts.entries()].filter(([p,n])=>p.toLowerCase()!=='athlete'&&n>0).sort((a,b)=>a[0].localeCompare(b[0]));
 const otherRow=other.length?'<div class="roster-stat-row">'+other.map(([p,n])=>rosterPill(p,n)).join('')+'</div>':'';
 $('rosterStats').innerHTML=totalRow+athleteRow+otherRow;
 const text=clean($('rosterSearch').value);
 const filtered=rows.filter(r=>!text||clean([r.full_name,r.athlete_id,r.position,r.gender,...(r.events||[])].join(' ')).includes(text));
 const eventMax=Math.max(0,...filtered.map(r=>(r.events||[]).length));
 const headings=['STT','Fullname','Position',...Array.from({length:eventMax},(_,i)=>'Event '+(i+1))];
 $('rosterTable').innerHTML='<thead><tr>'+headings.map(h=>'<th>'+esc(h)+'</th>').join('')+'</tr></thead><tbody>'+
 (filtered.length?filtered.map((r,i)=>{
   const pos=r.position==='Athlete'?'Athlete ('+(/^f/i.test(r.gender)?'F':/^m/i.test(r.gender)?'M':'?')+')':r.position;
   const eventCells=Array.from({length:eventMax},(_,j)=>'<td title="'+esc((r.events||[])[j]||'')+'">'+esc((r.events||[])[j]||'')+'</td>').join('');
   return '<tr><td>'+(i+1)+'</td><td title="'+esc(r.athlete_id)+'">'+esc(r.full_name)+'</td><td>'+esc(pos)+'</td>'+eventCells+'</tr>';
 }).join(''):'<tr><td colspan="'+headings.length+'">'+(rows.length?'Không tìm thấy người phù hợp.':'Đơn vị chưa có người đăng ký.')+'</td></tr>')+'</tbody>';
 $('rosterFooter').textContent='Hiển thị '+filtered.length+' / '+rows.length+' người · Kéo ngang để xem các Event';
}

const $=id=>document.getElementById(id);
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function clean(s){return String(s||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').trim().replace(/\s+/g,' ').toLowerCase()}
function status(id,message,type=''){const el=$(id);el.textContent=message;el.className='status '+type}
async function api(path,options={}){const res=await fetch(ROOT+path,{cache:'no-store',...options,headers:{...(options.body?{'Content-Type':'application/json','X-Registration-CSRF':CSRF}:{}),...(options.headers||{})}});let data;try{data=await res.json()}catch{throw Error('Máy chủ không trả JSON')}if(!res.ok)throw Error(data.error||'HTTP '+res.status);return data}
async function loadUnits(){let response=await api('/api/registration/units');UNITS=response.units||[];changeUnitType()}
function changeUnitType(){PICKED_UNIT=null;$('unitName').value='';$('unitNoc').value='';const type=$('unitType').value;const label={NOC:'Country',Province:'Province',Club:'Club'}[type];$('unitNameLabel').textContent=label+' *';$('unitName').placeholder=type==='Club'?'Tìm hoặc nhập tên Club':'Tìm và chọn '+label;status('unitStatus',type==='Club'?'Có thể chọn Club cũ hoặc tạo Club mới.':'Chỉ chọn '+label+' đã có trong danh mục.');searchUnits()}
function openUnitDialog(){$('unitDialog').classList.remove('hidden');$('cancelUnit').style.display=CURRENT_UNIT?'none':'';if(CURRENT_UNIT){$('unitType').value=CURRENT_UNIT.type;changeUnitType();$('unitName').value=CURRENT_UNIT.name;searchUnits()}$('unitName').focus()}
function searchUnits(){PICKED_UNIT=null;const input=clean($('unitName').value),type=$('unitType').value;const matches=UNITS.filter(u=>u.type===type&&clean(u.name).includes(input)).slice(0,30);$('unitMatches').innerHTML=matches.length?matches.map(u=>`<button class="match" type="button" data-uid="${u.id}" data-utype="${esc(u.type)}">${esc(u.name)} <small>(${esc(u.noc)})</small></button>`).join(''):'<p class="hint" style="padding:10px">'+(type==='Club'?'Club mới — nhập tên và bấm OK để lưu (VIE).':'Không tìm thấy. Chỉ chọn danh mục đã có.')+'</p>';$('unitMatches').querySelectorAll('button').forEach(b=>b.onclick=()=>selectUnit(UNITS.find(u=>u.type===b.dataset.utype&&u.id===Number(b.dataset.uid))));if(type!=='NOC')$('unitNoc').value='VIE';else $('unitNoc').value=''}
function selectUnit(u){if(!u)return;PICKED_UNIT=u;$('unitName').value=u.name;$('unitNoc').value=u.noc;status('unitStatus','Đã chọn '+({NOC:'Country',Province:'Province',Club:'Club'}[u.type])+': '+u.label,'success')}
async function confirmUnit(){try{const name=$('unitName').value.trim(),type=$('unitType').value;if(!name)throw Error('Vui lòng chọn tên đơn vị.');let unit=PICKED_UNIT;if(unit&&(unit.type!==type||clean(unit.name)!==clean(name)))unit=null;if(!unit){const existing=UNITS.find(u=>u.type===type&&clean(u.name)===clean(name));if(existing)unit=existing;else if(type==='Club'){const response=await api('/api/registration/units',{method:'POST',body:JSON.stringify({type,name})});unit=response.unit;UNITS.push(unit)}else throw Error('Country/Province chỉ được chọn từ danh mục có sẵn.')}if(!CURRENT_UNIT||CURRENT_UNIT.id!==unit.id||CURRENT_UNIT.type!==unit.type){++LOOKUP_SERIAL;resetExisting();$('athleteId').value='';$('fullName').value='';$('dob').value='';$('gender').value='';$('position').value='';$('eventRows').innerHTML='';addEventRow();syncPositionLock()}CURRENT_UNIT=unit;$('selectedName').value=unit.name;$('selectedNoc').value=unit.noc;$('selectedStatus').textContent=({NOC:'Country',Province:'Province',Club:'Club'}[unit.type])+' đã chọn';$('unitDialog').classList.add('hidden');$('rosterSearch').value='';loadRoster();status('resultStatus','Sẵn sàng nhập thông tin đăng ký.')}catch(e){status('unitStatus',e.message,'error')}}
let NOTICE_RESOLVE=null;
function popupNotice(title,message,confirm=false){
  $('noticeTitle').textContent=title;
  $('noticeMessage').textContent=message;
  $('noticeCancel').classList.toggle('hidden',!confirm);
  $('noticeOk').textContent=confirm?'Xác nhận':'OK';
  $('noticeDialog').classList.remove('hidden');
  return new Promise(resolve=>{NOTICE_RESOLVE=resolve});
}
function closeNotice(yes){
  $('noticeDialog').classList.add('hidden');
  const done=NOTICE_RESOLVE;NOTICE_RESOLVE=null;if(done)done(yes);
}
function resetExisting(){EXISTING_PROFILE=null;EDIT_READY=false;$('saveBtn').textContent='Save Registration';$('fullName').readOnly=false;$('dob').disabled=false;$('gender').disabled=false;$('position').disabled=false}
function fillEvents(existing){
  const mappings={1:'Kyorugi Individual',2:'Kyorugi Team',3:'Kyorugi Mixed Team',4:'Recognized Individual',5:'Recognized Pair',6:'Recognized Team',7:'Freestyle Individual',8:'Freestyle Pair',9:'Freestyle Team',10:'Freestyle Mixed Team',11:'Breaking Individual',12:'Demonstration'};
  $('eventRows').innerHTML='';
  let count=0;
  for(let i=1;i<=12;i++){
    const value=String(existing['Event_'+i]||'').trim();if(!value)continue;
    const rule=mappings[i],disc=rule.startsWith('Kyorugi')?'Kyorugi':'Poomsae';
    if((i===2&&!['Team 3','Team 4','Team 5'].includes(value)) ||
       (i===3 && value.toLowerCase()!=='mixed team') ||
       (i>3 && value.toLowerCase()!==rule.toLowerCase())){
      throw Error('Event_'+i+' đang có nội dung khác chuẩn: '+value+'. Chưa thể tự chỉnh, cần quản trị kiểm tra để tránh mất dữ liệu.');
    }
    const division=disc==='Kyorugi'?rule.substring(8):rule;
    addEventRow();const tr=$('eventRows').lastElementChild;
    tr.querySelector('.discipline').value=disc;updateDivision(tr);
    tr.querySelector('.division').value=division;updateDetail(tr);
    if(i===1){const select=tr.querySelector('.detail');if(![...select.options].some(o=>o.value===value))select.add(new Option(value,value));select.value=value}
    if(i===2)tr.querySelector('.detail').value=value;
    count++;
  }
  if(!count)addEventRow();syncPositionLock();
}
async function lookupAthlete(quiet=false){
 const serial=++LOOKUP_SERIAL;resetExisting();
 const id=$('athleteId').value.trim();if(!id||!CURRENT_UNIT)return;
 try{
   const result=await api('/api/registration/lookup',{method:'POST',body:JSON.stringify({athlete_id:id,unit_id:CURRENT_UNIT.id,unit_type:CURRENT_UNIT.type})});
   if(serial!==LOOKUP_SERIAL||$('athleteId').value.trim()!==id)return;
   if(!result.found){status('resultStatus','GAL/ID chưa có hồ sơ. Có thể đăng ký mới.');return}
   const a=result.athlete;EXISTING_PROFILE=a;EDIT_READY=true;
   $('fullName').value=a.full_name;$('dob').value=a.dob;$('gender').value=a.gender;
   $('position').value=a.position;onIdentity();fillEvents(a.events);$('fullName').readOnly=true;$('dob').disabled=true;$('gender').disabled=true;$('position').disabled=true;
   $('saveBtn').textContent='Update Registration';
   status('resultStatus',`GAL/ID ${id} đã có. Nội dung hiện tại đã được tải để điều chỉnh.`,'success');
   if(!quiet)await popupNotice('GAL/ID ĐÃ ĐĂNG KÝ',`GAL/ID: ${id}\nHồ sơ đã tồn tại. Sam đã tải thông tin và nội dung cũ.\nKen có thể chỉnh nội dung rồi bấm Update Registration; không tạo thêm mã ID.`);
 }catch(e){if(serial===LOOKUP_SERIAL){status('resultStatus',e.message,'error');await popupNotice('KHÔNG THỂ TRA CỨU',e.message)}}
}
function getAge(){let dob=$('dob').value;if(!/^\d{4}-\d{2}-\d{2}$/.test(dob))return '';let age=YEAR-Number(dob.slice(0,4));return age<=9?'U9':age<=12?'U12':age<=14?'U14':age<=17?'U17':'O18'}
function onIdentity(){let age=getAge();$('ageLabel').textContent=age?`Current age group ${YEAR}: ${age}`:'Chọn ngày sinh để xác định nhóm tuổi';updateAllRows()}
let NEXT_ROW=0;
function addEventRow(){let id=++NEXT_ROW;const tr=document.createElement('tr');tr.className='event-line';tr.dataset.row=id;tr.innerHTML=`<td class="event-cell"><select class="discipline"><option value="">— Chọn —</option><option>Kyorugi</option><option>Poomsae</option></select></td><td class="event-cell"><select class="division" disabled><option value="">— Chọn —</option></select></td><td class="event-cell"><select class="detail" disabled><option value="">Không áp dụng</option></select></td><td><button type="button" class="btn red" title="Bỏ dòng">×</button></td>`; $('eventRows').appendChild(tr);tr.querySelector('.discipline').onchange=()=>updateDivision(tr);tr.querySelector('.division').onchange=()=>updateDetail(tr);tr.querySelector('.btn').onclick=()=>tr.remove();updateDivision(tr);syncPositionLock()}
function updateDivision(tr){const discipline=tr.querySelector('.discipline').value,division=tr.querySelector('.division');const previous=division.value;division.innerHTML='<option value="">— Chọn —</option>'+(TYPES[discipline]||[]).map(x=>`<option>${esc(x)}</option>`).join('');division.disabled=!discipline;if((TYPES[discipline]||[]).includes(previous))division.value=previous;updateDetail(tr)}
function allowedWeights(){const code=getAge(),gender=$('gender').value;if(!code||!gender)return[];let result=new Set();Object.entries(WEIGHT_CONFIG.categories||{}).forEach(([key,values])=>{let u=key.toUpperCase();if(u.includes(code)&&u.includes(gender.toUpperCase()))(values||[]).forEach(w=>result.add(String(w).replace(/\s+/g,'').replace(/kg$/i,'')+'kg'))});if(!result.size&&code==='O18')(WEIGHT_CONFIG.senior[gender]||[]).forEach(w=>result.add(w+'kg'));return[...result].sort((a,b)=>Number(a.replace(/[^\d.]/g,''))-Number(b.replace(/[^\d.]/g,'')))}
function updateDetail(tr){const discipline=tr.querySelector('.discipline').value,division=tr.querySelector('.division').value,detail=tr.querySelector('.detail');const before=detail.value;let choices=[];if(discipline==='Kyorugi'&&division==='Individual')choices=allowedWeights();else if(discipline==='Kyorugi'&&division==='Team')choices=['Team 3','Team 4','Team 5'];detail.innerHTML='<option value="">'+(choices.length?'— Chọn —':'Không áp dụng')+'</option>'+choices.map(x=>`<option>${esc(x)}</option>`).join('');detail.disabled=!choices.length;if(choices.includes(before))detail.value=before}
function syncPositionLock(){
 const canRegister=$('position').value==='Athlete';
 const panel=$('divisionPanel');
 panel.classList.toggle('inactive',!canRegister);
 $('addLine').disabled=!canRegister;
 document.querySelectorAll('.event-line').forEach(tr=>{
   const discipline=tr.querySelector('.discipline');
   const division=tr.querySelector('.division');
   const detail=tr.querySelector('.detail');
   const remove=tr.querySelector('.btn');
   discipline.disabled=!canRegister;
   remove.disabled=!canRegister;
   if(canRegister){updateDivision(tr)}
   else{division.disabled=true;detail.disabled=true}
 });
}
function updateAllRows(){document.querySelectorAll('.event-line').forEach(updateDetail);syncPositionLock()}
function eventsFromRows(){if($('position').value!=='Athlete')return [];const items=[];for(const tr of document.querySelectorAll('.event-line')){let discipline=tr.querySelector('.discipline').value,division=tr.querySelector('.division').value,detail=tr.querySelector('.detail');if(!discipline&&!division)continue;if(!discipline||!division)throw Error('Chưa chọn đủ cột Discipline và Division.');if(!detail.disabled&&!detail.value)throw Error('Chưa chọn Weight / Team.');items.push({type:discipline==='Kyorugi'?'Kyorugi '+division:division,detail:detail.disabled?'':detail.value})}return items}
async function saveRegistration(){
 const button=$('saveBtn');button.disabled=true;
 try{
  if(!CURRENT_UNIT)throw Error('Chưa chọn đơn vị.');
  const payload={unit_id:CURRENT_UNIT.id,unit_type:CURRENT_UNIT.type,athlete_id:$('athleteId').value.trim(),full_name:$('fullName').value.trim(),dob:$('dob').value,gender:$('gender').value,position:$('position').value,events:eventsFromRows()};
  if(!payload.position)throw Error('Chưa chọn Position.');
  if(EXISTING_PROFILE){
    if(!EDIT_READY||EXISTING_PROFILE.id!==payload.athlete_id)throw Error('Vui lòng tra cứu lại GAL/ID.');
    payload.expected_events=EXISTING_PROFILE.events;
    const ok=await popupNotice('XÁC NHẬN THAY ĐỔI',`GAL/ID: ${payload.athlete_id}\nBạn có muốn cập nhật các nội dung đã chọn và gỡ những nội dung không còn chọn không?\nHồ sơ ID, tên và đơn vị không bị thay đổi.`,true);
    if(!ok)return;
    payload.edit_confirmed=true;
  }
  const data=await api('/api/registration/save',{method:'POST',body:JSON.stringify(payload)});
  status('resultStatus',data.message,'success');
  await loadRoster();
  const confirmReturn=popupNotice('ĐĂNG KÝ THÀNH CÔNG',data.message,true);
  $('noticeCancel').textContent='Đăng ký tiếp';
  $('noticeOk').textContent='Về trang giải đấu';
  const goBack=await confirmReturn;
  if(goBack){window.location.assign(ROOT || '/');return;}
  await lookupAthlete(true);
 }catch(e){status('resultStatus',e.message,'error');await popupNotice('THÔNG BÁO ĐĂNG KÝ',e.message)}finally{button.disabled=false}
}
async function initialize(){const back=ROOT || '/',link=$('backHome');link.href=back;$('cancelUnit').href=back;$('dob').max=YEAR+'-12-31';$('athleteId').addEventListener('blur',()=>{clearTimeout(LOOKUP_TIMER);lookupAthlete()});$('athleteId').addEventListener('input',()=>{clearTimeout(LOOKUP_TIMER);const wasExisting=!!EXISTING_PROFILE;++LOOKUP_SERIAL;resetExisting();if(wasExisting){$('fullName').value='';$('dob').value='';$('gender').value='';$('position').value='';$('eventRows').innerHTML='';addEventRow()}if($('athleteId').value.trim().length>=4){LOOKUP_TIMER=setTimeout(()=>lookupAthlete(),700)}});$('dob').onchange=onIdentity;$('gender').onchange=updateAllRows;$('position').onchange=syncPositionLock;addEventRow();syncPositionLock();try{await loadUnits()}catch(e){status('unitStatus',e.message,'error')}try{WEIGHT_CONFIG=await api('/api/registration/weights')}catch(e){status('resultStatus',e.message,'error')}}
initialize();
</script></body></html>'''



def _registration_db():
    # Use server-side service credentials for write operations; never return the key.
    return kyeorugi_db

def _registration_year():
    meta = load_setup()
    start = str(meta.get('date_from') or meta.get('start_date') or '').strip()
    if start:
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d'):
            try:
                return datetime.strptime(start[:10], fmt).year
            except ValueError:
                pass
    return int(meta.get('competition_year') or meta.get('year') or datetime.now().year)

install_registration_routes(app, _registration_db, _registration_year, _weighting_configuration, load_setup)


# =================== MULTI-EVENT PUBLIC PORTAL ===================
# A public slug is an alias in setup.public_slug; the authoritative database
# event_code remains unchanged until all related tables are migrated.
EVENT_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,79}$')

def _all_public_events():
    if supabase is None:
        return []
    try:
        result = (supabase.table(SIGMA_EVENTS_TABLE).select(
            'id,event_code,event_name,setup,is_active,updated_at,registration_open_at,registration_close_at,documents_deadline_at')
            .order('updated_at', desc=True).limit(200).execute())
        return result.data or []
    except Exception as exc:
        app.logger.warning('Cannot load event index: %s', type(exc).__name__)
        return []

def _public_slug(row):
    setup = row.get('setup') if isinstance(row.get('setup'),dict) else {}
    raw = str(setup.get('public_slug') or row.get('event_code') or '').strip().lower()
    return raw if EVENT_SLUG_RE.fullmatch(raw) else str(row.get('event_code') or '')

def _find_public_event(slug):
    if not EVENT_SLUG_RE.fullmatch(slug or ''):
        return None
    return next((row for row in _all_public_events() if _public_slug(row) == slug), None)

def get_legacy_event_code():
    # This is solely the identity for the old *local* draw/cache.
    # Never use whichever event was last edited/is_active, as multiple
    # events can be active and that would leak the old JSON into new events.
    return os.environ.get('LEGACY_SIGMA_EVENT_CODE', 'TRE_TQ_2026').strip()

PORTAL_HOME_HTML = r"""<!doctype html>
<html lang="vi"><head>
<link rel="icon" type="image/png" sizes="any" href="/portal-logo?v=20261118" />
<link rel="apple-touch-icon" href="/portal-logo?v=20261118" /><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Taekwondo Competition Management System</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;flex-direction:column;background:#07111f;color:#f2f8ff;font-family:Arial,Helvetica,sans-serif}
/* Public event portal BODY background. The image layer is at 50% opacity;
   header, event cards and footer retain their own solid backgrounds. */
body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
  background-image:url("{{ url_for('portal_background') }}");
  background-size:cover;background-position:center center;background-repeat:no-repeat;
  opacity:.50}
body>header,body>main,body>footer{position:relative;z-index:1}
header{min-height:116px;background:linear-gradient(100deg,#03152d,#084b8a,#03152d);border-bottom:3px solid #ffd34d;padding:14px 22px;display:grid;grid-template-columns:150px minmax(0,1fr) 150px;align-items:center;gap:12px}
.logo-wrap{cursor:pointer;border:0;background:transparent;padding:0;text-align:left;width:124px;min-height:76px;display:flex;align-items:center;justify-content:flex-start}.logo{max-width:124px;max-height:78px;object-fit:contain;background:#fff;border-radius:5px;padding:3px}.logo-fallback{font-size:29px;font-weight:1000;font-style:italic;letter-spacing:-3px;color:white;text-shadow:2px 2px #f53c3c}
h1{margin:0;color:#ffae39;text-shadow:0 2px 0 #081e3c,0 4px 12px #0008;letter-spacing:.8px;font-size:clamp(20px,2.5vw,36px);text-align:center}.clock{border:5px solid #ffd34d;border-radius:18px;text-align:center;padding:9px 6px;font-weight:800;background:#162e4c;font-size:20px}
main{width:100%;max-width:1230px;margin:30px auto 0;padding:0 18px;flex:1}.page-title{color:#ffd34d;font-size:29px;font-weight:900;margin:0 0 19px}
.event{background:#0d2238;border:1px solid #345875;border-radius:15px;margin-bottom:16px;overflow:hidden;box-shadow:0 10px 24px #0002}
.event summary{list-style:none;cursor:pointer;padding:20px 22px;display:flex;align-items:center;justify-content:space-between;gap:16px;outline-offset:-3px}.event summary::-webkit-details-marker{display:none}.event summary:hover,.event[open] summary{background:#123150}
.event-summary-text{min-width:0;flex:1}.event h2{margin:0 0 12px;font-size:clamp(18px,2vw,25px);color:#ffcf60}.meta{color:#c8e0f4;line-height:1.75;font-size:15px}
.status{flex:none;align-self:flex-start;display:inline-flex;align-items:center;justify-content:center;min-width:168px;min-height:49px;text-align:center;border-radius:14px;padding:12px 18px;font-size:16px;font-weight:900;line-height:1.2;white-space:nowrap;font-family:Arial,Helvetica,sans-serif;letter-spacing:.1px;box-shadow:0 2px 9px #0003}
.status-upcoming{background:#fff2a4;color:#624300}.status-open{background:#c5f4cc;color:#08612e}.status-closed{background:#fbd0d3;color:#8e2028}.status-ongoing{background:#ffdbac;color:#8c420a}.status-finished{background:#d8dce1;color:#424b54}
.more{border-top:1px solid #315170;padding:20px 22px 23px;background:#0a1b2f}.items{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px;margin:0 0 22px}.field{padding:11px 15px;border:1px solid #244667;border-radius:9px;background:#0d2945}.field span{display:block;color:#9bb9d2;font-size:13px;margin-bottom:6px}.field strong{font-size:15px;overflow-wrap:anywhere}
.document-links{display:flex;flex-wrap:wrap;gap:10px}.document-links a{display:inline-block;background:#123c67;color:#d9ebff;border:1px solid #4f789f;border-radius:9px;padding:10px 13px;text-decoration:none;font-weight:800;overflow-wrap:anywhere}.document-links a:hover{border-color:#ffd34d;color:#ffd34d}
.open-link{display:inline-block;background:#ffd34d;color:#08203c;text-decoration:none;padding:13px 21px;border-radius:9px;font-weight:900;font-family:Arial,Helvetica,sans-serif;font-size:14px;letter-spacing:0;line-height:1.35}.open-link:hover{background:#ffe387}.empty{padding:25px;border:1px solid #345875;border-radius:10px;color:#b8d0e6}
footer{margin-top:10px;text-align:center;color:#b9d1e8;font-size:13px;padding:15px 15px 15px;background:linear-gradient(180deg,#081b30,#061323);border-top:1px solid #315879;line-height:1.0}
.footer-name{font-size:17px;font-weight:900;color:#ffd34d;letter-spacing:.7px}
.footer-quote{font-size:14px;color:#d9eaff;margin:6px auto;max-width:740px}
.footer-contact{margin-top:7px}.footer-contact a{color:#83d7ff;text-decoration:none;font-weight:700;overflow-wrap:anywhere}.footer-contact a:hover{text-decoration:underline}
.footer-bottom{margin-top:9px;color:#829fb9;font-size:11px}

.admin-overlay{position:fixed;inset:0;background:#000a;z-index:1500;display:none;align-items:center;justify-content:center;padding:16px}
.admin-overlay.open{display:flex}.admin-modal{width:min(440px,100%);background:#102a45;border:1px solid #49769b;border-radius:18px;padding:25px;box-shadow:0 20px 65px #0009}
.admin-modal h2{margin:0 0 20px;color:#ffd34d;font-size:24px;text-align:center}.admin-modal label{display:block;font-weight:700;margin:12px 0 7px;color:#cfe5ff}
.admin-modal input{width:100%;background:#fff;color:#132840;border:1px solid #79a3ce;border-radius:9px;padding:12px;font:inherit}
.admin-modal .buttons{display:flex;justify-content:flex-end;gap:10px;margin-top:22px}.admin-modal button{border:0;border-radius:9px;padding:11px 17px;cursor:pointer;font-weight:800}
.admin-modal button[type=submit]{background:#ffd34d;color:#06192b}.admin-modal button[type=button]{background:#264965;color:white}.admin-error{color:#ffbcc2;min-height:21px;margin-top:10px;font-size:13px}

@media(max-width:700px){header{grid-template-columns:70px minmax(0,1fr) 102px;min-height:90px;padding:9px 8px;gap:4px}.logo-wrap{width:62px;min-height:44px}.logo{max-width:62px;max-height:50px}.logo-fallback{font-size:20px}.clock{min-width:0;font-size:11px;padding:7px 3px}h1{font-size:clamp(15px,4vw,23px)}.items{grid-template-columns:1fr}main{margin:18px auto}.event summary{padding:16px}.more{padding:16px}.status{min-width:113px;min-height:38px;font-size:12px;padding:9px 10px}.event h2{font-size:17px}}
</style></head><body>
<header><button class="logo-wrap" type="button" id="adminLogo" aria-label="Admin Login"><img class="logo" src="{{ url_for('portal_logo') }}" alt="NTP" onerror="this.style.display='none';this.nextElementSibling.style.display='block'"><span class="logo-fallback" style="display:none">NTP</span></button><h1>TAEKWONDO COMPETITION MANAGEMENT SYSTEM</h1><div class="clock"><div id="date">--/--/----</div><div id="time">--:--:--</div></div></header>
<main><h2 class="page-title">SELECT EVENT</h2>
{% if events %}{% for e in events %}
<details class="event"><summary><div class="event-summary-text"><h2>{{e.name}}</h2><div class="meta">📅 {{e.dates}}{% if e.location %}<br>📍 {{e.location}}{% endif %}</div></div><span class="status status-{{e.status_key}}">{{e.status}}</span></summary>
<section class="more"><div class="items">
<div class="field"><span>Event Code</span><strong>{{e.code}}</strong></div>
<div class="field"><span>Registration Opens</span><strong>{{e.registration_open}}</strong></div>
<div class="field"><span>Registration Closes</span><strong>{{e.registration_close}}</strong></div>
<div class="field"><span>Document Submission Deadline</span><strong>{{e.documents}}</strong></div>
{% if e.description %}<div class="field" style="grid-column:1/-1"><span>Event Information</span><strong>{{e.description}}</strong></div>{% endif %}
{% if e.links %}<div class="field" style="grid-column:1/-1"><span>Documents &amp; Links</span><div class="document-links">{% for doc in e.links %}<a href="{{doc.url}}" target="_blank" rel="noopener noreferrer">📄 {{doc.title}}</a>{% endfor %}</div></div>{% endif %}
</div><a class="open-link" href="/{{e.slug}}">VIEW EVENT</a></section></details>
{% endfor %}{% else %}<div class="empty">Events are unavailable. Check the Supabase connection and SELECT permissions.</div>{% endif %}
</main>
<footer role="contentinfo">
  <div class="footer-name">Taekwondo Event &amp; Competition Management Platform</div>
  <div class="footer-quote">Connecting Passion · Honoring Sportsmanship · Shaping Taekwondo’s Future</div>
  <div>Created and Designed by <strong style="color:#fff">Nguyễn Thiên Phụng</strong></div>
  <div class="footer-contact">Contact: <a href="mailto:nhoctotokute93@gmail.com">nhoctotokute93@gmail.com</a></div>
</footer>
<div class="admin-overlay" id="adminOverlay"><form class="admin-modal" id="adminLoginForm" autocomplete="on"><h2>ADMIN LOGIN</h2><label for="adminUsername">ID</label><input id="adminUsername" name="username" autocomplete="username" required maxlength="90"><label for="adminPassword">Password</label><input id="adminPassword" type="password" autocomplete="current-password" required><div class="admin-error" id="adminError" role="alert"></div><div class="buttons"><button type="button" id="adminCancel">Cancel</button><button type="submit" id="adminSubmit">Sign In</button></div></form></div>
<script>
function tick(){const d=new Date();document.getElementById('date').textContent=new Intl.DateTimeFormat('vi-VN',{day:'2-digit',month:'2-digit',year:'numeric',timeZone:'Asia/Ho_Chi_Minh'}).format(d);document.getElementById('time').textContent=new Intl.DateTimeFormat('vi-VN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,timeZone:'Asia/Ho_Chi_Minh'}).format(d)}
tick();setInterval(tick,1000);
const overlay=document.getElementById('adminOverlay');
document.getElementById('adminLogo').addEventListener('click',()=>{overlay.classList.add('open');document.getElementById('adminUsername').focus()});
document.getElementById('adminCancel').addEventListener('click',()=>overlay.classList.remove('open'));
overlay.addEventListener('click',e=>{if(e.target===overlay)overlay.classList.remove('open')});
document.addEventListener('keydown',e=>{if(e.key==='Escape')overlay.classList.remove('open')});
document.getElementById('adminLoginForm').addEventListener('submit',async e=>{e.preventDefault();const submit=document.getElementById('adminSubmit');submit.disabled=true;const err=document.getElementById('adminError');err.textContent='';try{const res=await fetch('/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({username:document.getElementById('adminUsername').value,password:document.getElementById('adminPassword').value})});const data=await res.json();if(!res.ok)throw Error(data.error||'Sign In không thành công');location.assign('/admin')}catch(ex){err.textContent=ex.message;document.getElementById('adminPassword').value=''}finally{submit.disabled=false}});

document.querySelectorAll('details.event').forEach(item=>item.addEventListener('toggle',()=>{if(!item.open)return;document.querySelectorAll('details.event').forEach(other=>{if(other!==item)other.open=false})}));
</script></body></html>"""

EVENT_NOT_READY_HTML = r"""<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{name}}</title>
<style>body{font-family:Arial,Helvetica,sans-serif;background:#07111f;color:white;margin:0}.header{background:#092b4e;border-bottom:3px solid #ffd34d;padding:24px;text-align:center}h1{color:#ffd34d}.content{max-width:760px;margin:50px auto;border:1px solid #315574;border-radius:16px;background:#0d233b;padding:30px;line-height:1.8}a{color:#ffd34d}</style><div class="header"><h1>{{name}}</h1></div><div class="content"><h2>ĐANG CHUẨN BỊ DỮ LIỆU GIẢI ĐẤU</h2><p>Giải đã có trong danh mục nhưng các bảng đăng ký, lịch thi đấu và kết quả chưa được phân tách theo Event Code. Để tránh trộn dữ liệu, trang chi tiết chỉ mở sau khi hoàn tất đồng bộ.</p><p><a href="/">← Về trang chủ</a></p></div></html>"""

@app.get('/portal')
def portal_alias():
    return redirect('/', code=302)

PORTAL_TZ = ZoneInfo('Asia/Ho_Chi_Minh')

@app.get('/portal-background')
def portal_background():
    """Serve the shared portal background; accept either icons/ or icon/."""
    root = str(_PROJECT_ROOT)
    for folder in ('icons', 'icon'):
        directory = os.path.join(root, folder)
        if os.path.isfile(os.path.join(directory, 'TKD_web.png')):
            return send_from_directory(directory, 'TKD_web.png')
    abort(404)

@app.get('/portal-logo')
def portal_logo():
    # The original Windows deployment stores its logo in icon/NTP.png.
    # Allow alternate capitalization and conventional icons folder.
    root = str(_PROJECT_ROOT)
    for folder, name in (
        ('icon', 'NTP.png'), ('icons', 'NTP.png'),
        ('icon', 'ntp.png'), ('icons', 'ntp.png'),
        ('icons', 'main.png'), ('icon', 'main.png'),
        ('icons', 'logo.png'), ('icon', 'logo.png'),
        ('icons', 'Logo.png'), ('icon', 'Logo.png'),
        ('icon', 'NTP.jpg'), ('icons', 'NTP.jpg'),
    ):
        if os.path.isfile(os.path.join(root, folder, name)):
            return send_from_directory(os.path.join(root, folder), name)
    abort(404)


def _portal_datetime(value, *, end_of_day=False):
    if isinstance(value, datetime):
        result = value
    else:
        raw = str(value or '').strip()
        if not raw:
            return None
        try:
            result = datetime.fromisoformat(raw.replace('Z','+00:00'))
        except ValueError:
            result = None
        if result is None:
            for fmt in ('%d/%m/%Y %H:%M', '%d/%m/%Y %H:%M:%S',
                        '%Y-%m-%d %H:%M', '%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d'):
                try:
                    result = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
        if result is None:
            return None
        if end_of_day and len(raw) <= 10:
            result = result.replace(hour=23, minute=59, second=59)
    return result.replace(tzinfo=PORTAL_TZ) if result.tzinfo is None else result.astimezone(PORTAL_TZ)


def _portal_dt_display(value):
    dt = _portal_datetime(value)
    return dt.strftime('%d/%m/%Y %H:%M') if dt else 'Chưa thiết lập'


def _portal_status(now, start, end, registration_open, registration_close):
    # Date of tournament always has precedence over registration status.
    if end and now > end:
        return 'finished', 'Finished'
    if start and end and start <= now <= end:
        return 'ongoing', 'In Progress'
    if registration_open and registration_close and registration_open <= now <= registration_close:
        return 'open', 'Registration Open'
    if registration_close and now > registration_close:
        return 'closed', 'Registration Closed'
    return 'upcoming', 'Upcoming'


def _safe_public_links(raw):
    # Only validated HTTP(S) links can reach public templates, including legacy JSON.
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw[:30]:
        if not isinstance(item, dict):
            continue
        try:
            result.extend(_admin_event_links([item]))
        except ValueError:
            continue
    return result


def _homepage():
    now = datetime.now(PORTAL_TZ)
    events = []
    legacy_code = get_legacy_event_code()
    for row in _all_public_events():
        s = row.get('setup') if isinstance(row.get('setup'), dict) else {}
        start_raw = s.get('date_from') or s.get('start_date') or ''
        end_raw = s.get('date_to') or s.get('end_date') or ''
        if not start_raw and row.get('event_code') == legacy_code:
            legacy = _load_legacy_event_optional()
            start_raw = legacy.get('date_from','')
            end_raw = legacy.get('date_to','')
            s = {**legacy, **s}
        start = _portal_datetime(start_raw)
        end = _portal_datetime(end_raw, end_of_day=True)
        reg_open_raw = row.get('registration_open_at') or s.get('registration_open_at') or s.get('registration_open')
        reg_close_raw = row.get('registration_close_at') or s.get('registration_close_at') or s.get('registration_close')
        doc_raw = row.get('documents_deadline_at') or s.get('documents_deadline_at') or s.get('documents_deadline')
        reg_open = _portal_datetime(reg_open_raw)
        reg_close = _portal_datetime(reg_close_raw)
        key,label = _portal_status(now, start, end, reg_open, reg_close)
        # Completed events stay among the dated events until three complete days after the tournament.
        old_finished = end is not None and now > end + timedelta(days=3)
        events.append({'name':row.get('event_name') or 'Giải chưa đặt tên',
            'slug':_public_slug(row),'code':row.get('event_code'),
            'dates':format_event_date_range(start_raw,end_raw) or 'Chưa thiết lập',
            'location':s.get('location') or s.get('venue') or '',
            'registration_open':_portal_dt_display(reg_open_raw),
            'registration_close':_portal_dt_display(reg_close_raw),
            'documents':_portal_dt_display(doc_raw),
            'description':s.get('description') or '',
            'links':_safe_public_links(s.get('links')),
            'status_key':key,'status':label,
            '_sort':(1 if old_finished else 0, start or datetime.max.replace(tzinfo=PORTAL_TZ), str(row.get('event_name') or ''))})
    events.sort(key=lambda e:e['_sort'])
    return render_template_string(PORTAL_HOME_HTML,events=events)

# Replace the historical root detail route with the event-directory homepage.
# Original `index` stays as a normal function and is called only with an
# explicit, verified Event context.
_original_index = index
app.view_functions['index'] = _homepage

@app.get('/<event_slug>')
def public_event_page(event_slug):
    row = _find_public_event(event_slug)
    if not row:
        abort(404)
    g.event_code = row['event_code']
    g.event_prefix = '/' + event_slug
    return _original_index()

# Canonicalize event detail links with a trailing slash; retain current event.
@app.get('/<event_slug>/')
def public_event_trailing_slash(event_slug):
    if not _find_public_event(event_slug):
        abort(404)
    return redirect('/' + event_slug, code=308)

# Scope legacy APIs under the event URL. For any other event we reject calls,
# because old Result/Information Lists/Kyeorugi Matchs still lack event_code.
# Unprefixed API endpoints are blocked for public web visitors.
_old_api_rules = [(str(rule.rule), rule.endpoint, tuple(rule.methods or ()))
                  for rule in app.url_map.iter_rules()
                  if str(rule.rule).startswith('/api/')]
for _index, (_old_rule, _endpoint, _methods) in enumerate(_old_api_rules):
    _view = app.view_functions[_endpoint]
    def _scope_view(event_slug, _original=_view, **kwargs):
        row = _find_public_event(event_slug)
        if not row:
            return jsonify({'error':'Event Code không tồn tại'}), 404
        g.event_code = row['event_code']
        g.event_prefix = '/' + event_slug
        return _original(**kwargs)
    app.add_url_rule('/<event_slug>' + _old_rule, 'scoped_api_' + str(_index),
                     _scope_view, methods=[m for m in _methods if m not in ('HEAD','OPTIONS')])

# Registration page is scoped too, but writes will require event_code migration
# before any new-event registration becomes possible.
_orig_registration = app.view_functions.get('registration_form_page')
if _orig_registration:
    @app.get('/<event_slug>/registration')
    def scoped_registration(event_slug):
        row = _find_public_event(event_slug)
        if not row:
            abort(404)
        g.event_code = row['event_code']
        g.event_prefix = '/' + event_slug
        return _orig_registration()

@app.before_request
def _prevent_unscoped_event_reads():
    # No global endpoints that could accidentally mix records across events.
    if request.path.startswith('/api/'):
        return jsonify({'error':'Cần truy cập API qua đường dẫn /{event_code}/api/...'}), 403
    if request.path == '/registration':
        return redirect('/', code=302)



# =================== MULTI-EVENT ADMIN CONSOLE ===================
# Credentials are configured in .env, not placed in public HTML or JS.
import hashlib
import hmac
import threading
import time as time_module

_ADMIN_FAILURES = {}
_ADMIN_FAILURES_LOCK = threading.Lock()
_ADMIN_ALLOWED = set('abcdefghijklmnopqrstuvwxyz0123456789_-')
_ADMIN_TTL = 60 * 60 * 8


def _admin_authenticated():
    return bool(session.get('event_admin') and
                time_module.time() - float(session.get('event_admin_at') or 0) < _ADMIN_TTL)


def _admin_guard():
    if not _admin_authenticated():
        return jsonify({'error': 'Phiên quản trị đã hết hạn. Hãy đăng nhập lại.'}), 401
    if request.headers.get('X-Admin-CSRF', '') != session.get('event_admin_csrf'):
        return jsonify({'error': 'Yêu cầu không hợp lệ (CSRF).'}), 403
    return None


def _admin_writer():
    # Never use an anon/public client for administrative writes.
    if kyeorugi_db is None or not _kyeorugi_key or not any(
        os.environ.get(k, '').strip() for k in ('KYEORUGI_SUPABASE_SERVICE_ROLE_KEY', 'SUPABASE_SECRET_KEY')
    ):
        raise RuntimeError('Chưa cấu hình Supabase secret/service-role key ở server cho Admin.')
    return kyeorugi_db


def _admin_password_valid(username, password):
    expected_user = os.environ.get('EVENT_ADMIN_USERNAME', '').strip()
    expected_password = os.environ.get('EVENT_ADMIN_PASSWORD', '')
    stored_hash = os.environ.get('EVENT_ADMIN_PASSWORD_HASH', '').strip()
    if not expected_user or (not stored_hash and not expected_password):
        return False
    user_ok = hmac.compare_digest(username.encode(), expected_user.encode())
    if stored_hash.startswith('pbkdf2_sha256$'):
        try:
            _, iterations, salt, digest = stored_hash.split('$', 3)
            computed = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(iterations))
            password_ok = hmac.compare_digest(computed.hex(), digest)
        except (ValueError, TypeError):
            password_ok = False
    else:
        password_ok = bool(expected_password) and hmac.compare_digest(password.encode(), expected_password.encode())
    return user_ok and password_ok


@app.post('/admin/login')
def event_admin_login():
    # Basic bounded retry limiter per IP. For production behind Nginx also use
    # rate limiting at the proxy, since process-local counters aren't shared.
    ip = request.remote_addr or 'unknown'
    now = time_module.monotonic()
    with _ADMIN_FAILURES_LOCK:
        times = [t for t in _ADMIN_FAILURES.get(ip, []) if now - t < 900]
        _ADMIN_FAILURES[ip] = times
        if len(times) >= 6:
            return jsonify({'error': 'Quá nhiều lần đăng nhập sai. Thử lại sau 15 phút.'}), 429
    body = request.get_json(silent=True) or {}
    username = str(body.get('username') or '')[:90]
    password = str(body.get('password') or '')[:256]
    if not _admin_password_valid(username, password):
        with _ADMIN_FAILURES_LOCK:
            _ADMIN_FAILURES.setdefault(ip, []).append(now)
        return jsonify({'error': 'ID hoặc mật khẩu không chính xác.'}), 401
    with _ADMIN_FAILURES_LOCK:
        _ADMIN_FAILURES.pop(ip, None)
    session.clear()
    session['event_admin'] = True
    session['event_admin_at'] = time_module.time()
    session['event_admin_csrf'] = token_urlsafe(32)
    return jsonify({'ok': True})


@app.get('/admin')
def event_admin_page():
    if not _admin_authenticated():
        return redirect('/', code=302)
    response = app.make_response(render_template_string(ADMIN_EVENTS_HTML,
            csrf=session['event_admin_csrf']))
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/admin/events')
def event_admin_events():
    if not _admin_authenticated():
        return jsonify({'error':'Yêu cầu đăng nhập quản trị.'}),401
    try:
        rows = (_admin_writer().table('sigma_events').select('*')
                .order('updated_at',desc=True).limit(500).execute().data or [])
        return jsonify({'events':rows})
    except Exception as exc:
        app.logger.error('Admin events list error: %s', type(exc).__name__)
        return jsonify({'error':'Không đọc được sigma_events. Kiểm tra Terminal và quyền Supabase.'}),503


def _admin_datetime(raw):
    if not raw:
        return None
    if not isinstance(raw, str) or len(raw) > 50:
        raise ValueError('Thời gian không hợp lệ.')
    dt = None
    for fmt in ('%d/%m/%Y %H:%M', '%d/%m/%Y %H:%M:%S'):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            break
        except ValueError:
            pass
    if dt is None:
        try:
            dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('Ngày giờ không hợp lệ. Nhập DD/MM/YYYY HH:mm (24 giờ).')
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=PORTAL_TZ)
    return dt.astimezone(PORTAL_TZ).isoformat()


def _admin_event_links(raw):
    """Validate external document links; no upload and no script/data URL."""
    from urllib.parse import urlsplit
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 30:
        raise ValueError('Tối đa 30 liên kết cho mỗi giải.')
    cleaned = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError('Định dạng liên kết không hợp lệ.')
        title = str(item.get('title') or '').strip()
        url = str(item.get('url') or '').strip()
        if not title and not url:
            continue
        if not title or not url or len(title) > 160 or len(url) > 1800:
            raise ValueError('Mỗi tài liệu cần tên (tối đa 160 ký tự) và URL hợp lệ.')
        parsed = urlsplit(url)
        if (parsed.scheme not in ('https', 'http') or not parsed.netloc or
                parsed.username or parsed.password or any(c.isspace() for c in url)):
            raise ValueError('Liên kết phải bắt đầu bằng https:// hoặc http:// và không chứa tài khoản trong URL.')
        cleaned.append({'title':title, 'url':url})
    return cleaned


def _admin_event_payload(body, existing=None):
    name = str(body.get('event_name') or '').strip()
    # For UPDATE, the stored Event Code is authoritative. Never normalize it
    # or compare its casing to a disabled field sent from the browser.
    # Historical events may legitimately use uppercase identifiers.
    code = (str(existing['event_code']) if existing else
            str(body.get('event_code') or '').strip().lower())
    slug = str(body.get('public_slug') or code).strip().lower()
    if not 5 <= len(name) <= 250:
        raise ValueError('Tên giải phải có từ 5 đến 250 ký tự.')
    if not EVENT_SLUG_RE.fullmatch(slug) or (not existing and not EVENT_SLUG_RE.fullmatch(code)):
        raise ValueError('Event Code / URL chỉ gồm chữ thường, số, _ và -, tối đa 80 ký tự.')
    start = str(body.get('date_from') or '').strip()
    end = str(body.get('date_to') or '').strip()
    d_start = parse_event_date(start) if start else None
    d_end = parse_event_date(end) if end else None
    if (start and not d_start) or (end and not d_end):
        raise ValueError('Ngày thi đấu phải theo dạng dd/mm/yyyy hoặc yyyy-mm-dd.')
    if d_start and d_end and d_end < d_start:
        raise ValueError('Ngày kết thúc không được trước ngày bắt đầu.')
    reg_open = _admin_datetime(body.get('registration_open_at'))
    reg_close = _admin_datetime(body.get('registration_close_at'))
    documents = _admin_datetime(body.get('documents_deadline_at'))
    if reg_open and reg_close and datetime.fromisoformat(reg_close) <= datetime.fromisoformat(reg_open):
        raise ValueError('Hạn đăng ký phải sau thời điểm mở đăng ký.')
    if len(str(body.get('location') or '')) > 500 or len(str(body.get('description') or '')) > 3000:
        raise ValueError('Địa điểm hoặc mô tả quá dài.')
    setup = dict(existing.get('setup') or {}) if existing else {}
    setup.update({'event':name,'date_from':d_start.strftime('%d/%m/%Y') if d_start else '',
                  'date_to':d_end.strftime('%d/%m/%Y') if d_end else '',
                  'location':str(body.get('location') or '').strip(),
                  'description':str(body.get('description') or '').strip(),
                  'links':_admin_event_links(body.get('links')),
                  'public_slug':slug})
    return {'event_code':code, 'event_name':name, 'setup':setup,
            'registration_open_at':reg_open, 'registration_close_at':reg_close,
            'documents_deadline_at':documents}


@app.post('/admin/events')
def event_admin_create():
    guarded = _admin_guard()
    if guarded: return guarded
    try:
        body = request.get_json(silent=True) or {}
        payload = _admin_event_payload(body)
        # Avoid collision between aliases and existing authoritative event codes.
        existing = (_admin_writer().table('sigma_events').select('event_code,setup').execute().data or [])
        for row in existing:
            if payload['event_code'] == row['event_code'] or payload['setup']['public_slug'] == _public_slug(row):
                return jsonify({'error':'Event Code hoặc URL đã tồn tại.'}),409
        payload['is_active'] = False
        rows = (_admin_writer().table('sigma_events').insert(payload).execute().data or [])
        return jsonify({'ok':True,'event':rows[0] if rows else None}),201
    except ValueError as exc:
        return jsonify({'error':str(exc)}),400
    except Exception as exc:
        app.logger.error('Admin event create error: %s', type(exc).__name__)
        return jsonify({'error':'Không thể tạo giải. Kiểm tra cấu trúc bảng, quyền ghi và Terminal.'}),503


@app.put('/admin/events/<int:event_id>')
def event_admin_update(event_id):
    guarded = _admin_guard()
    if guarded: return guarded
    try:
        rows = (_admin_writer().table('sigma_events').select('*').eq('id',event_id).limit(1).execute().data or [])
        if not rows:
            return jsonify({'error':'Không tìm thấy sự kiện.'}),404
        old = rows[0]
        payload = _admin_event_payload(request.get_json(silent=True) or {}, old)
        all_rows = (_admin_writer().table('sigma_events').select('id,event_code,setup').execute().data or [])
        slug = payload['setup']['public_slug']
        if any(int(r['id']) != event_id and (slug == _public_slug(r) or slug == r['event_code']) for r in all_rows):
            return jsonify({'error':'URL đã được sử dụng bởi giải khác.'}),409
        payload.pop('event_code',None)
        changed = (_admin_writer().table('sigma_events').update(payload).eq('id',event_id).execute().data or [])
        return jsonify({'ok':True,'event':changed[0] if changed else None})
    except ValueError as exc:
        return jsonify({'error':str(exc)}),400
    except Exception as exc:
        app.logger.exception('Admin event update failed for event id=%s', event_id)
        return jsonify({'error':'Supabase từ chối cập nhật. Kiểm tra Terminal để xem lỗi quyền ghi hoặc dữ liệu.'}),503


@app.post('/admin/logout')
def event_admin_logout():
    guarded = _admin_guard()
    if guarded: return guarded
    session.clear()
    return jsonify({'ok':True})


ADMIN_EVENTS_HTML = r"""<!doctype html><html lang="vi"><head>
<link rel="icon" type="image/png" sizes="any" href="/portal-logo?v=20261118" />
<link rel="apple-touch-icon" href="/portal-logo?v=20261118" /><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Event Admin | Phung TKD System</title><style>
*{box-sizing:border-box}body{margin:0;background:#07111f;color:#f1f7ff;font-family:Arial,Helvetica,sans-serif}
header{min-height:116px;background:linear-gradient(100deg,#03152d,#084b8a,#03152d);border-bottom:3px solid #ffd34d;padding:12px 22px;display:grid;grid-template-columns:140px 1fr 140px;align-items:center;gap:12px}
.logo{max-width:106px;max-height:80px;background:#fff;padding:4px;border-radius:5px}.system-title{text-align:center;font-size:clamp(19px,2.5vw,32px);font-weight:900;color:#ffaa32;text-shadow:0 3px 3px #0008}.clock{padding:10px;border:2px solid #ffd34d;border-radius:12px;text-align:center;font-weight:800;background:#1a3353;font-size:15px}
main{padding:22px;max-width:1650px;margin:auto}.title-bar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:16px}.title-bar h1{color:#ffd34d;margin:0;font-size:28px}
.split{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px}.panel{background:#0d243c;border:1px solid #315878;border-radius:15px;padding:20px;min-width:0}.panel h2{font-size:21px;color:#ffd34d;margin:0 0 17px}.fields{display:grid;grid-template-columns:1fr 1fr;gap:13px}.field{display:flex;flex-direction:column;gap:6px}.field.wide{grid-column:1/-1}.field label{color:#c4dff7;font-size:13px;font-weight:700}input,textarea{width:100%;background:#fff;border:1px solid #9bb6d2;border-radius:9px;padding:11px;color:#14283b;font:inherit}input:disabled{background:#b6c5d3;color:#47586c}textarea{min-height:92px;resize:vertical}button{padding:11px 18px;border:0;border-radius:9px;font-weight:800;cursor:pointer;font-family:inherit}.primary{background:#ffd34d;color:#07111f}.blue{background:#195d9e;color:white}.muted{background:#2a445f;color:white}.buttons{display:flex;flex-wrap:wrap;gap:9px;margin-top:17px}.hint{font-size:12px;line-height:1.5;color:#a5c4df;margin-top:13px}.status{min-height:24px;margin-top:13px;font-size:14px;color:#a5f2bc}.status.error{color:#ffc0c0}
.event-list{max-height:calc(100vh - 295px);overflow-y:auto;display:flex;flex-direction:column;gap:10px}.event-item{width:100%;text-align:left;background:#113151;border:1px solid #365c7b;color:#eff8ff;border-radius:10px;padding:15px}.event-item:hover,.event-item.active{border-color:#ffd34d;background:#194268}.event-item strong{display:block;color:#ffd34d;font-size:16px;line-height:1.35}.event-item small{display:block;color:#c0d9eb;margin-top:5px;line-height:1.5}.event-row{display:flex;justify-content:space-between;gap:10px}.tag{font-size:11px;color:#fff;background:#38618a;padding:4px 8px;border-radius:20px;white-space:nowrap;height:fit-content}
.date-format-help{font-size:11px;color:#91b7d4}.document-editor{border:1px solid #325a78;background:#082037;border-radius:10px;padding:12px}.document-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.6fr) auto;gap:8px;margin:8px 0;align-items:center}.document-row input{min-width:0}.document-row button{background:#8d2940;color:#fff}.document-help{font-size:12px;color:#a8c8e6;line-height:1.5;margin:0 0 8px}@media(max-width:640px){.document-row{grid-template-columns:1fr}.document-row button{justify-self:end}}
@media(max-width:900px){.split{grid-template-columns:1fr}.event-list{max-height:none}header{grid-template-columns:60px 1fr 95px;gap:6px;padding:10px}.logo{max-width:58px}.clock{font-size:10px;padding:6px}.fields{grid-template-columns:1fr}.field.wide{grid-column:1}.panel{padding:14px}main{padding:12px}}

/* Admin date validation and visual hints */
.field input.date-invalid{border:2px solid #ff6b75!important;background:#fff0f0!important;box-shadow:0 0 0 3px #ff73732c;color:#8d1024}
.field input.date-adjusted{border:2px solid #f4c74b!important;box-shadow:0 0 0 3px #ffd34d20}
.date-warning{display:none;color:#ffd5d6;background:#772336;border:1px solid #fc777c;border-radius:8px;padding:9px 11px;font-size:12px;font-weight:700;line-height:1.4;margin-top:3px}
.date-warning.visible{display:block}.date-warning.note{color:#ffe7a4;background:#604b1b;border-color:#e7bd4f}
</style></head><body><header><img class="logo" src="/portal-logo" alt="NTP" onerror="this.style.visibility='hidden'"><div class="system-title">TAEKWONDO COMPETITION MANAGEMENT SYSTEM</div><div class="clock"><div id="date"></div><div id="time"></div></div></header>
<main><div class="title-bar"><h1>EVENT ADMINISTRATION</h1><div class="buttons"><button class="blue" onclick="location.href='/'">Home</button><button class="muted" id="logout">Sign Out</button></div></div>
<div class="split"><section class="panel"><h2 id="editorTitle">CREATE NEW EVENT</h2><form id="eventForm"><div class="fields">
<div class="field wide"><label for="event_name">Event Name *</label><input required maxlength="250" id="event_name" placeholder="Vô địch Taekwondo..." /></div>
<div class="field"><label for="event_code">Event Code * (locked after creation)</label><input required pattern="[a-z0-9_-]+" maxlength="80" id="event_code" placeholder="tre_tq_2026" /></div>
<div class="field"><label for="public_slug">Event URL Slug *</label><input required pattern="[a-z0-9_-]+" maxlength="80" id="public_slug" placeholder="tre_tq_2026" /></div>
<div class="field"><label for="date_from">Competition Start Date</label><input type="text" id="date_from" inputmode="numeric" maxlength="10" placeholder="DD/MM/YYYY" autocomplete="off" /><span class="date-format-help">Day / Month / Year</span><span class="date-warning" id="warn_date_from" role="alert"></span></div>
<div class="field"><label for="date_to">Competition End Date</label><input type="text" id="date_to" inputmode="numeric" maxlength="10" placeholder="DD/MM/YYYY" autocomplete="off" /><span class="date-format-help">Day / Month / Year</span><span class="date-warning" id="warn_date_to" role="alert"></span></div>
<div class="field wide"><label for="location">Venue</label><input id="location" maxlength="500" placeholder="Competition venue" /></div>
<div class="field"><label for="registration_open_at">Registration Opens (date & time)</label><input type="text" id="registration_open_at" inputmode="numeric" maxlength="16" placeholder="DD/MM/YYYY HH:mm" autocomplete="off" /><span class="date-format-help">DD/MM/YYYY · 24-hour time (Vietnam)</span><span class="date-warning" id="warn_registration_open_at" role="alert"></span></div>
<div class="field"><label for="registration_close_at">Registration Closes (date & time)</label><input type="text" id="registration_close_at" inputmode="numeric" maxlength="16" placeholder="DD/MM/YYYY HH:mm" autocomplete="off" /><span class="date-format-help">DD/MM/YYYY · 24-hour time (Vietnam)</span><span class="date-warning" id="warn_registration_close_at" role="alert"></span></div>
<div class="field wide"><label for="documents_deadline_at">Document Submission Deadline (date & time)</label><input type="text" id="documents_deadline_at" inputmode="numeric" maxlength="16" placeholder="DD/MM/YYYY HH:mm" autocomplete="off" /><span class="date-format-help">DD/MM/YYYY · 24-hour time (Vietnam)</span><span class="date-warning" id="warn_documents_deadline_at" role="alert"></span></div>
<div class="field wide"><label for="description">Event Details</label><textarea id="description" maxlength="3000" placeholder="Rules, notes, public information..." ></textarea></div>
<div class="field wide"><label>DOCUMENTS &amp; LINKS</label><div class="document-editor"><p class="document-help">Add regulations, registration forms, PDFs or Google Drive links. Enter a document title and URL for each item.</p><div id="documentRows"></div><button class="blue" type="button" id="addDocument">+ Add Link</button></div></div>
</div><div class="buttons"><button type="button" class="blue" id="newEvent">+ New Event</button><button type="submit" class="primary" id="saveEvent">Save Event</button></div><div class="status" role="status" id="saveStatus"></div><div class="hint">Changes are saved to sigma_events. Existing Event Codes and competition results remain unchanged. All times use Vietnam time (UTC+7).</div></form></section>
<section class="panel"><h2>EVENT LIST</h2><div class="event-list" id="eventList"><div class="hint">Loading from Supabase...</div></div><div class="buttons"><button class="blue" type="button" id="refresh">Refresh Supabase</button></div></section></div></main>
<script>
const CSRF={{ csrf|tojson }};let EVENTS=[];let selectedId=null;const fields=['event_name','event_code','public_slug','date_from','date_to','location','registration_open_at','registration_close_at','documents_deadline_at','description'];
function tick(){const d=new Date();document.getElementById('date').textContent=new Intl.DateTimeFormat('vi-VN',{day:'2-digit',month:'2-digit',year:'numeric',timeZone:'Asia/Ho_Chi_Minh'}).format(d);document.getElementById('time').textContent=new Intl.DateTimeFormat('vi-VN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,timeZone:'Asia/Ho_Chi_Minh'}).format(d)}tick();setInterval(tick,1000);
function displayStatus(msg,error=false){const e=document.getElementById('saveStatus');e.textContent=msg;e.classList.toggle('error',error)}
function isoDate(raw){const str=String(raw||'').trim();let m=str.match(/^(\d{4})-(\d{2})-(\d{2})/);if(m)return `${m[3]}/${m[2]}/${m[1]}`;m=str.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);return m?str:''}
function dateTimeLocal(raw){if(!raw)return '';const date=new Date(raw);if(Number.isNaN(date.getTime()))return '';const parts=new Intl.DateTimeFormat('en-GB',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23',timeZone:'Asia/Ho_Chi_Minh'}).formatToParts(date);const x={};parts.forEach(v=>x[v.type]=v.value);return `${x.day}/${x.month}/${x.year} ${x.hour}:${x.minute}`}
function validVNDate(raw,withTime=false){if(!raw)return true;const pattern=withTime?/^(\d{2})\/(\d{2})\/(\d{4}) (\d{2}):(\d{2})$/:/^(\d{2})\/(\d{2})\/(\d{4})$/;const m=raw.match(pattern);if(!m)return false;const day=+m[1],month=+m[2],year=+m[3];if(year<1900||year>2200||month<1||month>12||day<1||day>31)return false;const dt=new Date(Date.UTC(year,month-1,day));if(dt.getUTCFullYear()!==year||dt.getUTCMonth()!==month-1||dt.getUTCDate()!==day)return false;return !withTime||(Number(m[4])>=0&&Number(m[4])<=23&&Number(m[5])>=0&&Number(m[5])<=59)}
function documentRow(title='',url=''){const wrap=document.getElementById('documentRows');const row=document.createElement('div');row.className='document-row';const a=document.createElement('input');a.className='doc-title';a.placeholder='Title: Event Rules, Registration Form...';a.maxLength=160;a.value=title;const b=document.createElement('input');b.className='doc-url';b.type='url';b.placeholder='https://drive.google.com/...';b.maxLength=1800;b.value=url;const del=document.createElement('button');del.type='button';del.textContent='Remove';del.addEventListener('click',()=>row.remove());row.append(a,b,del);wrap.append(row)}
document.getElementById('addDocument').addEventListener('click',()=>documentRow());
function readDocuments(){return [...document.querySelectorAll('.document-row')].map(row=>({title:row.querySelector('.doc-title').value.trim(),url:row.querySelector('.doc-url').value.trim()})).filter(x=>x.title||x.url)}
function clearDateHints(){for(const k of ['date_from','date_to','registration_open_at','registration_close_at','documents_deadline_at']){const el=document.getElementById(k);el.classList.remove('date-invalid','date-adjusted');const w=document.getElementById('warn_'+k);if(w){w.textContent='';w.classList.remove('visible','note')}}}
function newEvent(){clearDateHints();selectedId=null;document.getElementById('eventForm').reset();document.getElementById('documentRows').replaceChildren();document.getElementById('event_code').disabled=false;document.getElementById('editorTitle').textContent='CREATE NEW EVENT';document.getElementById('saveEvent').textContent='Save New Event';document.querySelectorAll('.event-item').forEach(e=>e.classList.remove('active'));displayStatus('')}
function fillEvent(row){clearDateHints();selectedId=row.id;const s=row.setup||{};const vals={event_name:row.event_name||'',event_code:row.event_code||'',public_slug:s.public_slug||row.event_code||'',date_from:isoDate(s.date_from||s.start_date),date_to:isoDate(s.date_to||s.end_date),location:s.location||s.venue||'',registration_open_at:dateTimeLocal(row.registration_open_at),registration_close_at:dateTimeLocal(row.registration_close_at),documents_deadline_at:dateTimeLocal(row.documents_deadline_at),description:s.description||''};fields.forEach(k=>document.getElementById(k).value=vals[k]||'');document.getElementById('documentRows').replaceChildren();(Array.isArray(s.links)?s.links:[]).forEach(link=>documentRow(link.title||'',link.url||''));document.getElementById('event_code').disabled=true;document.getElementById('editorTitle').textContent='EDIT EVENT';document.getElementById('saveEvent').textContent='Update Event';document.querySelectorAll('.event-item').forEach(e=>e.classList.toggle('active',Number(e.dataset.id)===Number(selectedId)));displayStatus('Editing event: '+row.event_code)}
function render(){const box=document.getElementById('eventList');box.replaceChildren();if(!EVENTS.length){box.textContent='No events yet.';return}for(const row of EVENTS){const button=document.createElement('button');button.type='button';button.className='event-item';button.dataset.id=row.id;button.classList.toggle('active',Number(row.id)===Number(selectedId));const line=document.createElement('div');line.className='event-row';const info=document.createElement('div');const strong=document.createElement('strong');strong.textContent=row.event_name;const small=document.createElement('small');const s=row.setup||{};small.textContent=[row.event_code,[s.date_from,s.date_to].filter(Boolean).join(' – '),s.location||''].filter(Boolean).join(' • ');info.append(strong,small);const tag=document.createElement('span');tag.className='tag';tag.textContent=row.is_active?'Active':'Event';line.append(info,tag);button.appendChild(line);button.addEventListener('click',()=>fillEvent(row));box.append(button)}}
async function loadEvents(){const box=document.getElementById('eventList');try{const res=await fetch('/admin/events',{credentials:'same-origin',cache:'no-store'});const payload=await res.json();if(!res.ok)throw Error(payload.error||'HTTP '+res.status);EVENTS=payload.events||[];render()}catch(err){box.textContent='Unable to load events: '+err.message;displayStatus(err.message,true)}}
function dataForm(){const out={};fields.forEach(k=>out[k]=document.getElementById(k).value.trim());for(const key of ['date_from','date_to'])if(!validVNDate(out[key],false))throw Error('Invalid competition date. Use DD/MM/YYYY.');for(const key of ['registration_open_at','registration_close_at','documents_deadline_at'])if(!validVNDate(out[key],true))throw Error('Invalid date/time. Use DD/MM/YYYY HH:mm (24-hour).');out.links=readDocuments();if(out.links.length>30)throw Error('Maximum 30 links.');for(const doc of out.links){if(!doc.title||!/^https?:\/\/[^\s/]+/i.test(doc.url))throw Error('Enter a document title and a valid HTTPS URL.')}return out}

// DD/MM/YYYY HH:mm strings are interpreted in the calendar's local wall time.
// Using UTC getters/setters avoids depending on the browser computer's timezone.
const DATE_PAIRS=[['date_from','date_to',false],['registration_open_at','registration_close_at',true]];
const AUTO_DATE_DEFAULTS=new Set();
function parseVNValue(value,withTime){
  const raw=String(value||'').trim();if(!validVNDate(raw,withTime)||!raw)return null;
  const p=raw.match(/^(\d{2})\/(\d{2})\/(\d{4})(?: (\d{2}):(\d{2}))?$/);
  if(!p)return null;
  return Date.UTC(Number(p[3]),Number(p[2])-1,Number(p[1]),Number(p[4]||0),Number(p[5]||0));
}
function nextDayValue(value,withTime){
  const t=parseVNValue(value,withTime);if(t===null)return '';
  const d=new Date(t+86400000);const pad=v=>String(v).padStart(2,'0');
  const date=pad(d.getUTCDate())+'/'+pad(d.getUTCMonth()+1)+'/'+d.getUTCFullYear();
  return withTime?date+' '+pad(d.getUTCHours())+':'+pad(d.getUTCMinutes()):date;
}
function setDateWarning(field,message,kind='error'){
  const input=document.getElementById(field),w=document.getElementById('warn_'+field);
  if(!input||!w)return;
  input.classList.toggle('date-invalid',!!message&&kind==='error');
  input.setAttribute('aria-invalid',message&&kind==='error'?'true':'false');
  w.textContent=message||'';w.classList.toggle('visible',!!message);
  w.classList.toggle('note',kind==='note');
}
function checkAdminDates(){
  for(const f of ['date_from','date_to','registration_open_at','registration_close_at','documents_deadline_at'])setDateWarning(f,'');
  let ok=true;
  for(const [start,end,withTime] of DATE_PAIRS){
    const va=document.getElementById(start).value.trim(),vb=document.getElementById(end).value.trim();
    const a=parseVNValue(va,withTime),b=parseVNValue(vb,withTime);
    if(a!==null&&b!==null&&b<a){setDateWarning(end,'End date/time must not be earlier than the start.');ok=false}
    if(withTime&&a!==null&&b!==null&&b===a){setDateWarning(end,'Registration must close after it opens.');ok=false}
  }
  const regEnd=parseVNValue(document.getElementById('registration_close_at').value,true);
  const docs=parseVNValue(document.getElementById('documents_deadline_at').value,true);
  const compStart=parseVNValue(document.getElementById('date_from').value,false);
  if(regEnd!==null&&docs!==null&&docs<regEnd){
    setDateWarning('documents_deadline_at','Note: Document deadline is before registration closes. Please review.','note');
  }else if(compStart!==null&&docs!==null&&docs>=compStart+86400000){
    setDateWarning('documents_deadline_at','Note: Document deadline falls after competition begins. Please review.','note');
  }
  return ok;
}
for(const [start,end,withTime] of DATE_PAIRS){
  const startInput=document.getElementById(start),endInput=document.getElementById(end);
  const offerDefault=()=>{
    const value=startInput.value.trim();
    if(!value||parseVNValue(value,withTime)===null){checkAdminDates();return;}
    // Suggest an end one day later only when unset, never replace a manual date.
    if(!endInput.value.trim()){
      endInput.value=nextDayValue(value,withTime);
      endInput.classList.add('date-adjusted');
      setDateWarning(end,'Suggested end: 1 day after start. You can change it.','note');
    }else{checkAdminDates()}
  };
  startInput.addEventListener('change',offerDefault);
  startInput.addEventListener('blur',offerDefault);
  startInput.addEventListener('input',checkAdminDates);
  endInput.addEventListener('input',()=>{endInput.classList.remove('date-adjusted');checkAdminDates()});
  endInput.addEventListener('change',checkAdminDates);
}
const documentsField=document.getElementById('documents_deadline_at');
documentsField.addEventListener('input',checkAdminDates);
documentsField.addEventListener('change',checkAdminDates);
// Suggested document deadline: 1 day after registration closes, only if blank.
document.getElementById('registration_close_at').addEventListener('blur',()=>{
  if(!documentsField.value.trim()){
    const proposed=nextDayValue(document.getElementById('registration_close_at').value,true);
    if(proposed){documentsField.value=proposed;documentsField.classList.add('date-adjusted');setDateWarning('documents_deadline_at','Suggested deadline: 1 day after registration closes. You can change it.','note')}
  }
});

document.getElementById('eventForm').addEventListener('submit',async e=>{e.preventDefault();const btn=document.getElementById('saveEvent');btn.disabled=true;displayStatus('Saving...');try{const res=await fetch(selectedId?`/admin/events/${selectedId}`:'/admin/events',{method:selectedId?'PUT':'POST',headers:{'Content-Type':'application/json','X-Admin-CSRF':CSRF},credentials:'same-origin',body:JSON.stringify((()=>{if(!checkAdminDates())throw Error('Please correct the highlighted date/time fields.');return dataForm()})())});const result=await res.json();if(!res.ok)throw Error(result.error||'HTTP '+res.status);const id=result.event?.id||selectedId;await loadEvents();const row=EVENTS.find(r=>Number(r.id)===Number(id));if(row)fillEvent(row);displayStatus('Saved successfully to Supabase.')}catch(err){displayStatus(err.message,true)}finally{btn.disabled=false}});
document.getElementById('newEvent').addEventListener('click',newEvent);document.getElementById('refresh').addEventListener('click',loadEvents);document.getElementById('logout').addEventListener('click',async()=>{await fetch('/admin/logout',{method:'POST',headers:{'X-Admin-CSRF':CSRF},credentials:'same-origin'});location.href='/'});loadEvents();
</script></body></html>"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="127.0.0.1", port=port, debug=False)
