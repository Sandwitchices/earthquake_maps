#!/usr/bin/env python3
"""
Annotate PHIVOLCS earthquake CSV with latitude and longitude based on
the city/province extracted from the parenthetical part of the location text.

Writes a new CSV next to the input file named
`phivolcs_earthquake_all_years_with_coords.csv`.

Usage:
  python scripts/assign_coords.py

This is a best-effort offline annotator using a small PLACE_COORDS mapping.
"""
from pathlib import Path
import re
import pandas as pd


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
    "abra": (17.6495, 120.6169),
    "ilocos sur": (17.5669, 120.3832),
    "sarangani": (6.0590, 125.1581),
    "batanes": (20.4470, 121.9803),
    "palawan": (9.8349, 118.7381),
}


def extract_parenthetical(text: str) -> str:
    if not isinstance(text, str):
        return ""
    m = re.search(r"\(([^)]+)\)", text)
    if m:
        return m.group(1).strip()
    return ""


def estimate_latlon(location_text: str):
    """Estimate lat/lon from freeform location text using PLACE_COORDS."""
    if not location_text:
        return None, None
    text = str(location_text).strip()
    # prefer parenthetical content
    candidate = extract_parenthetical(text) or text

    # if ' of ' present, take the last part
    if not candidate and " of " in text.lower():
        parts = re.split(r"(?i)\s+of\s+", text)
        candidate = parts[-1].strip()

    cand = candidate.lower() if candidate else ""
    if cand in PLACE_COORDS:
        return PLACE_COORDS[cand]

    # substring match
    for key, coord in PLACE_COORDS.items():
        if key in cand:
            return coord

    # token match
    tokens = re.split(r"\W+", cand)
    for t in tokens:
        if not t:
            continue
        for key, coord in PLACE_COORDS.items():
            if t in key:
                return coord

    return None, None


def annotate_csv(in_csv: Path):
    if not in_csv.exists():
        print(f"Input CSV not found: {in_csv}")
        return
    df = pd.read_csv(in_csv)

    lat_col = []
    lon_col = []

    for _, row in df.iterrows():
        loc = ""
        # try several common column names
        for col in ("Location", "location", "LOCATION"):
            if col in row.index:
                loc = row.get(col)
                break

        lat, lon = None, None
        # if CSV already contains lat/lon columns, prefer them
        for lat_key in ("Latitude", "latitude", "lat", "Lat"):
            if lat_key in row.index and pd.notna(row.get(lat_key)):
                try:
                    lat = float(row.get(lat_key))
                except Exception:
                    lat = None
                break
        for lon_key in ("Longitude", "longitude", "lon", "Lon"):
            if lon_key in row.index and pd.notna(row.get(lon_key)):
                try:
                    lon = float(row.get(lon_key))
                except Exception:
                    lon = None
                break

        if lat is None or lon is None:
            # try to extract parenthetical city first
            paren = extract_parenthetical(loc)
            lat, lon = estimate_latlon(paren or loc)

        lat_col.append(lat if lat is not None else "")
        lon_col.append(lon if lon is not None else "")

    df["latitude"] = lat_col
    df["longitude"] = lon_col

    out = in_csv.with_name(in_csv.stem + "_with_coords" + in_csv.suffix)
    df.to_csv(out, index=False)
    print(f"Wrote annotated CSV to: {out}")


if __name__ == "__main__":
    # try the submodule data path first
    repo_root = Path(__file__).parent.parent
    candidate = repo_root / "phivolcs-earthquake-data-scraper" / "data" / "phivolcs_earthquake_all_years.csv"
    if not candidate.exists():
        # fallback to local data folder
        candidate = repo_root / "phivolcs_earthquake_all_years.csv"

    annotate_csv(candidate)
