"""Génère public/conditions.json pour École-Surf.fr.

Sources (gratuites, usage commercial autorisé, attribution obligatoire) :
- Copernicus Marine Service : houle (GLOBAL_ANALYSISFORECAST_WAV_001_027),
  température de l'eau (GLOBAL_ANALYSISFORECAST_PHY_001_024),
  hauteur d'eau avec marée sur la façade Atlantique/Manche (IBI_ANALYSISFORECAST_PHY_005_001).
- MET Norway (api.met.no, CC BY 4.0) : vent, température de l'air, ciel.

Identifiants Copernicus lus dans les variables d'environnement
COPERNICUSMARINE_SERVICE_USERNAME / COPERNICUSMARINE_SERVICE_PASSWORD (secrets GitHub).
"""
import json
import math
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import copernicusmarine as cm

UA = "ecole-surf.fr https://ecole-surf.fr/ (github: ecole-surf-conditions)"
WAV = "cmems_mod_glo_wav_anfc_0.083deg_PT3H-i"
PHY = "cmems_mod_glo_phy_anfc_0.083deg_PT1H-m"
IBI = "cmems_mod_ibi_phy_anfc_0.027deg-2D_PT1H-m"
OUT = os.path.join(os.path.dirname(__file__), "public", "conditions.json")

NOW = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
T0 = NOW - timedelta(hours=30)  # couvre toute la journée locale (courbe de marée)
T1 = NOW + timedelta(days=4)


def log(*a):
    print(*a, flush=True)


def r1(x, n=1):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else round(x, n)


def open_ds(dataset_id, variables, spots, pad=0.35, depth=False):
    lats = [s["lat"] for s in spots]
    lngs = [s["lng"] for s in spots]
    kw = dict(
        dataset_id=dataset_id,
        variables=variables,
        minimum_longitude=min(lngs) - pad,
        maximum_longitude=max(lngs) + pad,
        minimum_latitude=min(lats) - pad,
        maximum_latitude=max(lats) + pad,
        start_datetime=T0.strftime("%Y-%m-%dT%H:%M:%S"),
        end_datetime=T1.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    if depth:
        kw.update(minimum_depth=0, maximum_depth=1)
    ds = cm.open_dataset(**kw)
    if "depth" in ds.dims:
        ds = ds.isel(depth=0)
    return ds.load()


def nearest_valid(da, lat, lng):
    """Série temporelle de la cellule marine valide la plus proche du spot."""
    first = da.isel(time=0)
    vals = first.values
    la = first["latitude"].values
    lo = first["longitude"].values
    LO, LA = np.meshgrid(lo, la)
    d = (LA - lat) ** 2 + ((LO - lng) * math.cos(math.radians(lat))) ** 2
    d = np.where(np.isnan(vals), np.inf, d)
    if not np.isfinite(d).any():
        return None
    i, j = np.unravel_index(np.argmin(d), d.shape)
    s = da.isel(latitude=i, longitude=j).to_series()
    s.index = pd.to_datetime(s.index, utc=True)
    return s.dropna()


def at_now(s):
    if s is None or s.empty:
        return None
    idx = s.index.get_indexer([pd.Timestamp(NOW)], method="nearest")[0]
    return s.iloc[idx]


def daily_max(s, tz, days):
    if s is None or s.empty:
        return [None] * len(days)
    loc = s.tz_convert(tz)
    out = []
    for d in days:
        part = loc[loc.index.date == d]
        out.append(r1(part.max()) if not part.empty else None)
    return out


def metno(lat, lng):
    url = "https://api.met.no/weatherapi/locationforecast/2.0/complete"
    r = requests.get(url, params={"lat": round(lat, 4), "lon": round(lng, 4)}, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    rows = []
    for t in r.json()["properties"]["timeseries"]:
        det = t["data"]["instant"]["details"]
        sym = None
        for k in ("next_1_hours", "next_6_hours", "next_12_hours"):
            if k in t["data"]:
                sym = t["data"][k]["summary"]["symbol_code"]
                break
        rows.append({"time": pd.Timestamp(t["time"]), "air": det.get("air_temperature"),
                     "ws": det.get("wind_speed"), "wd": det.get("wind_from_direction"), "sym": sym})
    df = pd.DataFrame(rows).set_index("time")
    return df


def build_region(slug, reg):
    tz = ZoneInfo(reg["tz"])
    spots = reg["spots"]
    today = datetime.now(tz).date()
    days = [today + timedelta(days=k) for k in range(3)]
    res = [dict(n=s["n"], c=s["c"], lv=s["lv"], face=s["face"]) for s in spots]

    try:
        wav = open_ds(WAV, ["VHM0", "VTPK", "VMDR"], spots)
        for s, o in zip(spots, res):
            hs = nearest_valid(wav["VHM0"], s["lat"], s["lng"])
            if hs is None:
                continue
            tp = nearest_valid(wav["VTPK"], s["lat"], s["lng"])
            dr = nearest_valid(wav["VMDR"], s["lat"], s["lng"])
            o["hs"], o["tp"], o["dir"] = r1(at_now(hs)), r1(at_now(tp), 0), r1(at_now(dr), 0)
            o["d_hs"], o["d_tp"] = daily_max(hs, tz, days), [None if v is None else round(v) for v in daily_max(tp, tz, days)]
    except Exception:
        log(f"[{slug}] houle KO"); traceback.print_exc()

    try:
        phy = open_ds(PHY, ["thetao"], spots, depth=True)
        for s, o in zip(spots, res):
            o["sst"] = r1(at_now(nearest_valid(phy["thetao"], s["lat"], s["lng"])))
    except Exception:
        log(f"[{slug}] température eau KO"); traceback.print_exc()

    if reg["tide"] == "ibi":
        try:
            ibi = open_ds(IBI, ["zos"], spots, pad=0.15)
            start = pd.Timestamp(datetime.combine(today, datetime.min.time()), tz=tz).tz_convert("UTC")
            hours = pd.date_range(start, periods=24, freq="h")
            for s, o in zip(spots, res):
                z = nearest_valid(ibi["zos"], s["lat"], s["lng"])
                if z is None or z.empty:
                    continue
                z = z[~z.index.duplicated()].sort_index()
                serie = z.reindex(z.index.union(hours)).interpolate(method="time").reindex(hours)
                if serie.notna().sum() >= 20:
                    o["tide"] = [r1(v, 2) for v in serie.values]
        except Exception:
            log(f"[{slug}] marée KO"); traceback.print_exc()

    for s, o in zip(spots, res):
        try:
            df = metno(s["lat"], s["lng"])
            cur = df.iloc[df.index.get_indexer([pd.Timestamp(NOW)], method="nearest")[0]]
            o["air"], o["ws"], o["wd"], o["sym"] = r1(cur["air"], 0), r1(cur["ws"] * 3.6, 0), r1(cur["wd"], 0), cur["sym"]
            loc = df.tz_convert(tz)
            o["d_ws"] = [r1(loc[loc.index.date == d]["ws"].max() * 3.6, 0) if (loc.index.date == d).any() else None for d in days]
            o["d_air"] = [r1(loc[loc.index.date == d]["air"].max(), 0) if (loc.index.date == d).any() else None for d in days]
        except Exception:
            log(f"[{slug}] météo KO pour {s['n']}"); traceback.print_exc()
        time.sleep(0.3)

    return {"tz": reg["tz"], "tideModel": reg["tide"], "days": [d.isoformat() for d in days], "spots": res}


def main():
    regions = json.load(open(os.path.join(os.path.dirname(__file__), "spots.json"), encoding="utf-8"))
    only = sys.argv[1:]
    out = {"updated": NOW.isoformat(), "sources": "Copernicus Marine Service ; MET Norway (CC BY 4.0)", "regions": {}}
    ok = 0
    for slug, reg in regions.items():
        if only and slug not in only:
            continue
        log(f"→ {slug}")
        out["regions"][slug] = build_region(slug, reg)
        ok += sum(1 for s in out["regions"][slug]["spots"] if s.get("hs") is not None)
    log(f"Spots avec houle : {ok}")
    if ok == 0:
        log("Aucune donnée de houle : on ne publie pas.")
        sys.exit(1)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"Écrit {OUT} ({os.path.getsize(OUT)} octets)")


if __name__ == "__main__":
    main()
