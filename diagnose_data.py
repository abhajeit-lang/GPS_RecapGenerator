from app import app
from models import db, VehicleActivity, Vehicle, Atelier, Project

with app.app_context():
    print("--- Available Dates in VehicleActivity ---")
    dates = db.session.query(VehicleActivity.date).distinct().order_by(VehicleActivity.date).all()
    for d in dates:
        print(d[0])
    
    print("\n--- Project -> Atelier -> Vehicle Hierarchy ---")
    projects = Project.query.all()
    for p in projects:
        print(f"Project: {p.name}")
        for a in p.ateliers:
            print(f"  Atelier: {a.name} (ID: {a.id})")
            for v in a.vehicles:
                print(f"    Vehicle: {v.id}")
                # Check for activities for this vehicle
                act_count = VehicleActivity.query.filter_by(vehicle_code=v.id).count()
                print(f"      Activities: {act_count}")
