import pandas as pd
from datetime import datetime, time, timedelta
from pathlib import Path
import math
import re


# Default reference time (fallback)
DEFAULT_REF_HOUR = 20
DEFAULT_REF_MIN = 0


def format_decimal_hours(decimal_hours):
    """Convert decimal hours to 'XhYmin' format.
    Example: 0.80 -> '48min', 1.33 -> '1h20min'
    """
    if not decimal_hours or decimal_hours == 0:
        return '0min'
    
    hours = int(decimal_hours)
    minutes = round((decimal_hours - hours) * 60)
    
    if minutes == 60:
        hours += 1
        minutes = 0
    
    if hours == 0:
        return f'{minutes}min'
    elif minutes == 0:
        return f'{hours}h'
    else:
        return f'{hours}h{minutes}min'


def load_file(path: Path):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() in ('.xls', '.xlsx'):
        df = pd.read_excel(path)
    else:
        # Try different encodings and header positions
        encodings = ['utf-8', 'latin-1', 'cp1252']
        for enc in encodings:
            try:
                # First pass: read a few lines to find the header
                # We look for a row containing 'CODE' and 'CAA'
                temp_df = pd.read_csv(path, encoding=enc, sep=';', nrows=10, header=None)
                header_idx = 0
                for idx, row in temp_df.iterrows():
                    row_str = " ".join(str(val).upper() for val in row.values)
                    if 'CODE' in row_str and 'CAA' in row_str:
                        header_idx = idx
                        break
                
                df = pd.read_csv(path, encoding=enc, sep=';', skiprows=header_idx)
                # If we skipped metadata, the first row might still be part of the header or empty
                if 'CODE' not in "".join(str(c).upper() for c in df.columns):
                     # Try again without skiprows if detection failed
                     df = pd.read_csv(path, encoding=enc, sep=';')
                return df
            except Exception:
                continue
        # Final fallback
        return pd.read_csv(path, encoding='utf-8', sep=';', skiprows=1)


def parse_datetime(x):
    if pd.isna(x):
        return None
    if isinstance(x, datetime):
        return x
    s = str(x).strip()
    if not s:
        return None
    # Remove any non-breaking spaces or weird chars
    s = s.replace('\xa0', ' ').replace('\u202f', ' ')
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
    """Parse KM value, handling thousands separators and comma decimals."""
    if pd.isna(x):
        return 0.0
    s = str(x).strip()
    if not s:
        return 0.0
    # Special case: '74 360' might be 74.360 in some contexts, but usually spaces are thousands separators.
    # We remove all spaces and non-breaking spaces.
    s = s.replace(' ', '').replace('\xa0', '').replace('\u202f', '')
    
    # If there are multiple dots/commas, it's a mess. 
    # Usually European: "1.234,56" -> remove "." and replace "," with "."
    if ',' in s and '.' in s:
        # Determine which one is the decimal. Usually the last one.
        if s.find(',') > s.find('.'): # 1.234,56
             s = s.replace('.', '').replace(',', '.')
        else: # 1,234.56
             s = s.replace(',', '')
    elif ',' in s:
        s = s.replace(',', '.')
        
    try:
        return float(s)
    except Exception:
        return 0.0


def split_interval_at_ref(start: datetime, end: datetime, ref_hour=DEFAULT_REF_HOUR, ref_min=DEFAULT_REF_MIN):
    """Return (seconds_before_ref, seconds_after_ref) for interval [start, end).
    Handles spans across multiple days by summing multiple splits.
    Reference time: ref_hour:ref_min (default 20:00)
    """
    if end <= start:
        return 0.0, 0.0
    cur = start
    s_before = 0.0
    s_after = 0.0
    while cur < end:
        ref_dt = datetime.combine(cur.date(), time(ref_hour, ref_min, 0))
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


def process_dataframe(df: pd.DataFrame, include_date=False, ref_hour=DEFAULT_REF_HOUR, ref_min=DEFAULT_REF_MIN, vehicle_configs=None):
    """
    Process DataFrame and aggregate vehicle working time and KM split at reference time.
    vehicle_configs: dict { vehicle_id: {'min_trip_km': float, 'max_trip_km': float} }
    """
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
        
    # Default configs
    default_min_km = 0.5
    default_max_km = 15.0

    # Process each vehicle
    results = []
    # Process each vehicle
    results = []
    for vehicle_raw, g in df.groupby(vcol):
        vehicle = str(vehicle_raw).strip() # Clean ID for lookup
        g = g.reset_index(drop=True)
        day_map = {} if include_date else None
        total_before_sec = 0.0
        total_after_sec = 0.0
        km_before = 0.0
        km_after = 0.0
        
        trip_count = 0.0  # Now float for fractional cycles
        attente_count = 0 # Count of waiting events
        km_in_range = 0.0  # KM of valid trips (within min-max)
        km_out_of_range = 0.0  # KM of trips outside range
        total_duration_course = 0.0
        total_duration_attente = 0.0
        total_duration_arret = 0.0
        
        # Determine strict bounds for this vehicle
        v_min = default_min_km
        v_max = default_max_km
        v_movement = 'Move'
        
        if vehicle_configs and vehicle in vehicle_configs:
            v_min = vehicle_configs[vehicle].get('min_trip_km', default_min_km)
            v_max = vehicle_configs[vehicle].get('max_trip_km', default_max_km)
            v_movement = vehicle_configs[vehicle].get('movement_type', 'Move')
        else:
             # Fallback: Check if config keys have whitespace issues
            if vehicle_configs:
                 for k, v in vehicle_configs.items():
                     if str(k).strip() == vehicle:
                         v_min = v.get('min_trip_km', default_min_km)
                         v_max = v.get('max_trip_km', default_max_km)
                         v_movement = v.get('movement_type', 'Move')
                         break
        
        # DEBUG C105 - Log to file
        if vehicle == 'C105':
            try:
                with open(r'c:\Users\user\Documents\GPS_Recap\debug_log.txt', 'a') as f:
                    f.write(f"\n--- Processing C105 at {datetime.now()} ---\n")
                    f.write(f"Config Key Found: {vehicle in vehicle_configs if vehicle_configs else 'No Configs'}\n")
                    f.write(f"Applied Config: Min={v_min}, Max={v_max}, Movement={v_movement}\n")
                    f.write(f"Column Map: {col_map}\n")
                    # Log first 5 CAA values
                    sample_caa = g[caacol].head(5).tolist()
                    f.write(f"Sample CAA raw: {sample_caa}\n")
            except: pass
            print(f"DEBUG C105: Min={v_min}, Max={v_max}, Movement={v_movement}")
            print(f"   Config Found? {vehicle in vehicle_configs if vehicle_configs else 'No Configs'}")

        # SMART CYCLE DETECTION: Two-pass algorithm
        # Pass 1: Collect all valid Course distances to calculate median
        course_distances = []
        if v_movement == 'Move':
            for i, row in g.iterrows():
                caa_raw = str(row[caacol]).strip().lower()
                if 'course' in caa_raw:
                    km = row.get(kmcol, 0.0)
                    if v_min <= km <= v_max:
                        course_distances.append(km)
        
        # Calculate median and threshold
        median_distance = 0.0
        cycle_threshold = 0.0
        if len(course_distances) > 0:
            course_distances.sort()
            n = len(course_distances)
            if n % 2 == 0:
                median_distance = (course_distances[n//2 - 1] + course_distances[n//2]) / 2.0
            else:
                median_distance = course_distances[n//2]
            cycle_threshold = median_distance * 0.7  # 70% threshold

        for i, row in g.iterrows():
            caa_raw = str(row[caacol]).strip().lower()
            activity_type = 'other'
            if 'course' in caa_raw:
                activity_type = 'course'
            elif 'attente' in caa_raw:
                activity_type = 'attente'
            elif 'arrêt' in caa_raw or 'arret' in caa_raw:
                activity_type = 'arret'
            
            start = row[start_col]
            stop = row[stop_col]
            
            if stop <= start:
                continue
                
            dur_seconds = (stop - start).total_seconds()
            km = row.get(kmcol, 0.0)
            
            # Use seconds for internal calculations, convert to hours for storage
            total_dur_sec = dur_seconds
            total_dur_hours = dur_seconds / 3600.0
            
            # Update metric totals (in hours for consistency with storage)
            # Categorize KM for current row (Vehicle level)
            is_valid_work = (activity_type == 'course' and v_movement == 'Move' and v_min <= km <= v_max)
            
            if is_valid_work:
                km_in_range += km
                if median_distance > 0:
                    # Detect half vs full cycle based on distance
                    if km >= cycle_threshold:
                        trip_count += 1.0  # Full cycle
                    else:
                        trip_count += 0.5  # Half cycle (stopped midway)
                else:
                    trip_count += 1.0 # Fallback
            else:
                km_out_of_range += km
            
            # Update duration metrics
            if activity_type == 'course':
                total_duration_course += total_dur_hours
            elif activity_type == 'attente':
                total_duration_attente += total_dur_hours
                attente_count += 1 # Count waiting events
            elif activity_type == 'arret':
                total_duration_arret += total_dur_hours
            
            # Working time logic: Course + Attente = Working Time. Arrêt = Stop.
            is_working = activity_type in ('course', 'attente')
            
            # Split time and KM at reference time
            sec_before, sec_after = split_interval_at_ref(start, stop, ref_hour, ref_min)
            
            # Proportionally allocate KM (using seconds for both)
            if total_dur_sec > 0:
                km_b = km * (sec_before / total_dur_sec)
                km_a = km * (sec_after / total_dur_sec)
            else:
                km_b = km_a = 0.0
            
            # Only add to "Working Hours" (before/after split) if it is working time
            if is_working:
                total_before_sec += sec_before
                total_after_sec += sec_after
            
            km_before += km_b
            km_after += km_a
            
            if include_date:
                day_key = (start.year, start.month, start.day)
                if day_key not in day_map:
                    day_map[day_key] = {
                        'before_sec': 0.0, 'after_sec': 0.0, 
                        'km_before': 0.0, 'km_after': 0.0, 'km_out_of_range': 0.0,
                        'trip_count': 0, 'attente_count': 0, 'dur_course': 0.0, 'dur_attente': 0.0, 'dur_arret': 0.0
                    }
                
                if is_working:
                    day_map[day_key]['before_sec'] += sec_before
                    day_map[day_key]['after_sec'] += sec_after
                
                day_map[day_key]['km_before'] += km_b
                day_map[day_key]['km_after'] += km_a
                
                # Categorize KM for this row
                is_valid_work = (activity_type == 'course' and v_movement == 'Move' and v_min <= km <= v_max)
                
                if not is_valid_work:
                    day_map[day_key]['km_out_of_range'] += km
                
                if activity_type == 'course':
                    day_map[day_key]['trip_count'] += 1
                    day_map[day_key]['dur_course'] += total_dur_hours
                elif activity_type == 'attente':
                    day_map[day_key]['attente_count'] += 1
                    day_map[day_key]['dur_attente'] += total_dur_hours
                elif activity_type == 'arret':
                    day_map[day_key]['dur_arret'] += total_dur_hours
        
        results.append({
            'vehicle': vehicle,
            'time_before_hhmm': seconds_to_hhmm(total_before_sec),
            'time_after_hhmm': seconds_to_hhmm(total_after_sec),
            'time_before_seconds': int(round(total_before_sec)),
            'time_after_seconds': int(round(total_after_sec)),
            'km_before': round(km_before, 3),
            'km_after': round(km_after, 3),
            'km_in_range': round(km_in_range, 3),  # KM of valid trips
            'km_out_of_range': round(km_out_of_range, 3),  # KM outside range
            'day_map': day_map,
            # Aggregated totals for the whole file/vehicle
            'trip_count': trip_count,
            'attente_count': attente_count, # NEW: Count of waiting events
            'total_duration_course': total_duration_course,
            'total_duration_attente': total_duration_attente,
            'total_duration_arret': total_duration_arret
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
                        'time_before_hhmm': seconds_to_hhmm(metrics['before_sec']),
                        'time_after_hhmm': seconds_to_hhmm(metrics['after_sec']),
                        'km_before': round(metrics['km_before'], 3),
                        'km_after': round(metrics['km_after'], 3),
                        'trip_count': metrics.get('trip_count', 0),
                        'duration_course_h': round(metrics.get('dur_course', 0.0) / 3600, 2),
                        'duration_attente_h': round(metrics.get('dur_attente', 0.0) / 3600, 2),
                        'duration_arret_h': round(metrics.get('dur_arret', 0.0) / 3600, 2)
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
                    month_map[month_key]['km_before'] += metrics['km_before']
                    month_map[month_key]['km_after'] += metrics['km_after']
                    # Accumulate new metrics
                    month_map[month_key].setdefault('trip_count', 0)
                    month_map[month_key]['trip_count'] += metrics.get('trip_count', 0)
                    month_map[month_key].setdefault('dur_course', 0.0)
                    month_map[month_key]['dur_course'] += metrics.get('dur_course', 0.0)
                    month_map[month_key].setdefault('dur_attente', 0.0)
                    month_map[month_key]['dur_attente'] += metrics.get('dur_attente', 0.0)
                    month_map[month_key].setdefault('dur_arret', 0.0)
                    month_map[month_key]['dur_arret'] += metrics.get('dur_arret', 0.0)
            for (year, month), metrics in month_map.items():
                all_monthly.append({
                    'year_month': f"{year:04d}-{month:02d}",
                    'vehicle': vehicle,
                    'hours_before_20h': round(metrics['before_sec'] / 3600, 2),
                    'hours_after_20h': round(metrics['after_sec'] / 3600, 2),
                    'time_before_hhmm': seconds_to_hhmm(metrics['before_sec']),
                    'time_after_hhmm': seconds_to_hhmm(metrics['after_sec']),
                    'km_before': round(metrics['km_before'], 3),
                    'time_before_hhmm': seconds_to_hhmm(metrics['before_sec']),
                    'time_after_hhmm': seconds_to_hhmm(metrics['after_sec']),
                    'km_before': round(metrics['km_before'], 3),
                    'km_after': round(metrics['km_after'], 3),
                    'trip_count': metrics.get('trip_count', 0),
                    'duration_course_h': round(metrics.get('dur_course', 0.0) / 3600, 2),
                    'duration_attente_h': round(metrics.get('dur_attente', 0.0) / 3600, 2),
                    'duration_arret_h': round(metrics.get('dur_arret', 0.0) / 3600, 2)
                })
        report_df = pd.DataFrame(all_monthly)
        out = outdir / f"report_monthly_{infile.stem}.{out_format}"

    if out_format == 'csv':
        report_df.to_csv(out, index=False)
    else:
        report_df.to_excel(out, index=False)
    print(f"Wrote {out}")
    return report_df
