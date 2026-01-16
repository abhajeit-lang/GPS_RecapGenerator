import sqlite3

def check_c130_daily_activity():
    conn = sqlite3.connect('instance/gps_reports.db')
    cursor = conn.cursor()
    
    query = """
    SELECT date, trip_count, duration_course 
    FROM vehicle_activity 
    WHERE vehicle_code = 'C130' 
    ORDER BY date
    """
    
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()
    
    print("Daily Activity for C130:")
    total = 0
    for row in rows:
        print(f"Date: {row[0]}, Trips: {row[1]}, Duration: {row[2]:.2f}s")
        total += row[1]
    
    print(f"Total Trips in DB: {total}")

if __name__ == "__main__":
    check_c130_daily_activity()
