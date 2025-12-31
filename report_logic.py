import pandas as pd
from datetime import datetime, time, timedelta
from pathlib import Path
import math
import re

REF_HOUR = 20


def load_file(path: Path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() in ('.xls', '.xlsx'):
        df = pd.read_excel(path)
    else:
        # Read with semicolon separator, skip first row (metadata)
        df = pd.read_csv(path, encoding='utf-8', sep=';', skiprows=1)
    return df


def parse_datetime(x):
    if pd.isna(x):
        return None
    if isinstance(x, datetime):
        return x
    s = str(x).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M","%d/%m/%Y %H:%M:%S","%d/%m/%Y %H:%M","%d-%m-%Y %H:%M:%S","%d-%m-%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    try:
        return pd.to_datetime(s)
    except Exception:
        return None


def parse_duration(x):
    """Parse duration string like '8:00:00' or '08:00:40' to seconds."""
    if pd.isna(x):
        return 0.0
    s = str(x).strip()
    if not s:
        return 0.0
    # Try HH:MM:SS or H:MM:SS format
    parts = s.split(':')
    try:
        if len(parts) == 3:
            h, m, s_val = int(parts[0]), int(parts[1]), float(parts[2])
            return h * 3600 + m * 60 + s_val
        elif len(parts) == 2:
            h, m = int(parts[0]), int(parts[1])
            return h * 3600 + m * 60
    except Exception:
        pass
    return 0.0


def parse_km(x):
    """Parse KM value, handling comma as decimal separator."""
    if pd.isna(x):
        return 0.0
    s = str(x).strip()
    if not s:
        return 0.0
    # Replace comma with dot for decimal
    s = s.replace(',', '.')
    try:
        return float(s)
    except Exception:
        return 0.0


def split_interval_at_20(start: datetime, end: datetime):
    """Return (seconds_before20, seconds_after20) for interval [start, end).
    Handles spans across multiple days by summing multiple splits.
    """
    if end <= start:
        return 0.0, 0.0
    cur = start
    s_before = 0.0
    s_after = 0.0
    while cur < end:
        ref_dt = datetime.combine(cur.date(), time(REF_HOUR,0,0))
        # Determine segment end: either end, or next ref boundary or midnight
        seg_end = min(end, ref_dt) if cur < ref_dt else min(end, ref_dt + timedelta(days=1))
        dur = (seg_end - cur).total_seconds()
        if cur < ref_dt:
            s_before += dur
        else:
            s_after += dur
        cur = seg_end
        # if at exact ref_dt and cur < end, continue loop (will go to after segment)
        if cur == ref_dt and cur < end:
            continue
        # if seg_end reached end, loop ends
    return s_before, s_after


def seconds_to_hhmm(seconds: float):
    seconds = int(round(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h:02d}:{m:02d}"


def process_dataframe(df: pd.DataFrame, include_date=False):
    """Process DataFrame and aggregate vehicle working time and KM split at 20:00."""
    # Skip empty rows
    df = df.dropna(how='all').copy()
    
    # Normalize column names
    df = df.rename(columns={c: str(c).strip() for c in df.columns})

    # Detect columns (French names)
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

    if 'vehicle' not in col_map or 'start_time' not in col_map or 'stop_time' not in col_map or 'caa' not in col_map:
        raise ValueError(f'Could not find required columns. Found: {list(df.columns)}. Expected: Code, Heure de départ, Heure d\'arrêt, CAA.')

    vcol = col_map['vehicle']
    start_col = col_map['start_time']
    stop_col = col_map['stop_time']
    caacol = col_map['caa']
    kmcol = col_map.get('km')

    # Select and clean data
    cols_to_keep = [vcol, start_col, stop_col, caacol]
    if kmcol:
        cols_to_keep.append(kmcol)
    
    df = df[cols_to_keep].copy()
    df = df.dropna(subset=[vcol, start_col, stop_col, caacol])
    
    # Parse start time (contains full datetime)
    df[start_col] = df[start_col].apply(parse_datetime)
    df = df.dropna(subset=[start_col])
    
    # Parse stop time (contains only time, combine with date from start_time)
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
    
    # Parse KM
    if kmcol:
        df[kmcol] = df[kmcol].apply(parse_km)
    else:
        df['__km'] = 0.0
        kmcol = '__km'

    # Process each vehicle
    results = []
    for vehicle, g in df.groupby(vcol):
        g = g.reset_index(drop=True)
        day_map = {} if include_date else None
        total_before_sec = 0.0
        total_after_sec = 0.0
        km_before = 0.0
        km_after = 0.0
        
        for i, row in g.iterrows():
            if str(row[caacol]).strip().lower() != 'course':
                continue
            
            start = row[start_col]
            stop = row[stop_col]
            
            if stop <= start:
                continue
            
            km = row[kmcol]
            
            # Split time and KM at 20:00
            sec_before, sec_after = split_interval_at_20(start, stop)
            
            # Proportionally allocate KM
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
            
            if include_date:
                day_key = (start.year, start.month, start.day)
                if day_key not in day_map:
                    day_map[day_key] = {'before_sec': 0.0, 'after_sec': 0.0, 'km_before': 0.0, 'km_after': 0.0}
                day_map[day_key]['before_sec'] += sec_before
                day_map[day_key]['after_sec'] += sec_after
                day_map[day_key]['km_before'] += km_b
                day_map[day_key]['km_after'] += km_a
        
        results.append({
            'vehicle': vehicle,
            'time_before_hhmm': seconds_to_hhmm(total_before_sec),
            'time_after_hhmm': seconds_to_hhmm(total_after_sec),
            'time_before_seconds': int(round(total_before_sec)),
            'time_after_seconds': int(round(total_after_sec)),
            'km_before': round(km_before, 3),
            'km_after': round(km_after, 3),
            'day_map': day_map
        })
    
    return pd.DataFrame(results)


def generate_reports(infile: Path, outdir: Path, period='daily', out_format='csv'):
    df = load_file(infile)
    processed = process_dataframe(df, include_date=True)

    if period == 'daily':
        # Generate one report per day
        all_daily = []
        for _, row in processed.iterrows():
            vehicle = row['vehicle']
            day_map = row['day_map']
            if day_map:
                for (year, month, day), metrics in day_map.items():
                    all_daily.append({
                        'date': f"{year:04d}-{month:02d}-{day:02d}",
                        'vehicle': vehicle,
                        'hours_before_20h': round(metrics['before_sec'] / 3600, 2),
                        'hours_after_20h': round(metrics['after_sec'] / 3600, 2),
                        'time_before_hhmm': seconds_to_hhmm(metrics['before_sec']),
                        'time_after_hhmm': seconds_to_hhmm(metrics['after_sec']),
                        'km_before': round(metrics['km_before'], 3),
                        'km_after': round(metrics['km_after'], 3)
                    })
        report_df = pd.DataFrame(all_daily)
        out = outdir / f"report_daily_{infile.stem}.{out_format}"
    else:
        # Generate one report per month
        all_monthly = []
        for _, row in processed.iterrows():
            vehicle = row['vehicle']
            day_map = row['day_map']
            month_map = {}  # {(year, month): {before_sec, after_sec, km_before, km_after}}
            if day_map:
                for (year, month, day), metrics in day_map.items():
                    month_key = (year, month)
                    if month_key not in month_map:
                        month_map[month_key] = {'before_sec': 0.0, 'after_sec': 0.0, 'km_before': 0.0, 'km_after': 0.0}
                    month_map[month_key]['before_sec'] += metrics['before_sec']
                    month_map[month_key]['after_sec'] += metrics['after_sec']
                    month_map[month_key]['km_before'] += metrics['km_before']
                    month_map[month_key]['km_after'] += metrics['km_after']
            for (year, month), metrics in month_map.items():
                all_monthly.append({
                    'year_month': f"{year:04d}-{month:02d}",
                    'vehicle': vehicle,
                    'hours_before_20h': round(metrics['before_sec'] / 3600, 2),
                    'hours_after_20h': round(metrics['after_sec'] / 3600, 2),
                    'time_before_hhmm': seconds_to_hhmm(metrics['before_sec']),
                    'time_after_hhmm': seconds_to_hhmm(metrics['after_sec']),
                    'km_before': round(metrics['km_before'], 3),
                    'km_after': round(metrics['km_after'], 3)
                })
        report_df = pd.DataFrame(all_monthly)
        out = outdir / f"report_monthly_{infile.stem}.{out_format}"

    if out_format == 'csv':
        report_df.to_csv(out, index=False)
    else:
        report_df.to_excel(out, index=False)
    print(f"Wrote {out}")
    return report_df
