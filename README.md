Vehicle Activity Report Generator

Usage:

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Run daily report:

```bash
python run_report.py "C:/path/to/Raport.csv" --output-dir out --period daily --format csv
```

Notes:
- The script treats each row as a status entry; when `CAA` == `Course`, it treats the time until the next record for the same vehicle as working time.
- If a working interval spans 20:00, time and KM are split proportionally across the before/after 20:00 buckets.
- Rows missing a next timestamp are ignored for duration calculation.
