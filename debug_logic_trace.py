import pandas as pd
from report_logic import process_dataframe

def debug_trace():
    print("=== DEBUGGING LOGIC TRACE FOR C105 ===")
    
    # Mock data representing C105 (mixed short and long trips)
    # User said: 24 lines total. 12 valid (0.7-5km), others invalid/short?
    # Or maybe filter fail?
    data = {
        'Code': ['C105'] * 6,
        'Date': ['02/01/2026'] * 6,
        'Heure de départ': [
            '08:00', '08:30', # Trip 1 (Normal ~2.2km)
            '09:00', '09:30', # Trip 2 (Short ~0.1km - should be filtered out by min 0.7)
            '10:00', '10:30'  # Trip 3 (Half ~1.1km - should be kept but 0.5 cycle)
        ],
        'Heure d\'arrêt': [
            '08:20', '08:50',
            '09:10', '09:40',
            '10:15', '10:45'
        ],
        'Durée': ['00:20:00'] * 6,
        'CAA': ['Course'] * 6,
        'KM': [
            2.2, 2.2,  # Normal
            0.1, 0.1,  # Tiny (should filter out)
            1.2, 1.2   # Half (should be 0.5 cycle)
        ]
    }
    df = pd.DataFrame(data)
    
    # 1. Test with CLEAN keys
    print("\n--- TEST 1: Clean Config Keys ---")
    configs = {
        'C105': {'min_trip_km': 0.7, 'max_trip_km': 5.0, 'movement_type': 'Move'}
    }
    
    # We need to monkeypath process_dataframe or copy relevant parts? 
    # Better to just call it and see output, but we need internal prints.
    # I will modify report_logic.py to ADD print statements for C105.
    
    process_dataframe(df, include_date=True, vehicle_configs=configs)
    
    # 2. Test with DIRTY keys (simulating DB issue)
    print("\n--- TEST 2: Dirty Config Keys (Space at end) ---")
    configs_dirty = {
        'C105 ': {'min_trip_km': 0.7, 'max_trip_km': 5.0, 'movement_type': 'Move'}
    }
    process_dataframe(df, include_date=True, vehicle_configs=configs_dirty)

if __name__ == "__main__":
    debug_trace()
