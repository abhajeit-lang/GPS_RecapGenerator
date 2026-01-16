import sqlite3

def migrate():
    conn = sqlite3.connect('instance/gps_reports.db')
    cursor = conn.cursor()
    
    try:
        print("Adding attente_count column to vehicle_activity...")
        cursor.execute("ALTER TABLE vehicle_activity ADD COLUMN attente_count INTEGER DEFAULT 0")
        print("Success!")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("Column attente_count already exists.")
        else:
            print(f"Error: {e}")
            
    conn.commit()
    conn.close()

if __name__ == "__main__":
    migrate()
