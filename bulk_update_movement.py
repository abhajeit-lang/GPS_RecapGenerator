import sqlite3

def bulk_update_movement():
    db_path = 'instance/gps_reports.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    categories = [
        'PELLE', 
        'TELESCOPIQUE', 
        'NIVELEUSE', 
        'COMPACTEUR', 
        'CHARGEUR', 
        'BULLDOZER', 
        'TRACTOPELLE'
    ]
    
    # Create placeholders for the IN clause
    placeholders = ', '.join(['?'] * len(categories))
    query = f"UPDATE vehicle SET movement_type = 'Sur place' WHERE category IN ({placeholders})"
    
    try:
        print(f"Updating vehicles with categories: {categories}")
        cursor.execute(query, categories)
        rows_updated = cursor.rowcount
        conn.commit()
        print(f"Success! Updated {rows_updated} vehicles.")
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    bulk_update_movement()
