#!/usr/bin/env python3
"""
Verification script to debug hour and KM calculations.
This script will show detailed breakdown of how times and KM are split at 20:00.
"""

from datetime import datetime, timedelta
from report_logic import split_interval_at_20, format_decimal_hours

def verify_split(start_str, end_str, km=0):
    """Verify the split of an interval at 20:00."""
    # Parse input
    start = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
    
    print(f"\n{'='*70}")
    print(f"Interval: {start} → {end}")
    print(f"Total Duration: {end - start}")
    print(f"Total KM: {km}")
    print(f"{'='*70}")
    
    # Get split
    sec_before, sec_after = split_interval_at_20(start, end)
    total_sec = (end - start).total_seconds()
    
    # Convert to hours
    hours_before = sec_before / 3600
    hours_after = sec_after / 3600
    
    # Allocate KM proportionally
    if total_sec > 0:
        km_before = km * (sec_before / total_sec)
        km_after = km * (sec_after / total_sec)
    else:
        km_before = km_after = 0
    
    print(f"\n✓ BEFORE 20:00")
    print(f"  Seconds: {sec_before:.0f}s")
    print(f"  Hours (decimal): {hours_before:.4f}h = {format_decimal_hours(hours_before)}")
    print(f"  Hours (2 decimal): {hours_before:.2f}h")
    print(f"  KM: {km_before:.2f} km")
    
    print(f"\n✓ AFTER 20:00")
    print(f"  Seconds: {sec_after:.0f}s")
    print(f"  Hours (decimal): {hours_after:.4f}h = {format_decimal_hours(hours_after)}")
    print(f"  Hours (2 decimal): {hours_after:.2f}h")
    print(f"  KM: {km_after:.2f} km")
    
    print(f"\n✓ TOTALS")
    print(f"  Total Hours: {hours_before + hours_after:.2f}h")
    print(f"  Total KM: {km_before + km_after:.2f} km")
    print(f"  Verification: {total_sec:.0f}s = {total_sec/3600:.4f}h")
    
    return {
        'hours_before': hours_before,
        'hours_after': hours_after,
        'km_before': km_before,
        'km_after': km_after,
        'hours_before_formatted': format_decimal_hours(hours_before),
        'hours_after_formatted': format_decimal_hours(hours_after)
    }


if __name__ == '__main__':
    print("\n" + "="*70)
    print("GPS RECAP - CALCULATION VERIFICATION TOOL")
    print("="*70)
    
    # Test cases from your screenshot
    print("\n[TEST 1] Single activity in morning (before 20:00)")
    verify_split("2025-12-25 08:00:00", "2025-12-25 10:32:00", km=2.53)
    
    print("\n[TEST 2] Activity crossing 20:00")
    verify_split("2025-12-25 19:00:00", "2025-12-25 21:00:00", km=10)
    
    print("\n[TEST 3] Evening activity (after 20:00)")
    verify_split("2025-12-25 20:30:00", "2025-12-25 22:00:00", km=5)
    
    print("\n[TEST 4] Multi-day activity")
    verify_split("2025-12-25 18:00:00", "2025-12-26 10:00:00", km=50)
    
    print("\n[TEST 5] Your screenshot data - ISUZU DC (2.53 hours shown)")
    # If 2.53 hours before 20:00 and 0.00 after with 2.53 KM
    verify_split("2025-12-25 08:00:00", "2025-12-25 10:32:00", km=2.53)
    
    print("\n" + "="*70)
    print("To use this script with your data:")
    print("1. Export a report from your system")
    print("2. Identify a specific row with hours and KM")
    print("3. Replace the test cases above with your actual start/end times")
    print("4. Run: python verify_calculations.py")
    print("="*70 + "\n")
