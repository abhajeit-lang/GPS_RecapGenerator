import sqlite3

def check_upload_times():
    conn = sqlite3.connect('instance/gps_reports.db')
    cursor = conn.cursor()
    
    # Check upload timestamps for C130
    print("=== C130 Upload Times ===")
    cursor.execute("""
        SELECT date, trip_count, uploaded_at
        FROM vehicle_activity
        WHERE vehicle_code = 'C130'
        ORDER BY date
    """)
    for row in cursor.fetchall():
        print(f"Date: {row[0]}, Trips: {row[1]}, Uploaded At: {row[2]}")
    
    conn.close()

if __name__ == "__main__":
    check_upload_times()
