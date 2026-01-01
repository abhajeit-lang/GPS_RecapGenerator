#!/usr/bin/env python3
"""
Script to identify and clear duplicate records from the database.
Useful when the same CSV file has been uploaded multiple times.
"""

from models import db, VehicleActivity, Vehicle
from app import app
from datetime import datetime
from collections import defaultdict

def find_duplicates():
    """Find duplicate records (same date + vehicle_code)."""
    with app.app_context():
        # Group records by date and vehicle_code
        records = VehicleActivity.query.all()
        
        grouped = defaultdict(list)
        for record in records:
            key = (record.date, record.vehicle_code)
            grouped[key].append(record)
        
        duplicates = {k: v for k, v in grouped.items() if len(v) > 1}
        
        if not duplicates:
            print("✓ No duplicate records found!")
            return {}
        
        print(f"\n⚠️  Found {len(duplicates)} duplicate(s):\n")
        for (date, vehicle), records in sorted(duplicates.items()):
            print(f"  Date: {date} | Vehicle: {vehicle}")
            print(f"    Records: {len(records)}")
            for i, rec in enumerate(records, 1):
                print(f"      [{i}] ID={rec.id} | Hours: {rec.hours_before_20h:.2f}h/{rec.hours_after_20h:.2f}h | KM: {rec.km_before:.2f}/{rec.km_after:.2f} | Uploaded: {rec.uploaded_at}")
            print()
        
        return duplicates

def clear_duplicates(keep_latest=True):
    """
    Remove duplicate records, keeping only the latest upload.
    
    Args:
        keep_latest: If True, keep the most recently uploaded record
    """
    with app.app_context():
        duplicates = find_duplicates()
        
        if not duplicates:
            return
        
        total_deleted = 0
        
        for (date, vehicle), records in duplicates.items():
            if keep_latest:
                # Sort by uploaded_at, keep the latest
                sorted_records = sorted(records, key=lambda r: r.uploaded_at, reverse=True)
                to_delete = sorted_records[1:]  # Delete all but the first (latest)
            else:
                # Delete all duplicates
                to_delete = records
            
            for record in to_delete:
                print(f"  🗑️  Deleting: {date} | {vehicle} | Uploaded: {record.uploaded_at}")
                db.session.delete(record)
                total_deleted += 1
        
        db.session.commit()
        print(f"\n✓ Deleted {total_deleted} duplicate record(s)")
        print("✓ Database cleaned!")

if __name__ == '__main__':
    import sys
    
    print("="*70)
    print("DATABASE DUPLICATE CHECKER & CLEANER")
    print("="*70)
    
    if len(sys.argv) > 1 and sys.argv[1] == '--clear':
        print("\n⚠️  CLEARING DUPLICATES (keeping latest uploads)...\n")
        clear_duplicates(keep_latest=True)
    else:
        print("\nScanning for duplicates...\n")
        find_duplicates()
        print("\nTo CLEAR duplicates, run:")
        print("  python clear_duplicates.py --clear")
        print("\nThis will DELETE old duplicate records, keeping only the latest upload.")
