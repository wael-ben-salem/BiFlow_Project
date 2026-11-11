"""
Télécharge 1 an de demandes NYC 311 (par défaut 2023-01 → 2024-01),
par pages de 50k, avec retry sur timeout, sauvegarde/reprise via .part,
et écrit le parquet attendu par BIFlow.
"""
import os
import time
from io import StringIO

import pandas as pd
import requests

# ============ PARAMÈTRES ============
BASE = "https://data.cityofnewyork.us/resource/erm2-nwe9.csv"
START = "2023-01-01"
END   = "2024-01-01"          # 12 mois
PAGE = 50000
OUT = "data/raw/nyc311_2023-01_2026-09_echantillon.parquet"  # nom attendu par BIFlow
PART = OUT + ".part"
META = OUT + ".offset"
MAX_RETRIES = 5
TIMEOUT = 300
SLEEP_BETWEEN = 0.5
# ===================================

WHERE = f"created_date>='{START}T00:00:00' AND created_date<'{END}T00:00:00'"
ORDER = "created_date ASC"

os.makedirs("data/raw", exist_ok=True)

# ---- Reprise ----
offset = 0
if os.path.exists(META):
    with open(META) as f:
        offset = int(f.read().strip())
    print(f"Reprise depuis offset={offset}")

frames = []
if os.path.exists(PART):
    print(f"Chargement du partiel {PART}...")
    frames.append(pd.read_parquet(PART))
    print(f"  {len(frames[-1])} lignes en cache")

# ---- Boucle ----
while True:
    params = {"$where": WHERE, "$order": ORDER, "$limit": PAGE, "$offset": offset}
    r = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(BASE, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            break
        except Exception as e:
            print(f"  [retry {attempt}/{MAX_RETRIES}] offset={offset} : {type(e).__name__}")
            time.sleep(5 * attempt)
    else:
        print(f"[ABANDON] offset={offset}. Relance le script, il reprendra.")
        break

    df = pd.read_csv(StringIO(r.text), dtype=str)
    if df.empty:
        print(f"[FIN] offset={offset} : plus de lignes.")
        break

    frames.append(df)
    total = sum(len(f) for f in frames)
    print(f"offset={offset:>9}  recu={len(df):>5}  total={total:>9}")

    offset += len(df)
    with open(META, "w") as f:
        f.write(str(offset))

    if len(frames) % 5 == 0:
        pd.concat(frames, ignore_index=True).to_parquet(PART, index=False)
        print(f"  [sauvegarde] {PART}  ({total} lignes)")

    if len(df) < PAGE:
        print(f"[FIN] dernière page incomplète ({len(df)} < {PAGE}).")
        break

    time.sleep(SLEEP_BETWEEN)

# ---- Assemblage ----
if not frames:
    print("Rien téléchargé.")
    raise SystemExit(1)

print("Assemblage final...")
all_df = pd.concat(frames, ignore_index=True)

for col in ("created_date", "closed_date"):
    if col in all_df.columns:
        all_df[col] = pd.to_datetime(all_df[col], errors="coerce")

print(f"Écriture de {OUT} ...")
all_df.to_parquet(OUT, index=False)

for f in (PART, META):
    if os.path.exists(f):
        os.remove(f)

print("OK", all_df.shape)
print("dates :", all_df["created_date"].min(), "->", all_df["created_date"].max())
print("boroughs :", all_df["borough"].value_counts(dropna=False).to_dict())