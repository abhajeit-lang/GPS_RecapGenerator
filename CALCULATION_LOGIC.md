# GPS RECAP - CALCULATION LOGIC EXPLANATION

## Overview
The application splits vehicle activity times and distances at the 20:00 (8 PM) threshold.

---

## TIME SPLIT LOGIC (Hours)

### Principle
For each activity record with `start_time` and `end_time`:
1. Calculate total duration: `end_time - start_time`
2. Split the duration at the 20:00 boundary
3. Allocate hours accordingly

### Examples

#### Example 1: Activity entirely BEFORE 20:00
```
Start: 2025-12-25 08:00:00
End:   2025-12-25 10:32:00
Duration: 2 hours 32 minutes = 2.5333 hours

Before 20:00: 2h32min = 2.5333 hours
After 20:00:  0h0min  = 0.0000 hours

Result in Report:
- Hours Before: 2.53h (rounded to 2 decimals)
- Hours After:  0.00h
```

#### Example 2: Activity crossing 20:00
```
Start: 2025-12-25 19:00:00
End:   2025-12-25 21:30:00
Duration: 2 hours 30 minutes = 2.5 hours

Before 20:00 (19:00 to 20:00): 1 hour    = 1.0000 hours
After 20:00  (20:00 to 21:30): 1h30min  = 1.5000 hours

Result in Report:
- Hours Before: 1.00h
- Hours After:  1.50h
```

#### Example 3: Activity entirely AFTER 20:00
```
Start: 2025-12-25 21:00:00
End:   2025-12-25 22:30:00
Duration: 1 hour 30 minutes = 1.5 hours

Before 20:00: 0h0min  = 0.0000 hours
After 20:00:  1h30min = 1.5000 hours

Result in Report:
- Hours Before: 0.00h
- Hours After:  1.50h
```

---

## KM SPLIT LOGIC (Kilometers)

### Principle
The total KM for an activity is split **proportionally** based on time spent before/after 20:00.

### Formula
```
KM_before = Total_KM × (Seconds_before / Total_Seconds)
KM_after  = Total_KM × (Seconds_after / Total_Seconds)
```

### Examples

#### Example 1: All KM before 20:00
```
Start:     2025-12-25 08:00:00
End:       2025-12-25 10:32:00  (2h32min before 20:00)
Total KM:  2.53 km

KM_before = 2.53 × (2h32min / 2h32min) = 2.53 × 1.0 = 2.53 km
KM_after  = 2.53 × (0h0min  / 2h32min) = 2.53 × 0.0 = 0.00 km

Result in Report:
- KM Before: 2.53 km
- KM After:  0.00 km
```

#### Example 2: KM split across 20:00
```
Start:     2025-12-25 19:00:00
End:       2025-12-25 21:00:00  (Total 2 hours)
Total KM:  10 km

Time before 20:00: 1 hour
Time after 20:00:  1 hour

KM_before = 10 × (1 hour / 2 hours) = 10 × 0.5 = 5.00 km
KM_after  = 10 × (1 hour / 2 hours) = 10 × 0.5 = 5.00 km

Result in Report:
- KM Before: 5.00 km
- KM After:  5.00 km
```

#### Example 3: 75% before, 25% after 20:00
```
Start:     2025-12-25 18:00:00
End:       2025-12-25 21:00:00  (Total 3 hours)
Total KM:  10 km

Time before 20:00: 2 hours (18:00 to 20:00)
Time after 20:00:  1 hour  (20:00 to 21:00)

KM_before = 10 × (2 hours / 3 hours) = 10 × 0.6667 = 6.67 km
KM_after  = 10 × (1 hour / 3 hours)  = 10 × 0.3333 = 3.33 km

Result in Report:
- KM Before: 6.67 km
- KM After:  3.33 km
```

---

## MULTI-DAY ACTIVITIES

For activities that span multiple days (start one day, end the next):

### Example: Night activity
```
Start: 2025-12-25 22:00:00 (Dec 25)
End:   2025-12-26 08:00:00 (Dec 26)

Day 1 Split:
- 22:00 to 23:59 (after 20:00): 2 hours = 2h

Day 2 Split:  
- 00:00 to 08:00 (before 20:00): 8 hours = 8h

Total:
- Hours Before 20:00: 8 hours
- Hours After 20:00:  2 hours

KM Allocation: Proportional to duration
- If Total KM = 50 km
- KM After (Day 1):  50 × (2/10) = 10 km
- KM Before (Day 2): 50 × (8/10) = 40 km
```

---

## HOW TO VERIFY YOUR DATA

### Step 1: Get your start and end times
From your CSV file:
- `Heure de départ` = Start time (full datetime)
- `Heure d'arrêt` = Stop time (time only, assumes same day unless after crosses midnight)

### Step 2: Calculate manually
```
Duration = End Time - Start Time
Hours = Duration / 3600 seconds
```

### Step 3: Use the verification script
```bash
python verify_calculations.py
```

Edit the test cases to match your actual data.

### Step 4: Check database
```bash
python inspect_db.py
python inspect_db.py 2025-12-25              # Specific date
python inspect_db.py 2025-12-25 PK05         # Specific vehicle on date
```

---

## COMMON ISSUES

### Issue 1: "Report shows 1.33h but I calculated 1h20min"
**Solution**: 1.33 hours = 1 hour + 0.33×60 minutes = 1h20min ✓
The system shows decimal hours (1.33) but can also display as "1h20min"

### Issue 2: "My total hours don't match"
**Check**:
1. Did you include only "Course" activities? (CAA column)
2. Are your start/stop times correct?
3. Did you account for KM being proportional to time?

### Issue 3: "KM doesn't match my calculation"
**Remember**: KM is split proportionally by time
- If you travel 10 km in 2 hours, and 1.5h was before 20:00
- Then KM_before = 10 × (1.5/2) = 7.5 km

---

## REFERENCE TIME

The split point is set to: **20:00 (8:00 PM)**

This is hardcoded in `report_logic.py`:
```python
REF_HOUR = 20
```

To change this, modify this value and restart the application.

---

## ROUNDING

- **Hours**: Stored as decimal to 4 places, displayed as 2 decimal places
- **KM**: Stored and displayed to 2-3 decimal places

This can cause small differences (±0.01) due to rounding.

---

## DEBUGGING STEPS

If numbers don't match, follow this process:

1. **Identify the specific record**
   - Date
   - Vehicle code
   - Start time
   - End time
   - Total KM

2. **Run verification script**
   ```bash
   python verify_calculations.py
   ```
   Replace test data with your exact times

3. **Check database**
   ```bash
   python inspect_db.py <DATE> <VEHICLE>
   ```

4. **Compare results**
   - Manual calculation
   - Script output
   - Database values
   - Report display

5. **Report any discrepancies**
   - Include exact times from CSV
   - Include reported values
   - Include expected values

---

## CONTACT

For questions about calculations, provide:
- Exact start time from CSV
- Exact end time from CSV
- Total KM from CSV
- What you expected
- What the report shows
