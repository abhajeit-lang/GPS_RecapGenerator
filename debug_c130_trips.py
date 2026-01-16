import sqlite3
from datetime import datetime

def analyze_c130_trips():
    conn = sqlite3.connect('instance/gps_reports.db')
    cursor = conn.cursor()
    
    # Get all Course activities for C130
    # Note: we need to recreate the filtering logic:
    # 1. Activity = Course (or similar)
    # 2. Min/Max KM (0.5 to 5.0 for At1)
    
    query = """
    SELECT date, start_time, duration_course, km_before, km_after
    FROM vehicle_activity
    WHERE vehicle_code = 'C130'
    ORDER BY date, start_time
    """
    
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()
    
    print(f"Total Raw Activity Rows for C130: {len(rows)}")
    
    # We need to simulate the parsing logic because raw rows might not be 1-to-1 with "Courses" 
    # if the DB stores aggregated daily headers vs granular lines.
    # WAIT - VehicleActivity stores AGGREGATED metrics per day/vehicle usually?
    # Let's check the model definition again.
    
    # Actually, report_logic.py processes the DATAFRAME and returns a summary.
    # The database stores `VehicleActivity` which seems to be daily summaries if I recall correctly.
    # Let's verify the model.

if __name__ == "__main__":
    analyze_c130_trips()
