#!/usr/bin/env python3
"""
Database inspection script to view raw VehicleActivity records and verify calculations.
"""

from models import db, VehicleActivity, Vehicle
from app import app
from datetime import datetime

def inspect_database(date_str=None, vehicle_id=None):
    """Inspect and display database records with calculation verification."""
    
    with app.app_context():
        print("\n" + "="*100)
        print("DATABASE RECORDS INSPECTION")
        print("="*100)
        
        # Build query
        query = VehicleActivity.query
        
        if date_str:
            query = query.filter(VehicleActivity.date == date_str)
            print(f"Filter: Date = {date_str}")
        
        if vehicle_id:
            query = query.filter(VehicleActivity.vehicle_code == vehicle_id)
            print(f"Filter: Vehicle = {vehicle_id}")
        
        records = query.order_by(VehicleActivity.date, VehicleActivity.vehicle_code).all()
        
        if not records:
            print("No records found!")
            return
        
        print(f"Found {len(records)} record(s)\n")
        
        # Display each record
        for i, record in enumerate(records, 1):
            vehicle = Vehicle.query.filter_by(id=record.vehicle_code).first()
            vehicle_name = vehicle.name if vehicle else "UNKNOWN"
            
            print(f"[{i}] Date: {record.date} | Vehicle: {record.vehicle_code} ({vehicle_name})")
            print(f"    Hours Before 20:00:  {record.hours_before_20h:.4f}h = {record.hours_before_20h:.2f}h")
            print(f"    Hours After 20:00:   {record.hours_after_20h:.4f}h = {record.hours_after_20h:.2f}h")
            print(f"    KM Before 20:00:     {record.km_before:.2f} km")
            print(f"    KM After 20:00:      {record.km_after:.2f} km")
            print(f"    Uploaded: {record.uploaded_at}")
            print()
        
        # Summary
        print("="*100)
        print("SUMMARY")
        print("="*100)
        
        total_hours_before = sum(r.hours_before_20h for r in records)
        total_hours_after = sum(r.hours_after_20h for r in records)
        total_km_before = sum(r.km_before for r in records)
        total_km_after = sum(r.km_after for r in records)
        
        print(f"Total Hours Before 20:00: {total_hours_before:.2f}h")
        print(f"Total Hours After 20:00:  {total_hours_after:.2f}h")
        print(f"Total KM Before 20:00:    {total_km_before:.2f} km")
        print(f"Total KM After 20:00:     {total_km_after:.2f} km")
        print("="*100 + "\n")


if __name__ == '__main__':
    import sys
    
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    vehicle_arg = sys.argv[2] if len(sys.argv) > 2 else None
    
    inspect_database(date_str=date_arg, vehicle_id=vehicle_arg)
    
    print("\nUsage:")
    print("  python inspect_db.py                    # Show all records")
    print("  python inspect_db.py 2025-12-25         # Show records for specific date")
    print("  python inspect_db.py 2025-12-25 PK05    # Show records for date and vehicle")
    print()
