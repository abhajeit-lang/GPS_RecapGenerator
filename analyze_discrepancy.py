#!/usr/bin/env python3
"""
Calculate theoretical values and compare with database to find discrepancies.
"""

import sys
sys.path.insert(0, '.')

from models import db, VehicleActivity
from app import app

def analyze_pk23_2025_12_25():
    """Detailed analysis of PK23 on 2025-12-25."""
    
    with app.app_context():
        record = VehicleActivity.query.filter_by(
            date='2025-12-25',
            vehicle_code='PK23'
        ).first()
        
        if not record:
            print("No record found for PK23 on 2025-12-25")
            return
        
        print("="*70)
        print("DETAILED ANALYSIS: PK23 on 2025-12-25")
        print("="*70)
        print()
        print("Database Record:")
        print(f"  Hours Before 20:00: {record.hours_before_20h:.4f} h = {record.hours_before_20h*60:.1f} minutes")
        print(f"  Hours After 20:00:  {record.hours_after_20h:.4f} h = {record.hours_after_20h*60:.1f} minutes")
        print(f"  Total Hours:        {(record.hours_before_20h + record.hours_after_20h):.4f} h")
        print()
        print(f"  KM Before 20:00:    {record.km_before:.2f} km")
        print(f"  KM After 20:00:     {record.km_after:.2f} km")
        print(f"  Total KM:           {(record.km_before + record.km_after):.2f} km")
        print()
        print(f"  Uploaded: {record.uploaded_at}")
        print()
        print("="*70)
        print("QUESTION: Where does the 15.55 km after 20:00 come from?")
        print("="*70)
        print()
        print("In your screenshot, you counted rows AFTER 20:08 (starting at row 3622)")
        print("But your sum was only 13.0 km.")
        print()
        print("Possible Reasons:")
        print("1. Your screenshot doesn't show ALL rows for that day")
        print("2. Some rows are hidden/filtered in the spreadsheet")
        print("3. The CSV file is different from what you're viewing")
        print("4. There are multiple uploads and the data has accumulated")
        print()
        print("SOLUTION:")
        print("1. Open the ORIGINAL CSV file you're viewing")
        print("2. Filter to show ONLY 'Course' activities")
        print("3. Sum all KM values AFTER 20:00 for PK23 on 2025-12-25")
        print("4. Compare with the database value (15.55 km)")
        print()

if __name__ == '__main__':
    analyze_pk23_2025_12_25()
