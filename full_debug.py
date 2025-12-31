from report_logic import load_file, parse_datetime, parse_km, split_interval_at_20, seconds_to_hhmm
from datetime import datetime, timedelta
import pandas as pd

df = load_file('sample.csv')

# Skip empty rows
df = df.dropna(how='all').copy()

# Normalize column names
df = df.rename(columns={c: str(c).strip() for c in df.columns})

# Detect columns
col_map = {}
for c in df.columns:
    uc = c.upper()
    if 'CODE' in uc:
        col_map['vehicle'] = c
    if 'HEURE' in uc and ('DÉPART' in uc or 'DEPART' in uc):
        col_map['start_time'] = c
    if 'HEURE' in uc and ('ARRÊT' in uc or 'ARRET' in uc):
        col_map['stop_time'] = c
    if 'CAA' in uc:
        col_map['caa'] = c
    if 'KM' in uc:
        col_map['km'] = c

print(f"Detected columns: {col_map}")

vcol = col_map['vehicle']
start_col = col_map['start_time']
stop_col = col_map['stop_time']
caacol = col_map['caa']
kmcol = col_map.get('km')

# Clean data
cols_to_keep = [vcol, start_col, stop_col, caacol, kmcol]
df = df[cols_to_keep].copy()
df = df.dropna(subset=[vcol, start_col, stop_col, caacol])
print(f"After initial cleanup: {len(df)} rows")

# Parse start time
df[start_col] = df[start_col].apply(parse_datetime)
df = df.dropna(subset=[start_col])
print(f"After parsing start: {len(df)} rows")

# Parse stop time
def parse_stop_time_with_date(row):
    stop_str = str(row[stop_col]).strip()
    start_dt = row[start_col]
    if not stop_str or pd.isna(start_dt):
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            stop_time = datetime.strptime(stop_str, fmt).time()
            stop_dt = datetime.combine(start_dt.date(), stop_time)
            if stop_dt < start_dt:
                stop_dt = stop_dt + timedelta(days=1)
            return stop_dt
        except Exception:
            continue
    return None

df[stop_col] = df.apply(lambda row: parse_stop_time_with_date(row), axis=1)
df = df.dropna(subset=[stop_col])
print(f"After parsing stop: {len(df)} rows")

# Parse KM
df[kmcol] = df[kmcol].apply(parse_km)

print(f"\nGrouping by {vcol}...")
print(f"Unique vehicles: {df[vcol].nunique()}")

results = []
count = 0
for vehicle, g in df.groupby(vcol):
    count += 1
    if count <= 3:
        print(f"  {vehicle}: {len(g)} records")
    
    g = g.reset_index(drop=True)
    total_before_sec = 0.0
    total_after_sec = 0.0
    km_before = 0.0
    km_after = 0.0
    
    course_count = 0
    for i, row in g.iterrows():
        if str(row[caacol]).strip().lower() != 'course':
            continue
        
        course_count += 1
        start = row[start_col]
        stop = row[stop_col]
        
        if stop <= start:
            continue
        
        km = row[kmcol]
        sec_before, sec_after = split_interval_at_20(start, stop)
        total_dur = (stop - start).total_seconds()
        if total_dur > 0:
            km_b = km * (sec_before / total_dur)
            km_a = km * (sec_after / total_dur)
        else:
            km_b = km_a = 0.0
        
        total_before_sec += sec_before
        total_after_sec += sec_after
        km_before += km_b
        km_after += km_a
    
    if count <= 3:
        print(f"    Course records: {course_count}, Time before: {seconds_to_hhmm(total_before_sec)}")
    
    results.append({
        'vehicle': vehicle,
        'time_before_hhmm': seconds_to_hhmm(total_before_sec),
        'time_after_hhmm': seconds_to_hhmm(total_after_sec),
        'km_before': round(km_before, 3),
        'km_after': round(km_after, 3)
    })

print(f"\nTotal vehicles processed: {len(results)}")
result_df = pd.DataFrame(results)
print(f"Result shape: {result_df.shape}")
print(result_df[result_df['km_before'] > 0].head(10))
