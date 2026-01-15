from app import app
from datetime import date
import json
from advanced_reports import generate_comparative_report

with app.app_context():
    atelier_ids = [1]
    start_date = date(2026, 1, 12)
    end_date = date(2026, 1, 12)
    
    results = generate_comparative_report(atelier_ids, start_date, end_date)
    print(f"Results: {json.dumps(results, indent=2)}")
