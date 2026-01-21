import os
from pathlib import Path
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename
from report_logic import load_file, process_dataframe, generate_reports, format_decimal_hours
from models import db, VehicleActivity, Vehicle, Project, Atelier
import tempfile
from datetime import datetime
import pandas as pd
from advanced_reports import generate_comparative_report, generate_atelier_performance_report, generate_project_overview
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
import io
import json

def load_settings():
    try:
        settings_path = Path(__file__).parent / 'settings.json'
        if settings_path.exists():
            with open(settings_path, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max upload
app.config['UPLOAD_FOLDER'] = Path(tempfile.gettempdir()) / 'gps_reports'
app.config['OUTPUT_FOLDER'] = Path(tempfile.gettempdir()) / 'gps_reports_output'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///gps_reports.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Create folders
app.config['UPLOAD_FOLDER'].mkdir(parents=True, exist_ok=True)
app.config['OUTPUT_FOLDER'].mkdir(parents=True, exist_ok=True)

db.init_app(app)

with app.app_context():
    db.create_all()

ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_file(Path(__file__).parent / 'static' / filename)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': f'Invalid file type. Allowed: {", ".join(ALLOWED_EXTENSIONS)}'}), 400
    
    try:
        # Save uploaded file
        filename = secure_filename(file.filename)
        filepath = Path(app.config['UPLOAD_FOLDER']) / filename
        file.save(str(filepath))
        
        # Load settings
        settings = load_settings()
        ref_hour = settings.get('split_hour', 20)
        ref_min = settings.get('split_min', 0)

        # Build vehicle configurations map
        vehicle_configs = {}
        all_vehicles = Vehicle.query.all()
        for v in all_vehicles:
            if v.atelier:
                vehicle_configs[v.id] = {
                    'min_trip_km': v.atelier.min_trip_km,
                    'max_trip_km': v.atelier.max_trip_km,
                    'movement_type': v.movement_type
                }

        # Load and process the file
        df = load_file(filepath)
        processed = process_dataframe(df, include_date=True, ref_hour=ref_hour, ref_min=ref_min, vehicle_configs=vehicle_configs)
        
        # First, check for duplicate dates
        dates_to_add = set()
        dates_existing = set()
        
        for _, row in processed.iterrows():
            vehicle = row['vehicle']
            day_map = row['day_map']
            if day_map:
                for (year, month, day), metrics in day_map.items():
                    date_obj = datetime(year, month, day).date()
                    dates_to_add.add(date_obj)
                    
                    # Check if this date-vehicle combo already exists
                    existing = VehicleActivity.query.filter_by(
                        date=date_obj,
                        vehicle_code=vehicle
                    ).first()
                    
                    if existing:
                        dates_existing.add(date_obj)
        
        # If any dates already exist, return warning
        if dates_existing:
            existing_dates_str = ', '.join([d.isoformat() for d in sorted(dates_existing)])
            return jsonify({
                'error': 'Duplicate Upload Prevented',
                'message': f'The following date(s) are already in the database and will NOT be re-uploaded:\n\n{existing_dates_str}\n\nTo re-upload this data, please delete the existing records first.',
                'duplicate': True,
                'existing_dates': [d.isoformat() for d in sorted(dates_existing)]
            }), 409
        
        # Store in database only new records
        stored_count = 0
        for _, row in processed.iterrows():
            vehicle = row['vehicle']
            day_map = row['day_map']
            if day_map:
                for (year, month, day), metrics in day_map.items():
                    # Create new record (we already checked above)
                    activity = VehicleActivity(
                        date=datetime(year, month, day).date(),
                        vehicle_code=vehicle,
                        hours_before_20h=metrics['before_sec'] / 3600,
                        hours_after_20h=metrics['after_sec'] / 3600,
                        km_before=metrics['km_before'],
                        km_after=metrics['km_after'],
                        km_out_of_range=metrics.get('km_out_of_range', 0.0),
                        trip_count=metrics.get('trip_count', 0),
                        attente_count=metrics.get('attente_count', 0),
                        duration_course=metrics.get('dur_course', 0.0),
                        duration_attente=metrics.get('dur_attente', 0.0),
                        duration_arret=metrics.get('dur_arret', 0.0)
                    )
                    db.session.add(activity)
                    stored_count += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'✓ Successfully stored {stored_count} new records in database.',
            'records': stored_count
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/recalculate', methods=['POST'])
def recalculate_metrics():
    """Recalculate trip counts and clean up cached temp files."""
    try:
        import shutil
        
        # 1. Clean up cached temp files
        files_deleted = 0
        output_folder = app.config.get('OUTPUT_FOLDER')
        upload_folder = app.config.get('UPLOAD_FOLDER')
        
        if output_folder and Path(output_folder).exists():
            for f in Path(output_folder).glob('*'):
                try:
                    f.unlink()
                    files_deleted += 1
                except: pass
        
        if upload_folder and Path(upload_folder).exists():
            for f in Path(upload_folder).glob('*'):
                try:
                    f.unlink()
                    files_deleted += 1
                except: pass
        
        # 2. Build vehicle configurations map
        vehicle_configs = {}
        all_vehicles = Vehicle.query.all()
        for v in all_vehicles:
            if v.atelier:
                vehicle_configs[v.id] = {
                    'min_trip_km': v.atelier.min_trip_km,
                    'max_trip_km': v.atelier.max_trip_km,
                    'movement_type': v.movement_type or 'Move'
                }
        
        # 3. Get all activities and recalculate trip counts
        activities = VehicleActivity.query.all()
        updated = 0
        
        for activity in activities:
            v_id = activity.vehicle_code
            if v_id in vehicle_configs:
                config = vehicle_configs[v_id]
                movement = config.get('movement_type', 'Move')
                
                # If vehicle is "Sur place", set trip count to 0
                if movement == 'Sur place':
                    if activity.trip_count != 0:
                        activity.trip_count = 0
                        updated += 1
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'✓ Mise à jour terminée!\n• {files_deleted} fichiers cache supprimés\n• {updated} enregistrements modifiés',
            'files_deleted': files_deleted,
            'updated': updated,
            'note': 'Les données sont maintenant à jour. Pour recalculer complètement, veuillez re-uploader le fichier CSV.'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/dates')
def get_dates():
    """Get list of available dates in database."""
    try:
        dates = db.session.query(VehicleActivity.date).distinct().order_by(VehicleActivity.date.desc()).all()
        return jsonify({
            'dates': [d[0].isoformat() for d in dates]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/report/delete/<date_str>', methods=['DELETE'])
def delete_date(date_str):
    """Delete all records for a specific date."""
    try:
        target_date = datetime.fromisoformat(date_str).date()
        
        # Delete all records for this date
        deleted_count = VehicleActivity.query.filter_by(date=target_date).delete()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Deleted {deleted_count} records for {date_str}',
            'deleted': deleted_count
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/report/by-date', methods=['POST'])
def report_by_date():
    """Generate report for a specific date."""
    try:
        data = request.json
        date_str = data.get('date')
        format_type = data.get('format', 'csv').lower()  # 'csv' or 'pdf'
        
        if not date_str:
            return jsonify({'error': 'Date is required'}), 400
        
        target_date = datetime.fromisoformat(date_str).date()
        
        # Query database for this date
        records = VehicleActivity.query.filter_by(date=target_date).all()
        
        if not records:
            return jsonify({'error': f'No records found for {date_str}'}), 404
        
        # Get vehicle details for report
        vehicles_dict = {}
        all_vehicles = Vehicle.query.all()
        for v in all_vehicles:
            vehicles_dict[v.id] = v
        
        if format_type == 'pdf':
            # Generate PDF
            pdf_buffer = generate_pdf_report_by_date(target_date, records, vehicles_dict)
            output_folder = Path(app.config['OUTPUT_FOLDER'])
            filename = f"report_{target_date.isoformat()}.pdf"
            filepath = output_folder / filename
            with open(filepath, 'wb') as f:
                f.write(pdf_buffer.getvalue())
        else:
            # Generate CSV (original behavior)
            data_list = [r.to_dict() for r in records]
            report_df = pd.DataFrame(data_list)
            output_folder = Path(app.config['OUTPUT_FOLDER'])
            filename = f"report_{target_date.isoformat()}.csv"
            filepath = output_folder / filename
            report_df.to_csv(filepath, index=False)
        
        return jsonify({
            'success': True,
            'message': f'Report generated for {target_date}',
            'filename': filename,
            'rows': len(records)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/report/by-month', methods=['POST'])
def report_by_month():
    """Generate report for a specific month."""
    try:
        data = request.json
        year = data.get('year')
        month = data.get('month')
        format_type = data.get('format', 'csv').lower()  # 'csv' or 'pdf'
        
        if not year or not month:
            return jsonify({'error': 'Year and month are required'}), 400
        
        # Query database for this month
        records = VehicleActivity.query.filter(
            db.func.strftime('%Y', VehicleActivity.date) == str(year).zfill(4),
            db.func.strftime('%m', VehicleActivity.date) == str(month).zfill(2)
        ).all()
        
        if not records:
            return jsonify({'error': f'No records found for {year}-{month:02d}'}), 404
        
        # Aggregate by vehicle
        summary = {}
        for record in records:
            vehicle = record.vehicle_code
            if vehicle not in summary:
                summary[vehicle] = {
                    'hours_before_20h': 0.0,
                    'hours_after_20h': 0.0,
                    'km_before': 0.0,
                    'km_after': 0.0
                }
            summary[vehicle]['hours_before_20h'] += record.hours_before_20h
            summary[vehicle]['hours_after_20h'] += record.hours_after_20h
            summary[vehicle]['km_before'] += record.km_before
            summary[vehicle]['km_after'] += record.km_after
        
        # Get vehicle details for report
        vehicles_dict = {}
        all_vehicles = Vehicle.query.all()
        for v in all_vehicles:
            vehicles_dict[v.id] = v
        
        output_folder = Path(app.config['OUTPUT_FOLDER'])
        
        if format_type == 'pdf':
            # Generate PDF
            pdf_buffer = generate_pdf_report_by_month(year, month, summary, vehicles_dict)
            filename = f"report_{year:04d}-{month:02d}.pdf"
            filepath = output_folder / filename
            with open(filepath, 'wb') as f:
                f.write(pdf_buffer.getvalue())
        else:
            # Generate CSV
            data_list = []
            for vehicle, metrics in summary.items():
                data_list.append({
                    'year_month': f'{year:04d}-{month:02d}',
                    'vehicle': vehicle,
                    'hours_before_20h': round(metrics['hours_before_20h'], 2),
                    'hours_after_20h': round(metrics['hours_after_20h'], 2),
                    'km_before': round(metrics['km_before'], 3),
                    'km_after': round(metrics['km_after'], 3)
                })
            
            report_df = pd.DataFrame(data_list)
            filename = f"report_{year:04d}-{month:02d}.csv"
            filepath = output_folder / filename
            report_df.to_csv(filepath, index=False)
        
        return jsonify({
            'success': True,
            'message': f'Report generated for {year}-{month:02d}',
            'filename': filename,
            'rows': len(summary)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/report/by-week', methods=['POST'])
def report_by_week():
    """Generate report for a specific week (ISO 8601 week number)."""
    try:
        from datetime import datetime, timedelta
        
        data = request.json
        year = data.get('year')
        week = data.get('week')
        format_type = data.get('format', 'csv').lower()  # 'csv' or 'pdf'
        
        if not year or not week:
            return jsonify({'error': 'Year and week are required'}), 400
        
        # Calculate start and end dates for the ISO week
        # ISO 8601: Week 1 is the week with the first Thursday
        jan4 = datetime(year, 1, 4)
        week_one_monday = jan4 - timedelta(days=jan4.weekday())
        week_start = week_one_monday + timedelta(weeks=week-1)
        week_end = week_start + timedelta(days=6)
        
        week_start_str = week_start.strftime('%Y-%m-%d')
        week_end_str = week_end.strftime('%Y-%m-%d')
        
        # Query database for this week
        records = VehicleActivity.query.filter(
            VehicleActivity.date >= week_start_str,
            VehicleActivity.date <= week_end_str
        ).all()
        
        if not records:
            return jsonify({'error': f'No records found for week {week} of {year}'}), 404
        
        # Aggregate by vehicle
        summary = {}
        for record in records:
            vehicle = record.vehicle_code
            if vehicle not in summary:
                summary[vehicle] = {
                    'hours_before_20h': 0.0,
                    'hours_after_20h': 0.0,
                    'km_before': 0.0,
                    'km_after': 0.0
                }
            summary[vehicle]['hours_before_20h'] += record.hours_before_20h
            summary[vehicle]['hours_after_20h'] += record.hours_after_20h
            summary[vehicle]['km_before'] += record.km_before
            summary[vehicle]['km_after'] += record.km_after
        
        # Get vehicle details for report
        vehicles_dict = {}
        all_vehicles = Vehicle.query.all()
        for v in all_vehicles:
            vehicles_dict[v.id] = v
        
        if format_type == 'pdf':
            # Generate PDF with aggregated summary
            pdf_buffer = generate_pdf_report_by_week(year, week, week_start_str, week_end_str, summary, vehicles_dict)
            output_folder = Path(app.config['OUTPUT_FOLDER'])
            filename = f"report_{year:04d}-W{week:02d}.pdf"
            filepath = output_folder / filename
            with open(filepath, 'wb') as f:
                f.write(pdf_buffer.getvalue())
        else:
            # Generate CSV
            data_list = []
            for vehicle, metrics in summary.items():
                data_list.append({
                    'year_week': f'{year:04d}-W{week:02d}',
                    'week_start': week_start_str,
                    'week_end': week_end_str,
                    'vehicle': vehicle,
                    'hours_before_20h': round(metrics['hours_before_20h'], 2),
                    'hours_after_20h': round(metrics['hours_after_20h'], 2),
                    'km_before': round(metrics['km_before'], 3),
                    'km_after': round(metrics['km_after'], 3)
                })
            
            report_df = pd.DataFrame(data_list)
            output_folder = Path(app.config['OUTPUT_FOLDER'])
            filename = f"report_{year:04d}-W{week:02d}.csv"
            filepath = output_folder / filename
            report_df.to_csv(filepath, index=False)
        
        return jsonify({
            'success': True,
            'message': f'Report generated for week {week} of {year}',
            'filename': filename,
            'rows': len(summary)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/download/<filename>')
def download_file(filename):
    try:
        output_folder = Path(app.config['OUTPUT_FOLDER'])
        filepath = output_folder / secure_filename(filename)
        
        if not filepath.exists():
            return jsonify({'error': 'File not found'}), 404
        
        return send_file(str(filepath), as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/vehicles/upload', methods=['POST'])
def upload_vehicles():
    """Upload vehicle data from Excel file."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    try:
        # Read Excel file
        df = pd.read_excel(file)
        
        # Validate required columns
        required_cols = {'ID', 'Matricule', 'Name', 'Category'}
        if not required_cols.issubset(df.columns):
            return jsonify({'error': f'Excel must have columns: {", ".join(required_cols)}'}), 400
        
        # Process and store vehicles
        added = 0
        updated = 0
        errors = []
        
        for idx, row in df.iterrows():
            try:
                vehicle_id = str(row['ID']).strip()
                matricule = str(row['Matricule']).strip()
                name = str(row['Name']).strip()
                category = str(row['Category']).strip()
                
                if not vehicle_id or not matricule or not name or not category:
                    errors.append(f"Row {idx+2}: Missing required fields")
                    continue
                
                existing = Vehicle.query.filter_by(id=vehicle_id).first()
                if existing:
                    existing.matricule = matricule
                    existing.name = name
                    existing.category = category
                    updated += 1
                else:
                    vehicle = Vehicle(id=vehicle_id, matricule=matricule, name=name, category=category)
                    db.session.add(vehicle)
                    added += 1
            except Exception as e:
                errors.append(f"Row {idx+2}: {str(e)}")
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'added': added,
            'updated': updated,
            'errors': errors,
            'message': f'Added {added} vehicles, updated {updated} vehicles'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to read Excel file: {str(e)}'}), 400

@app.route('/vehicles/add', methods=['POST'])
def add_vehicle():
    """Manually add a single vehicle."""
    try:
        data = request.json
        vehicle_id = data.get('id', '').strip()
        matricule = data.get('matricule', '').strip()
        name = data.get('name')
        category = data.get('category')
        movement_type = data.get('movement_type', 'Move')
        
        if not vehicle_id or not matricule or not name or not category:
            return jsonify({'error': 'All fields required'}), 400
            
        existing = Vehicle.query.filter_by(id=vehicle_id).first()
        if existing:
            return jsonify({'error': 'Vehicle ID already exists'}), 400
            
        vehicle = Vehicle(
            id=vehicle_id, 
            matricule=matricule, 
            name=name, 
            category=category,
            movement_type=movement_type
        )
        db.session.add(vehicle)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Vehicle {vehicle_id} added successfully',
            'vehicle': vehicle.to_dict()
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/vehicles', methods=['GET'])
def get_vehicles():
    """Get list of all vehicles."""
    try:
        vehicles = Vehicle.query.all()
        return jsonify({
            'vehicles': [v.to_dict() for v in vehicles],
            'total': len(vehicles)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/vehicles/<vehicle_id>', methods=['GET'])
def get_vehicle(vehicle_id):
    """Get vehicle details."""
    try:
        vehicle = Vehicle.query.filter_by(id=vehicle_id).first()
        if not vehicle:
            return jsonify({'error': 'Vehicle not found'}), 404
        return jsonify(vehicle.to_dict())
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/vehicles/<vehicle_id>', methods=['DELETE'])
def delete_vehicle(vehicle_id):
    """Delete a vehicle."""
    try:
        vehicle = Vehicle.query.filter_by(id=vehicle_id).first()
        if not vehicle:
            return jsonify({'error': 'Vehicle not found'}), 404
        
        db.session.delete(vehicle)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Vehicle {vehicle_id} deleted successfully'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/vehicles/<vehicle_id>', methods=['PUT'])
def update_vehicle(vehicle_id):
    """Update vehicle details."""
    try:
        vehicle = Vehicle.query.filter_by(id=vehicle_id).first()
        if not vehicle:
            return jsonify({'error': 'Vehicle not found'}), 404
            
        data = request.json
        matricule = data.get('matricule')
        name = data.get('name')
        category = data.get('category')
        movement_type = data.get('movement_type')
        
        if matricule:
            vehicle.matricule = matricule.strip()
        if name:
            vehicle.name = name.strip()
        if category:
            vehicle.category = category.strip()
        if movement_type:
            vehicle.movement_type = movement_type
            
        db.session.commit()
        return jsonify(vehicle.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/categories', methods=['GET'])
def get_categories():
    """Get list of all categories."""
    try:
        categories = db.session.query(Vehicle.category).distinct().order_by(Vehicle.category).all()
        return jsonify({
            'categories': [c[0] for c in categories]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

# --- Hierarchy Management Routes ---

@app.route('/hierarchy', methods=['GET'])
def get_hierarchy():
    """Get full hierarchy: Projects -> Ateliers -> Vehicles."""
    try:
        projects = Project.query.all()
        # Get unassigned vehicles
        unassigned = Vehicle.query.filter_by(atelier_id=None).all()
        
        return jsonify({
            'projects': [p.to_dict() for p in projects],
            'unassigned_vehicles': [v.to_dict() for v in unassigned]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/projects', methods=['POST'])
def create_project():
    try:
        data = request.json
        name = data.get('name')
        description = data.get('description')
        province = data.get('province')
        
        if not name:
            return jsonify({'error': 'Project name required'}), 400
        
        project = Project(name=name, description=description, province=province)
        db.session.add(project)
        db.session.commit()
        return jsonify(project.to_dict())
    except Exception as e:
        db.session.rollback()
        if 'UNIQUE constraint failed' in str(e):
             return jsonify({'error': f'Un projet nommé "{name}" existe déjà.'}), 400
        return jsonify({'error': str(e)}), 400

@app.route('/projects/<int:project_id>/ateliers', methods=['POST'])
def create_atelier(project_id):
    try:
        data = request.json
        name = data.get('name')
        min_trip_km = data.get('min_trip_km', 0.5)
        max_trip_km = data.get('max_trip_km', 15.0)
        
        if not name:
            return jsonify({'error': 'Atelier name required'}), 400
            
        atelier = Atelier(
            name=name, 
            project_id=project_id,
            min_trip_km=float(min_trip_km),
            max_trip_km=float(max_trip_km)
        )
        db.session.add(atelier)
        db.session.commit()
        return jsonify(atelier.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/vehicles/<vehicle_id>/assign', methods=['POST'])
def assign_vehicle(vehicle_id):
    try:
        data = request.json
        atelier_id = data.get('atelier_id')
        
        vehicle = Vehicle.query.filter_by(id=vehicle_id).first()
        if not vehicle:
            return jsonify({'error': 'Vehicle not found'}), 404
            
        if atelier_id is None:
            vehicle.atelier_id = None
        else:
            vehicle.atelier_id = atelier_id
            
        db.session.commit()
        return jsonify(vehicle.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/projects/<int:project_id>', methods=['DELETE'])
def delete_project(project_id):
    try:
        project = Project.query.get(project_id)
        if not project:
            return jsonify({'error': 'Project not found'}), 404
        
        # Check if project has ateliers
        if len(project.ateliers) > 0:
            return jsonify({
                'error': 'Cannot delete project',
                'message': f'Ce projet contient {len(project.ateliers)} atelier(s). Supprimez d\'abord les ateliers.'
            }), 400
        
        db.session.delete(project)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Projet supprimé'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/ateliers/<int:atelier_id>', methods=['PUT'])
def update_atelier(atelier_id):
    try:
        atelier = Atelier.query.get(atelier_id)
        if not atelier:
            return jsonify({'error': 'Atelier not found'}), 404
            
        data = request.json
        name = data.get('name')
        min_trip_km = data.get('min_trip_km')
        max_trip_km = data.get('max_trip_km')
        
        if name:
            atelier.name = name
        if min_trip_km is not None:
            atelier.min_trip_km = float(min_trip_km)
        if max_trip_km is not None:
            atelier.max_trip_km = float(max_trip_km)
            
        db.session.commit()
        return jsonify(atelier.to_dict())
    except Exception as e:
        db.session.rollback()
        if 'UNIQUE constraint failed' in str(e):
             return jsonify({'error': f'Un atelier nommé "{name}" existe déjà.'}), 400
        return jsonify({'error': str(e)}), 400

@app.route('/ateliers/<int:atelier_id>', methods=['DELETE'])
def delete_atelier(atelier_id):
    try:
        atelier = Atelier.query.get(atelier_id)
        if not atelier:
            return jsonify({'error': 'Atelier not found'}), 404
        
        # Check if atelier has assigned vehicles
        if len(atelier.vehicles) > 0:
            return jsonify({
                'error': 'Cannot delete atelier',
                'message': f'Cet atelier contient {len(atelier.vehicles)} engin(s). Désassignez d\'abord les engins.'
            }), 400
        
        db.session.delete(atelier)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Atelier supprimé'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/vehicles/<vehicle_id>/unassign', methods=['POST'])
def unassign_vehicle(vehicle_id):
    try:
        vehicle = Vehicle.query.filter_by(id=vehicle_id).first()
        if not vehicle:
            return jsonify({'error': 'Vehicle not found'}), 404
            
        vehicle.atelier_id = None
        db.session.commit()
        return jsonify(vehicle.to_dict())
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

# --- Advanced Reporting System Routes ---

@app.route('/reports/comparative', methods=['POST'])
def comparative_report():
    """Generate comparative analysis for multiple ateliers."""
    try:
        data = request.json
        atelier_ids = data.get('atelier_ids', [])
        start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d').date()
        end_date = datetime.strptime(data.get('end_date'), '%Y-%m-%d').date()
        
        if not atelier_ids:
            return jsonify({'error': 'At least one atelier required'}), 400
        
        results = generate_comparative_report(atelier_ids, start_date, end_date)
        
        return jsonify({
            'success': True,
            'data': results,
            'date_range': f"{start_date} to {end_date}"
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/reports/atelier/<int:atelier_id>', methods=['POST'])
def atelier_performance(atelier_id):
    """Generate detailed performance report for single atelier."""
    try:
        data = request.json
        start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d').date()
        end_date = datetime.strptime(data.get('end_date'), '%Y-%m-%d').date()
        
        result = generate_atelier_performance_report(atelier_id, start_date, end_date)
        
        if not result:
            return jsonify({'error': 'No data found for this atelier'}), 404
        
        return jsonify({
            'success': True,
            'data': result
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400



@app.route('/reports/atelier/<int:atelier_id>/pdf', methods=['POST'])
def atelier_performance_pdf(atelier_id):
    """Generate detailed performance report PDF for single atelier."""
    try:
        data = request.json
        start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d').date()
        end_date = datetime.strptime(data.get('end_date'), '%Y-%m-%d').date()
        
        result = generate_atelier_performance_report(atelier_id, start_date, end_date)
        
        if not result:
            return jsonify({'error': 'No data found for this atelier'}), 404
        
        pdf_buffer = generate_atelier_pdf(result)
        
        filename = f"Rapport_Atelier_{result['atelier_name'].replace(' ', '_')}_{start_date}.pdf"
        
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 400


def generate_atelier_pdf(data):
    """Generate PDF for atelier performance."""
    from reportlab.lib.pagesizes import A4, landscape
    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=landscape(A4), topMargin=0.5*inch, bottomMargin=0.5*inch, leftMargin=0.5*inch, rightMargin=0.5*inch)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#2c3e50'),
        spaceAfter=20,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )
    
    h2_style = ParagraphStyle(
        'CustomH2',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#667eea'),
        spaceBefore=15,
        spaceAfter=10,
        fontName='Helvetica-Bold'
    )
    
    story = []
    
    # Header
    story.append(Paragraph('📊 RAPPORT PERFORMANCE ATELIER', title_style))
    story.append(Spacer(1, 0.1*inch))
    
    # Atelier Info Card
    info_data = [
        [f"Atelier: {data['atelier_name']}", f"Projet: {data['project_name']}"],
        [f"Période: {data['date_range']}", f"Engins: {data['vehicle_count']}"]
    ]
    info_table = Table(info_data, colWidths=[3.5*inch, 3.5*inch])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 12),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#2d3748')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 0.2*inch))
    
    # Main Metrics Grid
    story.append(Paragraph('VUE D\'ENSEMBLE (TRAVAIL)', h2_style))
    
    metrics_data = [
        ['Engins', 'Cycles', 'KM Travaillé', 'Hrs Travaillé'],
        [str(data['vehicle_count']), str(data['total_trips']), f"{data['total_km_work']} km", f"{data['working_hours']} h"]
    ]
    
    metrics_table = Table(metrics_data, colWidths=[1.8*inch, 1.8*inch, 1.8*inch, 1.8*inch])
    metrics_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a5568')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 1), (-1, 1), 14),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#f7fafc')),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
    ]))
    story.append(metrics_table)
    story.append(Spacer(1, 0.2*inch))
    
    # Secondary Metrics - operational
    story.append(Paragraph('VUE OPÉRATIONNELLE', h2_style))
    op_data = [
        ['KM Hors Travail', 'Distance Moy./Cycle', 'Rapport Km Trav./Total'],
        [f"{data['total_km_out']} km", f"{data['avg_trip_distance']} km", f"{round(data['total_km_work']/data['total_km']*100, 1) if data['total_km']>0 else 0}%"]
    ]
    op_table = Table(op_data, colWidths=[2.4*inch, 2.4*inch, 2.4*inch])
    op_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#718096')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
    ]))
    story.append(op_table)
    story.append(Spacer(1, 0.2*inch))
    
    # Efficiency & Utilization with colors
    story.append(Paragraph('PERFORMANCE & EFFICIENCE', h2_style))
    
    kpi_data = [
        ['Taux d\'Efficacité', 'Taux d\'Utilisation'],
        [f"{data['efficiency_rate']}%", f"{data['utilization_rate']}%"]
    ]
    
    kpi_table = Table(kpi_data, colWidths=[3.6*inch, 3.6*inch])
    
    eff_color = colors.green if data['efficiency_rate'] >= 80 else colors.orange if data['efficiency_rate'] >= 60 else colors.red
    
    kpi_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2b6cb0')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 1), (-1, 1), 16),
        ('TEXTCOLOR', (0, 1), (0, 1), eff_color), # Efficiency Color
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 0.3*inch))
    
    # Vehicle Details Table
    story.append(Paragraph('DÉTAILS DES ENGINS', h2_style))
    
    # Table Header - User requested order
    table_headers = ['Engin', 'Cycles', 'KM Travaillé', 'Hrs Travaillé', 'KM Hors Travail', "Nbr d'attente", "Durée d'attente"]
    table_data = [table_headers]
    
    for v in data['vehicles']:
        m_type = v.get('movement_type', 'Move')
        trips_display = str(v['trips']) if m_type == 'Move' else 'N/A'
        
        table_data.append([
            f"{v['name']}\n({v['id']})",
            trips_display,
            v['km'],
            v['working_hours'],
            v.get('km_out_of_range', 0),
            v.get('attente_count', 0),
            f"{v.get('duration_attente', 0)} h"
        ])
        
    vehicle_table = Table(table_data, colWidths=[2.5*inch, 0.7*inch, 1.0*inch, 1.0*inch, 1.1*inch, 1.0*inch, 1.0*inch])
    vehicle_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'), # Left align names
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
    ]))
    
    story.append(vehicle_table)
    
    story.append(Spacer(1, 0.2*inch))
    legend_style = ParagraphStyle('Legend', parent=styles['Normal'], fontSize=8, textColor=colors.grey)
    story.append(Paragraph("* Cycles : Détection Intelligente - Si distance ≥ 70% de la médiane = 1 cycle complet, sinon = 0.5 cycle (arrêt en cours)", legend_style))
    story.append(Paragraph("** Les engins 'Sur place' ne comptent pas de cycles.", legend_style))
    
    doc.build(story)
    pdf_buffer.seek(0)
    return pdf_buffer


@app.route('/reports/project/<int:project_id>', methods=['POST'])
def project_overview(project_id):
    """Generate project overview with all ateliers."""
    try:
        data = request.json
        start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d').date()
        end_date = datetime.strptime(data.get('end_date'), '%Y-%m-%d').date()
        
        result = generate_project_overview(project_id, start_date, end_date)
        
        if not result:
            return jsonify({'error': 'No data found for this project'}), 404
        
        return jsonify({
            'success': True,
            'data': result
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

def generate_pdf_report_by_date(target_date, records, vehicles_dict):
    """Generate a professional PDF report for a specific date."""
    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#2c3e50'),
        spaceAfter=30,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )
    
    story = []
    
    # Title
    story.append(Paragraph('📊 RAPPORT D\'ACTIVITÉ QUOTIDIEN', title_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Date info
    info_style = ParagraphStyle('Info', parent=styles['Normal'], fontSize=12, alignment=TA_CENTER)
    # Format date in French
    date_str = target_date.strftime('%d %B %Y').replace('January', 'Janvier').replace('February', 'Février').replace('March', 'Mars').replace('April', 'Avril').replace('May', 'Mai').replace('June', 'Juin').replace('July', 'Juillet').replace('August', 'Août').replace('September', 'Septembre').replace('October', 'Octobre').replace('November', 'Novembre').replace('December', 'Décembre')
    story.append(Paragraph(f'<b>Date du Rapport:</b> {date_str}', info_style))
    story.append(Spacer(1, 0.3*inch))
    
    # Group by category
    categories_dict = {}
    for record in records:
        vehicle = record.vehicle_code
        vehicle_obj = vehicles_dict.get(vehicle)
        category = vehicle_obj.category if vehicle_obj else 'Unknown'
        
        if category not in categories_dict:
            categories_dict[category] = []
        categories_dict[category].append(record)
    
    # Create tables for each category
    for category in sorted(categories_dict.keys()):
        story.append(Paragraph(f'<b>{category}</b>', styles['Heading2']))
        
        # Table data with formatted cells
        table_data = [['ID Véhicule', 'Nom du Véhicule', 'Matricule', 'Avant 18:30\n(Heures)', 'Après 18:30\n(Heures)', 'Avant 18:30\n(KM)', 'Après 18:30\n(KM)']]
        
        for record in categories_dict[category]:
            vehicle_obj = vehicles_dict.get(record.vehicle_code)
            vehicle_name = vehicle_obj.name if vehicle_obj else '-'
            matricule = f'<font size="8">{vehicle_obj.matricule if vehicle_obj else "-"}</font>'
            
            table_data.append([
                record.vehicle_code,
                vehicle_name,
                Paragraph(matricule, styles['Normal']),
                format_decimal_hours(record.hours_before_20h),
                format_decimal_hours(record.hours_after_20h),
                f"{record.km_before:.2f}",
                f"{record.km_after:.2f}"
            ])
        
        # Add totals row
        total_hours_before = sum(r.hours_before_20h for r in categories_dict[category])
        total_hours_after = sum(r.hours_after_20h for r in categories_dict[category])
        total_km_before = sum(r.km_before for r in categories_dict[category])
        total_km_after = sum(r.km_after for r in categories_dict[category])
        
        table_data.append([
            'TOTAL',
            '', '',
            format_decimal_hours(total_hours_before),
            format_decimal_hours(total_hours_after),
            f"{total_km_before:.2f}",
            f"{total_km_after:.2f}"
        ])
        
        # Style table with proper column widths
        table = Table(table_data, colWidths=[0.9*inch, 2.2*inch, 1.1*inch, 0.95*inch, 0.95*inch, 0.95*inch, 0.95*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#E7E6E6')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#F2F2F2')]),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6)
        ]))
        
        story.append(table)
        story.append(Spacer(1, 0.3*inch))
    
    doc.build(story)
    pdf_buffer.seek(0)
    return pdf_buffer

def generate_pdf_report_by_month(year, month, summary, vehicles_dict):
    """Generate a professional PDF report for a specific month."""
    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#2c3e50'),
        spaceAfter=30,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )
    
    story = []
    
    # Title
    story.append(Paragraph('📊 RAPPORT D\'ACTIVITÉ MENSUEL', title_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Month info - Format in French
    from datetime import date
    french_months = {1: 'Janvier', 2: 'Février', 3: 'Mars', 4: 'Avril', 5: 'Mai', 6: 'Juin', 7: 'Juillet', 8: 'Août', 9: 'Septembre', 10: 'Octobre', 11: 'Novembre', 12: 'Décembre'}
    month_name = f'{french_months[month]} {year}'
    info_style = ParagraphStyle('Info', parent=styles['Normal'], fontSize=12, alignment=TA_CENTER)
    story.append(Paragraph(f'<b>Période du Rapport:</b> {month_name}', info_style))
    story.append(Spacer(1, 0.3*inch))
    
    # Group by category
    categories_dict = {}
    for vehicle_code, metrics in summary.items():
        vehicle_obj = vehicles_dict.get(vehicle_code)
        category = vehicle_obj.category if vehicle_obj else 'Unknown'
        
        if category not in categories_dict:
            categories_dict[category] = {}
        categories_dict[category][vehicle_code] = metrics
    
    # Create tables for each category
    for category in sorted(categories_dict.keys()):
        story.append(Paragraph(f'<b>{category}</b>', styles['Heading2']))
        
        # Table data with formatted cells
        table_data = [['ID Véhicule', 'Nom du Véhicule', 'Matricule', 'Avant 18:30\n(Heures)', 'Après 18:30\n(Heures)', 'Avant 18:30\n(KM)', 'Après 18:30\n(KM)']]
        
        for vehicle_code in sorted(categories_dict[category].keys()):
            metrics = categories_dict[category][vehicle_code]
            vehicle_obj = vehicles_dict.get(vehicle_code)
            vehicle_name = vehicle_obj.name if vehicle_obj else '-'
            matricule = f'<font size="8">{vehicle_obj.matricule if vehicle_obj else "-"}</font>'
            
            table_data.append([
                vehicle_code,
                vehicle_name,
                Paragraph(matricule, styles['Normal']),
                format_decimal_hours(metrics['hours_before_20h']),
                format_decimal_hours(metrics['hours_after_20h']),
                f"{metrics['km_before']:.2f}",
                f"{metrics['km_after']:.2f}"
            ])
        
        # Add totals row
        total_hours_before = sum(m['hours_before_20h'] for m in categories_dict[category].values())
        total_hours_after = sum(m['hours_after_20h'] for m in categories_dict[category].values())
        total_km_before = sum(m['km_before'] for m in categories_dict[category].values())
        total_km_after = sum(m['km_after'] for m in categories_dict[category].values())
        
        table_data.append([
            'TOTAL',
            '', '',
            format_decimal_hours(total_hours_before),
            format_decimal_hours(total_hours_after),
            f"{total_km_before:.2f}",
            f"{total_km_after:.2f}"
        ])
        
        # Style table with proper column widths
        table = Table(table_data, colWidths=[0.9*inch, 2.2*inch, 1.1*inch, 0.95*inch, 0.95*inch, 0.95*inch, 0.95*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#E7E6E6')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#F2F2F2')]),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6)
        ]))
        
        story.append(table)
        story.append(Spacer(1, 0.3*inch))
    
    doc.build(story)
    pdf_buffer.seek(0)
    return pdf_buffer

def generate_pdf_report_by_week(year, week, week_start, week_end, summary, vehicles_dict):
    """Generate a professional PDF report for a specific week."""
    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#2c3e50'),
        spaceAfter=30,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )
    
    story = []
    
    # Title
    story.append(Paragraph('📊 RAPPORT D\'ACTIVITÉ HEBDOMADAIRE', title_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Date info
    info_style = ParagraphStyle('Info', parent=styles['Normal'], fontSize=12, alignment=TA_CENTER)
    story.append(Paragraph(f'<b>Semaine:</b> {year}-W{week:02d} ({week_start} à {week_end})', info_style))
    story.append(Spacer(1, 0.3*inch))
    
    # Group by category
    categories_dict = {}
    for vehicle_code in summary.keys():
        vehicle_obj = vehicles_dict.get(vehicle_code)
        category = vehicle_obj.category if vehicle_obj else 'Unknown'
        
        if category not in categories_dict:
            categories_dict[category] = {}
        categories_dict[category][vehicle_code] = summary[vehicle_code]
    
    # Create tables for each category
    for category in sorted(categories_dict.keys()):
        story.append(Paragraph(f'<b>{category}</b>', styles['Heading2']))
        
        # Table data with formatted cells
        table_data = [['ID Véhicule', 'Nom du Véhicule', 'Matricule', 'Avant 18:30\n(Heures)', 'Après 18:30\n(Heures)', 'Avant 18:30\n(KM)', 'Après 18:30\n(KM)']]
        
        for vehicle_code in sorted(categories_dict[category].keys()):
            metrics = categories_dict[category][vehicle_code]
            vehicle_obj = vehicles_dict.get(vehicle_code)
            vehicle_name = vehicle_obj.name if vehicle_obj else '-'
            matricule = f'<font size="8">{vehicle_obj.matricule if vehicle_obj else "-"}</font>'
            
            table_data.append([
                vehicle_code,
                vehicle_name,
                Paragraph(matricule, styles['Normal']),
                format_decimal_hours(metrics['hours_before_20h']),
                format_decimal_hours(metrics['hours_after_20h']),
                f"{metrics['km_before']:.2f}",
                f"{metrics['km_after']:.2f}"
            ])
        
        # Add totals row
        total_hours_before = sum(m['hours_before_20h'] for m in categories_dict[category].values())
        total_hours_after = sum(m['hours_after_20h'] for m in categories_dict[category].values())
        total_km_before = sum(m['km_before'] for m in categories_dict[category].values())
        total_km_after = sum(m['km_after'] for m in categories_dict[category].values())
        
        table_data.append([
            'TOTAL',
            '', '',
            format_decimal_hours(total_hours_before),
            format_decimal_hours(total_hours_after),
            f"{total_km_before:.2f}",
            f"{total_km_after:.2f}"
        ])
        
        # Style table with proper column widths
        table = Table(table_data, colWidths=[0.9*inch, 2.2*inch, 1.1*inch, 0.95*inch, 0.95*inch, 0.95*inch, 0.95*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#E7E6E6')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#F2F2F2')]),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6)
        ]))
        
        story.append(table)
        story.append(Spacer(1, 0.3*inch))
    
    doc.build(story)
    pdf_buffer.seek(0)
    return pdf_buffer

def generate_vehicle_list_pdf():
    """Generate a professional PDF with all vehicles grouped by category."""
    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=A4, topMargin=0.5*inch, bottomMargin=0.5*inch)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#2c3e50'),
        spaceAfter=30,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    )
    
    story = []
    
    # Title
    story.append(Paragraph('🚗 LISTE DES VÉHICULES', title_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Get all vehicles from database
    all_vehicles = Vehicle.query.all()
    
    if not all_vehicles:
        story.append(Paragraph('Aucun véhicule enregistré dans le système.', styles['Normal']))
    else:
        # Group vehicles by category
        categories_dict = {}
        for vehicle in all_vehicles:
            if vehicle.category not in categories_dict:
                categories_dict[vehicle.category] = []
            categories_dict[vehicle.category].append(vehicle)
        
        # Create tables for each category
        for category in sorted(categories_dict.keys()):
            story.append(Paragraph(f'<b>{category}</b>', styles['Heading2']))
            
            # Table data
            table_data = [['ID Véhicule', 'Nom du Véhicule', 'Matricule']]
            
            for vehicle in sorted(categories_dict[category], key=lambda v: v.id):
                table_data.append([
                    vehicle.id,
                    vehicle.name,
                    vehicle.matricule
                ])
            
            # Add total row
            table_data.append([
                f'TOTAL: {len(categories_dict[category])} véhicules',
                '', ''
            ])
            
            # Style table
            table = Table(table_data, colWidths=[1.5*inch, 2.5*inch, 1.5*inch])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#E7E6E6')),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ('GRID', (0, 0), (-1, -1), 1, colors.grey),
                ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#F2F2F2')])
            ]))
            
            story.append(table)
            story.append(Spacer(1, 0.3*inch))
    
    doc.build(story)
    pdf_buffer.seek(0)
    return pdf_buffer

@app.route('/vehicles/download/pdf', methods=['GET'])
def download_vehicle_list_pdf():
    """Download vehicle list as PDF."""
    try:
        pdf_buffer = generate_vehicle_list_pdf()
        
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'vehicle_fleet_list_{datetime.now().strftime("%Y%m%d")}.pdf'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/files')
def list_files():
    try:
        output_folder = Path(app.config['OUTPUT_FOLDER'])
        files = list(output_folder.glob('report_*'))
        return jsonify({
            'files': [
                {
                    'name': f.name,
                    'size': f.stat().st_size,
                    'modified': f.stat().st_mtime
                }
                for f in files
            ]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)

