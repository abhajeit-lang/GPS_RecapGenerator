import sqlite3

def clear_all_activity_data():
    """Clear all vehicle activity records so user can re-upload with correct logic."""
    conn = sqlite3.connect('instance/gps_reports.db')
    cursor = conn.cursor()
    
    # Count before deletion
    cursor.execute("SELECT COUNT(*) FROM vehicle_activity")
    count = cursor.fetchone()[0]
    print(f"Found {count} activity records in database.")
    
    if count > 0:
        confirm = input("Delete all activity data? This cannot be undone. (yes/no): ")
        if confirm.lower() == 'yes':
            cursor.execute("DELETE FROM vehicle_activity")
            conn.commit()
            print(f"Deleted {count} records. You can now re-upload your CSV files.")
        else:
            print("Cancelled. No data was deleted.")
    else:
        print("No activity data to delete.")
    
    conn.close()

if __name__ == "__main__":
    clear_all_activity_data()
