from app import app, db
from models import Vehicle, VehicleActivity

with app.app_context():
    # Drop all tables
    db.drop_all()
    print("✓ Dropped all tables")
    
    # Create all tables from scratch
    db.create_all()
    print("✓ Created all tables")
    
    # Verify columns
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    
    # Check Vehicle table
    columns = [c['name'] for c in inspector.get_columns('vehicle')]
    print(f"Vehicle columns: {columns}")
    
    if 'matricule' in columns:
        print("✓ SUCCESS: matricule column created!")
    else:
        print("✗ ERROR: matricule column NOT created")
