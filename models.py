from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Project(db.Model):
    __tablename__ = 'project'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=True)
    province = db.Column(db.String(100), nullable=True)
    ateliers = db.relationship('Atelier', backref='project', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'province': self.province,
            'ateliers': [a.to_dict() for a in self.ateliers]
        }

class Atelier(db.Model):
    __tablename__ = 'atelier'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    min_trip_km = db.Column(db.Float, default=0.5)
    max_trip_km = db.Column(db.Float, default=15.0)
    vehicles = db.relationship('Vehicle', backref='atelier', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'project_id': self.project_id,
            'min_trip_km': self.min_trip_km,
            'max_trip_km': self.max_trip_km,
            'vehicle_count': len(self.vehicles),
            'vehicles': [{'id': v.id, 'name': v.name, 'matricule': v.matricule} for v in self.vehicles]
        }

class Vehicle(db.Model):
    __tablename__ = 'vehicle'
    
    id = db.Column(db.String(50), primary_key=True)  # Vehicle code (e.g., C024)
    matricule = db.Column(db.String(100), nullable=False)  # Registration plate
    name = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    atelier_id = db.Column(db.Integer, db.ForeignKey('atelier.id'), nullable=True)
    movement_type = db.Column(db.String(20), default='Move')  # 'Move' or 'Sur place'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Vehicle {self.id} {self.matricule} {self.name} ({self.category})>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'matricule': self.matricule,
            'name': self.name,
            'category': self.category,
            'movement_type': self.movement_type or 'Move',
            'atelier_id': self.atelier_id,
            'atelier_name': self.atelier.name if self.atelier else None,
            'project_name': self.atelier.project.name if self.atelier and self.atelier.project else None
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
    
    # New metrics
    trip_count = db.Column(db.Integer, default=0)
    duration_course = db.Column(db.Float, default=0.0)  # Seconds
    duration_attente = db.Column(db.Float, default=0.0) # Seconds
    duration_arret = db.Column(db.Float, default=0.0)   # Seconds
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
