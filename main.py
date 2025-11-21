from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from datetime import datetime
import importlib.util
import sys
import re
import os
import pandas as pd
import requests
app = FastAPI(title="PHIVOLCS Earthquake Viewer", version="1.0.0")

# Templates directory (create templates/earthquakes.html)
os.makedirs("templates", exist_ok=True)
templates = Jinja2Templates(directory="templates")

# Adjust this if your submodule folder name is different
SUBMODULE_DIR = Path(__file__).parent / "phivolcs-earthquake-data-scraper"
SCRAPER_FILE = SUBMODULE_DIR / "scrape_phivolcs.py"  # common filename in that repo


# Small local lookup table for common Philippine places (centroid coordinates).
# This is an offline fallback for when the source doesn't provide lat/lon.
# Add more entries as needed; keys are normalized (lowercase).
PLACE_COORDS = {
    "manila": (14.5995, 120.9842),
    "quezon city": (14.6760, 121.0437),
    "cebu": (10.3157, 123.8854),
    "cebu city": (10.3157, 123.8854),
    "davao": (7.1907, 125.4553),
    "davao city": (7.1907, 125.4553),
    "davao occidental": (6.75, 124.0),
    "davao oriental": (8.7, 126.5),
    "davao del sur": (6.75, 125.25),
    "occidental mindoro": (13.1333, 120.4833),
    "batangas": (13.7565, 121.0583),
    "zambales": (15.1980, 119.9806),
    "abran": (17.6495, 120.6169),
    "abra": (17.6495, 120.6169),
    "ilocos sur": (17.5669, 120.3832),
    "sarangani": (6.0590, 125.1581),
    "batangas": (13.7565, 121.0583),
    "batanes": (20.4470, 121.9803),
    "palawan": (9.8349, 118.7381),
    "sabtang": (20.4497, 120.3550),
    "sabtang": (20.4497, 120.3550),
    "sabtang (batanes)": (20.4497, 120.3550),
    "batangas": (13.7565, 121.0583)
}


def estimate_latlon(location_text: str):
    """Best-effort offline estimator for place -> (lat, lon).

    Looks for a place name inside parentheses first, then after the last 'of',
    then tries to match substrings against PLACE_COORDS keys.
    Returns (lat, lon) or (None, None) when not found.
    """
    if not location_text:
        return None, None

    text = str(location_text).strip()
    # prefer parenthetical content
    m = re.search(r"\(([^)]+)\)", text)
    candidate = None
    if m:
        candidate = m.group(1).strip()
    else:
        # try to find the last ' of ' token
        if ' of ' in text.lower():
            parts = re.split(r'(?i)\s+of\s+', text)
            candidate = parts[-1].strip()
        else:
            # fallback: take the last comma-separated part or the whole string
            if ',' in text:
                candidate = text.split(',')[-1].strip()
            else:
                candidate = text

    cand = candidate.lower() if candidate else ''

    # direct lookup
    if cand in PLACE_COORDS:
        return PLACE_COORDS[cand]

    # substring match (e.g., 'Looc (Occidental Mindoro)' -> match 'occidental mindoro')
    for key, coord in PLACE_COORDS.items():
        if key in cand:
            return coord

    # try token matching (words)
    tokens = re.split(r'\W+', cand)
    for t in tokens:
        if not t:
            continue
        for key, coord in PLACE_COORDS.items():
            if t in key:
                return coord

    return None, None


def load_scraper_module():
    """Load the scraper module from the submodule path using importlib."""
    if not SCRAPER_FILE.exists():
        raise FileNotFoundError(f"Scraper file not found: {SCRAPER_FILE}")
    spec = importlib.util.spec_from_file_location("phivolcs_scraper", str(SCRAPER_FILE))
    module = importlib.util.module_from_spec(spec)
    sys.modules["phivolcs_scraper"] = module
    spec.loader.exec_module(module)
    return module


def fetch_earthquakes():
    """
    Call the scraper function inside the submodule.
    Tries several common function names and returns a list of dicts.
    """
    mod = load_scraper_module()

    # Try common function names used in scrapers
    for fn_name in ("scrape_phivolcs", "scrape", "get_earthquakes", "get_data"):
        fn = getattr(mod, fn_name, None)
        if callable(fn):
            return fn()

    # Fallback: maybe the module exposes a variable
    if hasattr(mod, "EARTHQUAKES"):
        return getattr(mod, "EARTHQUAKES")
    # Fallback 2: try to read a combined CSV produced by the scraper (if present).
    # This allows the API to serve data without invoking the live scraper.
    combined_csv = SUBMODULE_DIR / "data" / "phivolcs_earthquake_all_years.csv"
    if combined_csv.exists():
        try:
            df = pd.read_csv(combined_csv)
            # Normalize column names to keys expected by the API/template
            # The scraper uses columns like 'Date-Time', 'Magnitude', 'Location', 'Depth'
            records = []
            for _, row in df.iterrows():
                # Try several column name alternatives for date/time/magnitude
                date = row.get('Date-Time') if 'Date-Time' in row.index else row.get('date', "")
                # If date contains both date and time, try to split (best-effort)
                time = ""
                if isinstance(date, str) and " " in date:
                    parts = date.split(None, 1)
                    date = parts[0]
                    time = parts[1] if len(parts) > 1 else ""

                mag = None
                for mag_col in ('Magnitude', 'magnitude', 'Mag'):
                    if mag_col in row.index:
                        mag = row.get(mag_col)
                        break

                records.append({
                    'date': date if date is not None else "",
                    'time': time,
                    'location': row.get('Location') if 'Location' in row.index else row.get('location', ""),
                    'magnitude': mag,
                    'depth': row.get('Depth') if 'Depth' in row.index else row.get('depth', ""),
                    # Try to grab lat/lon if present in CSV
                    'latitude': (row.get('Latitude') if 'Latitude' in row.index else row.get('latitude') if 'latitude' in row.index else row.get('Lat') if 'Lat' in row.index else None),
                    'longitude': (row.get('Longitude') if 'Longitude' in row.index else row.get('longitude') if 'longitude' in row.index else row.get('Lon') if 'Lon' in row.index else None)
                })
            return records
        except Exception as e:
            print(f"Failed to read fallback CSV {combined_csv}: {e}")

    raise RuntimeError("No scraper function or data found in submodule")


@app.get("/api/earthquakes")
async def api_earthquakes():
    """Return JSON list of earthquakes (magnitude >= 3.0)."""
    try:
        data = fetch_earthquakes()
        filtered = []
        for eq in data:
            try:
                mag = float(eq.get("magnitude", 0))
            except (TypeError, ValueError):
                continue
            if mag >= 3.0:
                # Try to capture latitude/longitude if present in source data
                lat = None
                lon = None
                for lat_key in ("Latitude", "latitude", "lat", "Lat"):
                    if lat_key in eq:
                        lat = eq.get(lat_key)
                        break
                for lon_key in ("Longitude", "longitude", "lon", "Lon"):
                    if lon_key in eq:
                        lon = eq.get(lon_key)
                        break

                # Normalize numeric lat/lon where possible
                try:
                    lat = float(lat) if lat is not None and lat != "" else None
                except Exception:
                    lat = None
                try:
                    lon = float(lon) if lon is not None and lon != "" else None
                except Exception:
                    lon = None

                # If lat/lon still missing, try offline estimator from location text
                if lat is None or lon is None:
                    est_lat, est_lon = estimate_latlon(eq.get("location", ""))
                    if est_lat is not None and est_lon is not None:
                        lat = lat or est_lat
                        lon = lon or est_lon

                # Compute a display radius in meters (assumption: radius scales with magnitude)
                radius_m = None
                try:
                    radius_m = float(mag) * 10000.0  # 10 km per magnitude unit (tunable)
                except Exception:
                    radius_m = None

                filtered.append({
                    "date": eq.get("date", ""),
                    "time": eq.get("time", ""),
                    "location": eq.get("location", "Unknown"),
                    "magnitude": mag,
                    "depth": eq.get("depth", "N/A"),
                    "latitude": lat,
                    "longitude": lon,
                    "radius_m": radius_m
                })
        # Sort so newest records come first. Best-effort parsing of date/time.
        def _dt_key(item):
            # Try join date and time if both present
            date_str = item.get("date") or ""
            time_str = item.get("time") or ""
            dt_str = (date_str + " " + time_str).strip()
            if not dt_str:
                return datetime.min
            # Normalize separators and remove stray non-breaking or weird characters
            dt_str = dt_str.replace('\u00A0', ' ')  # NBSP
            dt_str = re.sub(r"[\u2013\u2014\-]+", ' ', dt_str)  # replace dashes/–/— with space
            dt_str = re.sub(r"\s+", ' ', dt_str).strip()

            # Common formats to try (including patterns like '31 January 2023 11:29 PM')
            fmts = [
                "%d %B %Y %I:%M %p",
                "%d %B %Y %I:%M:%S %p",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%d-%b-%Y %H:%M:%S",
                "%d-%b-%Y %H:%M",
                "%d %B %Y %H:%M:%S",
                "%d %B %Y %H:%M",
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M",
            ]
            for f in fmts:
                try:
                    return datetime.strptime(dt_str, f)
                except Exception:
                    continue
            # Try ISO parse
            try:
                return datetime.fromisoformat(dt_str)
            except Exception:
                pass
            # Try parsing only the date portion
            try:
                return datetime.fromisoformat(date_str)
            except Exception:
                return datetime.min

        filtered.sort(key=_dt_key, reverse=True)
        return JSONResponse({"status": "success", "data": filtered})
        return JSONResponse({"status": "success", "data": filtered})
    except Exception as e:
        # Log to stdout so Railway/GitHub Actions logs show it
        print("Error in /api/earthquakes:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the HTML page using templates/earthquakes.html."""
    try:
        data = fetch_earthquakes()
        # Filter and normalize for template
        filtered = []
        for eq in data:
            try:
                mag = float(eq.get("magnitude", 0))
            except (TypeError, ValueError):
                continue
            if mag >= 3.0:
                # include lat/lon and radius for template
                lat = None
                lon = None
                for lat_key in ("Latitude", "latitude", "lat", "Lat"):
                    if lat_key in eq:
                        lat = eq.get(lat_key)
                        break
                for lon_key in ("Longitude", "longitude", "lon", "Lon"):
                    if lon_key in eq:
                        lon = eq.get(lon_key)
                        break

                try:
                    lat = float(lat) if lat not in (None, "") else None
                except Exception:
                    lat = None
                try:
                    lon = float(lon) if lon not in (None, "") else None
                except Exception:
                    lon = None

                # If lat/lon still missing, try offline estimator from location text
                if lat is None or lon is None:
                    est_lat, est_lon = estimate_latlon(eq.get("location", ""))
                    if est_lat is not None and est_lon is not None:
                        lat = lat or est_lat
                        lon = lon or est_lon

                radius_m = None
                try:
                    radius_m = float(mag) * 10000.0
                except Exception:
                    radius_m = None

                filtered.append({
                    "date": eq.get("date", ""),
                    "time": eq.get("time", ""),
                    "location": eq.get("location", "Unknown"),
                    "magnitude": mag,
                    "depth": eq.get("depth", "N/A"),
                    "latitude": lat,
                    "longitude": lon,
                    "radius_m": radius_m
                })
        # Sort newest first using same logic as the API endpoint
        def _dt_key_item(item):
            date_str = item.get("date") or ""
            time_str = item.get("time") or ""
            dt_str = (date_str + " " + time_str).strip()
            if not dt_str:
                return datetime.min
            dt_str = dt_str.replace('\u00A0', ' ')
            dt_str = re.sub(r"[\u2013\u2014\-]+", ' ', dt_str)
            dt_str = re.sub(r"\s+", ' ', dt_str).strip()

            fmts = [
                "%d %B %Y %I:%M %p",
                "%d %B %Y %I:%M:%S %p",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%d-%b-%Y %H:%M:%S",
                "%d-%b-%Y %H:%M",
                "%d %B %Y %H:%M:%S",
                "%d %B %Y %H:%M",
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M",
            ]
            for f in fmts:
                try:
                    return datetime.strptime(dt_str, f)
                except Exception:
                    continue
            try:
                return datetime.fromisoformat(dt_str)
            except Exception:
                pass
            try:
                return datetime.fromisoformat(date_str)
            except Exception:
                return datetime.min

        filtered.sort(key=_dt_key_item, reverse=True)
        return templates.TemplateResponse("earthquakes.html", {"request": request, "earthquakes": filtered})
        return templates.TemplateResponse("earthquakes.html", {"request": request, "earthquakes": filtered})
    except Exception as e:
        print("Error in /:", repr(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/reverse_geocode")
async def api_reverse_geocode(lat: float, lon: float):
    """Reverse geocode a coordinate using OpenRouteService. Requires ORS_API_KEY env var.

    Returns a JSON object with 'label' and 'properties' when available.
    """
    key = os.getenv('ORS_API_KEY')
    if not key:
        raise HTTPException(status_code=400, detail="ORS API key not configured. Set ORS_API_KEY environment variable.")

    url = 'https://api.openrouteservice.org/geocode/reverse'
    params = {
        'api_key': key,
        'point.lat': lat,
        'point.lon': lon,
        'size': 1
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to contact ORS: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"ORS returned {resp.status_code}: {resp.text}")

    data = resp.json()
    # Safe extraction
    label = None
    properties = None
    try:
        features = data.get('features', [])
        if features:
            props = features[0].get('properties', {})
            label = props.get('label') or props.get('name') or props.get('county') or props.get('locality')
            properties = props
    except Exception:
        pass

    return JSONResponse({"status": "success", "label": label, "properties": properties, "raw": data})