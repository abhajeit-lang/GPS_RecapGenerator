import sqlite3
import pandas as pd
from report_logic import process_dataframe

def diagnose():
    conn = sqlite3.connect('instance/gps_reports.db')
    cursor = conn.cursor()
    
    print("=== Vehicle Configuration ===")
    cursor.execute("""
        SELECT v.id, v.name, v.movement_type, a.name, a.min_trip_km, a.max_trip_km
        FROM vehicle v
        LEFT JOIN atelier a ON v.atelier_id = a.id
        WHERE v.id IN ('C105', 'C130')
    """)
    for row in cursor.fetchall():
        print(f"ID: '{row[0]}', Name: {row[1]}, Movement: {row[2]}")
        print(f"   Atelier: {row[3]}, Range: {row[4]} - {row[5]} km")
        
    print("\n=== Current Activity Data (First 5 rows for C105) ===")
    cursor.execute("""
        SELECT date, trip_count, duration_course, km_before, km_after
        FROM vehicle_activity
        WHERE vehicle_code = 'C105'
        ORDER BY date DESC
        LIMIT 5
    """)
    rows = cursor.fetchall()
    if not rows:
        print("No activity data found for C105")
    for row in rows:
        print(f"Date: {row[0]}, Trips: {row[1]}, Duration: {row[2]}, KM: {row[3]+row[4]:.2f}")

    conn.close()

if __name__ == "__main__":
    diagnose()
