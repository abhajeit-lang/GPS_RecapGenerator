import sqlite3

def investigate_c130():
    conn = sqlite3.connect('instance/gps_reports.db')
    cursor = conn.cursor()
    
    # 1. Get C130's atelier config
    print("=== C130 Configuration ===")
    cursor.execute("""
        SELECT v.id, v.name, v.movement_type, a.name, a.min_trip_km, a.max_trip_km
        FROM vehicle v
        LEFT JOIN atelier a ON v.atelier_id = a.id
        WHERE v.id = 'C130'
    """)
    config = cursor.fetchone()
    if config:
        print(f"Vehicle: {config[0]} ({config[1]})")
        print(f"Movement Type: {config[2]}")
        print(f"Atelier: {config[3]}")
        print(f"Min KM: {config[4]}")
        print(f"Max KM: {config[5]}")
    
    # 2. Get all activity records for C130 on Jan 2, 2026
    print("\n=== Activity Records for C130 ===")
    cursor.execute("""
        SELECT date, trip_count, km_before, km_after, duration_course
        FROM vehicle_activity
        WHERE vehicle_code = 'C130'
        ORDER BY date
    """)
    rows = cursor.fetchall()
    total_trips = 0
    for row in rows:
        print(f"Date: {row[0]}, Raw Trips (legs): {row[1]}, KM: {row[2]+row[3]:.2f}")
        total_trips += row[1]
    
    print(f"\nTotal Raw Trips (all dates): {total_trips}")
    print(f"Total Cycles (trips/2): {total_trips / 2}")
    
    conn.close()

if __name__ == "__main__":
    investigate_c130()
