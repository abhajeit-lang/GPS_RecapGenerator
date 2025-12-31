from app import app, db
from models import VehicleActivity
from report_logic import load_file, process_dataframe
from datetime import datetime

app.app_context().push()

# Load sample data
df = load_file('sample.csv')
processed = process_dataframe(df, include_date=True)

count = 0
for _, row in processed.iterrows():
    vehicle = row['vehicle']
    day_map = row['day_map']
    if day_map:
        for (year, month, day), metrics in day_map.items():
            activity = VehicleActivity(
                date=datetime(year, month, day).date(),
                vehicle_code=vehicle,
                hours_before_20h=metrics['before_sec'] / 3600,
                hours_after_20h=metrics['after_sec'] / 3600,
                km_before=metrics['km_before'],
                km_after=metrics['km_after']
            )
            db.session.add(activity)
            count += 1

db.session.commit()
print(f'✓ Stored {count} records in database')

# Show available dates
dates = db.session.query(VehicleActivity.date).distinct().order_by(VehicleActivity.date.desc()).limit(5).all()
print(f'Available dates: {[d[0] for d in dates]}')
