from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Vehicle(db.Model):
    __tablename__ = 'vehicle'
    
    id = db.Column(db.String(50), primary_key=True)  # Vehicle code (e.g., C024)
    matricule = db.Column(db.String(100), nullable=False)  # Registration plate
    name = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Vehicle {self.id} {self.matricule} {self.name} ({self.category})>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'matricule': self.matricule,
            'name': self.name,
            'category': self.category
        }

class VehicleActivity(db.Model):
    __tablename__ = 'vehicle_activity'
    
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    vehicle_code = db.Column(db.String(50), nullable=False, index=True)
    hours_before_20h = db.Column(db.Float, default=0.0)
    hours_after_20h = db.Column(db.Float, default=0.0)
    km_before = db.Column(db.Float, default=0.0)
    km_after = db.Column(db.Float, default=0.0)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<VehicleActivity {self.date} {self.vehicle_code}>'
    
    def to_dict(self):
        return {
            'date': self.date.isoformat(),
            'vehicle': self.vehicle_code,
            'hours_before_20h': round(self.hours_before_20h, 2),
            'hours_after_20h': round(self.hours_after_20h, 2),
            'km_before': round(self.km_before, 3),
            'km_after': round(self.km_after, 3)
        }
