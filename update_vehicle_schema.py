import sqlite3

def add_movement_type_column():
    db_path = 'instance/gps_reports.db'
    print(f"Connecting to {db_path}...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if column exists
        cursor.execute("PRAGMA table_info(vehicle)")
        columns = [info[1] for info in cursor.fetchall()]
        
        if 'movement_type' not in columns:
            print("Adding movement_type column...")
            cursor.execute("ALTER TABLE vehicle ADD COLUMN movement_type TEXT DEFAULT 'Move'")
            conn.commit()
            print("Database schema updated: movement_type added.")
        else:
            print("Column movement_type already exists.")
            
    except Exception as e:
        print(f"Error updating schema: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    add_movement_type_column()
