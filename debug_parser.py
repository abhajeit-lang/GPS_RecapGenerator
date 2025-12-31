from report_logic import load_file, parse_datetime, parse_km
from datetime import datetime, timedelta
import pandas as pd

df = load_file('sample.csv')

# Get just Course records
df = df[df['CAA'] == 'Course'].copy()
print(f"Total Course records: {len(df)}")

# Test column detection
vcol = 'Code'
start_col = 'Heure de départ'
stop_col = 'Heure d\'arrêt'
caacol = 'CAA'
kmcol = 'KM'

cols_to_keep = [vcol, start_col, stop_col, caacol, kmcol]
df = df[cols_to_keep].copy()
df = df.dropna(subset=[vcol, start_col, stop_col, caacol])
print(f"After dropping NaN: {len(df)}")

# Parse start time
df[start_col] = df[start_col].apply(parse_datetime)
df = df.dropna(subset=[start_col])
print(f"After parsing start_time: {len(df)}")
print("Sample start times:", df[start_col].head())

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
        except Exception as e:
            continue
    return None

df[stop_col] = df.apply(lambda row: parse_stop_time_with_date(row), axis=1)
print(f"After parsing stop_time: {len(df)}")
df = df.dropna(subset=[stop_col])
print(f"After dropping NaN stop times: {len(df)}")
print("Sample records:")
print(df[[vcol, start_col, stop_col, kmcol]].head())
