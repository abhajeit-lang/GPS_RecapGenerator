"""
Database initialization script.
Run this to create all tables with the correct schema.
"""
from app import app, db

with app.app_context():
    # Drop all tables and recreate them
    db.drop_all()
    db.create_all()
    print("Database created successfully with all tables!")
    print("Tables created:")
    print("  - project (id, name, description, province)")
    print("  - atelier (id, name, project_id)")
    print("  - vehicle (id, matricule, name, category, atelier_id)")
    print("  - vehicle_activity (date, vehicle_code, hours, km, trips, durations)")
