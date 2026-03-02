import os
from pathlib import Path
from flask import Flask, render_template, request, send_file, jsonify
from werkzeug.utils import secure_filename
from report_logic import load_file, process_dataframe, generate_reports, format_decimal_hours
from models import db, VehicleActivity, Vehicle, Project, Atelier
import tempfile
from datetime import datetime
import pandas as pd
from advanced_reports import generate_comparative_report, generate_atelier_performance_report, generate_project_overview, generate_project_atelier_daily_report, generate_global_daily_report
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
import io
import json

def check_future_date(date_obj):
    """Check if a date is in the future."""
    if date_obj > datetime.now().date():
        return True
    return False

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
        
        target_date = datetime.fromisoformat(date_str).date()
        if check_future_date(target_date):
            return jsonify({'error': 'La date ne peut pas être dans le futur (aujourd\'hui est le ' + datetime.now().strftime('%d/%m/%Y') + ')'}), 400
        
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
        
        # Check if month/year is in the future
        current_date = datetime.now()
        if year > current_date.year or (year == current_date.year and month > current_date.month):
            return jsonify({'error': 'Le mois sélectionné est dans le futur.'}), 400

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
        
        if check_future_date(week_start.date()):
            return jsonify({'error': 'La semaine sélectionnée est dans le futur.'}), 400

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

@app.route('/ateliers/<int:atelier_id>/initialize', methods=['POST'])
def initialize_atelier(atelier_id):
    try:
        # Unassign all vehicles from this atelier
        Vehicle.query.filter_by(atelier_id=atelier_id).update({Vehicle.atelier_id: None})
        db.session.commit()
        return jsonify({'success': True})
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

@app.route('/projects/<int:project_id>', methods=['PUT'])
def update_project(project_id):
    try:
        project = Project.query.get(project_id)
        if not project:
            return jsonify({'error': 'Project not found'}), 404
        
        data = request.json
        name = data.get('name')
        description = data.get('description')
        province = data.get('province')
        
        if name:
            project.name = name
        if description is not None:
            project.description = description
        if province is not None:
            project.province = province
        
        db.session.commit()
        return jsonify(project.to_dict())
    except Exception as e:
        db.session.rollback()
        if 'UNIQUE constraint failed' in str(e):
            return jsonify({'error': f'Un projet nommé "{name}" existe déjà.'}), 400
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
        
        if check_future_date(start_date) or check_future_date(end_date):
            return jsonify({'error': 'Les dates ne peuvent pas être dans le futur.'}), 400

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

@app.route('/reports/comparative/pdf', methods=['POST'])
def comparative_report_pdf():
    """Generate comparative analysis PDF with charts."""
    try:
        data = request.json
        atelier_ids = data.get('atelier_ids', [])
        project_id = data.get('project_id')
        start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d').date()
        end_date = datetime.strptime(data.get('end_date'), '%Y-%m-%d').date()
        
        if check_future_date(start_date) or check_future_date(end_date):
            return jsonify({'error': 'Les dates ne peuvent pas être dans le futur.'}), 400

        # If project_id is provided, get all ateliers for that project
        project_name = None
        if project_id:
            from models import Project
            project = Project.query.get(project_id)
            if not project:
                return jsonify({'error': 'Projet introuvable'}), 404
            atelier_ids = [a.id for a in project.ateliers]
            project_name = project.name
        
        if not atelier_ids:
            return jsonify({'error': 'At least one atelier required'}), 400
        
        results = generate_comparative_report(atelier_ids, start_date, end_date)
        
        if not results:
            return jsonify({'error': 'Aucune donnée trouvée'}), 404
        
        pdf_buffer = generate_comparative_pdf(results, start_date, end_date, project_name)
        
        prefix = project_name.replace(' ', '_') if project_name else 'Comparative'
        filename = f"Analyse_{prefix}_{start_date}_{end_date}.pdf"
        
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 400


def generate_comparative_pdf(results, start_date, end_date, project_name=None):
    """Generate comparative analysis PDF with bar charts per atelier."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.enums import TA_CENTER
    from io import BytesIO
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), topMargin=10*mm, bottomMargin=10*mm, leftMargin=15*mm, rightMargin=15*mm)
    
    styles = getSampleStyleSheet()
    HEADER_BG = colors.HexColor('#0369a1')
    
    subtitle_style = ParagraphStyle('Subtitle', parent=styles['Normal'], fontSize=10, textColor=colors.HexColor('#334155'), fontName='Helvetica')
    section_style = ParagraphStyle('Section', parent=styles['Normal'], fontSize=13, textColor=colors.HexColor('#0369a1'), fontName='Helvetica-Bold', spaceBefore=10, spaceAfter=5)
    
    # Category color palette
    CATEGORY_COLORS = {
        'CAMION': '#2563eb',
        'PELLE': '#dc2626',
        'BULLDOZER': '#f59e0b',
        'CHARGEUSE': '#10b981',
        'COMPACTEUR': '#8b5cf6',
        'CITERNE': '#ec4899',
        'BENNE': '#06b6d4',
        'NIVELEUSE': '#84cc16',
        'TRACTEUR': '#f97316',
        'DIVERS': '#64748b',
    }
    DEFAULT_COLOR = '#94a3b8'
    
    story = []
    
    # Header
    title = 'ANALYSE COMPARATIVE DES ATELIERS'
    if project_name:
        title = f'ANALYSE COMPARATIVE - {project_name.upper()}'
    header_data = [[title]]
    header_table = Table(header_data, colWidths=[landscape(A4)[0] - 30*mm])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 16),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 3*mm))
    
    date_str = f"Période: {start_date.strftime('%d/%m/%Y')} au {end_date.strftime('%d/%m/%Y')}"
    story.append(Paragraph(f"<b>{date_str}</b>", subtitle_style))
    story.append(Spacer(1, 5*mm))
    
    # For each atelier, generate ONE chart with ALL vehicle types color-coded
    for atelier_data in results:
        atelier_name = atelier_data.get('atelier_name', 'N/A')
        project_name_data = atelier_data.get('project_name', 'N/A')
        vehicles = atelier_data.get('vehicles', [])
        
        if not vehicles:
            story.append(Paragraph(f"<b>PROJET: {project_name_data} - ATELIER: {atelier_name}</b> — Aucune donnée", section_style))
            story.append(Spacer(1, 5*mm))
            continue
        
        # Sort all vehicles by working_hours descending
        vehicles.sort(key=lambda x: x['working_hours'], reverse=True)
        
        v_ids = [v['id'] for v in vehicles]
        v_hours = [v['working_hours'] for v in vehicles]
        v_km = [v.get('total_km', 0) for v in vehicles]
        v_cats = [v.get('category', 'Autre').upper() for v in vehicles]
        bar_colors = [CATEGORY_COLORS.get(cat, DEFAULT_COLOR) for cat in v_cats]
        
        # Create ONE chart with all vehicles
        fig_width = max(10, len(v_ids) * 0.55)
        fig, ax = plt.subplots(figsize=(fig_width, 4.5))
        
        bars = ax.bar(range(len(v_ids)), v_hours, color=bar_colors, edgecolor='white', linewidth=0.5, width=0.75)
        
        # Secondary Y-axis for KM
        ax2 = ax.twinx()
        km_line, = ax2.plot(range(len(v_ids)), v_km, color='#dc2626', marker='o', linestyle='-', linewidth=1, markersize=3, label='KM Total', alpha=0.8)
        ax2.set_ylabel('Distance (KM)', fontsize=9, fontweight='bold', color='#dc2626')
        ax2.tick_params(axis='y', labelcolor='#dc2626', labelsize=7)
        
        # Add value labels on top of bars
        for bar, h in zip(bars, v_hours):
            if h > 0:
                hrs = int(h)
                mins = int(round((h - hrs) * 60))
                label = f"{hrs}h{mins:02d}" if hrs > 0 else f"{mins}min"
                ax.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 0.08,
                       label, ha='center', va='bottom', fontsize=6, fontweight='bold', color='#334155')
        
        ax.set_xlabel('Code Engin', fontsize=9, fontweight='bold', color='#475569')
        ax.set_ylabel('Heures', fontsize=9, fontweight='bold', color='#475569')
        ax.set_title(f'PROJET: {project_name_data} - ATELIER: {atelier_name}', fontsize=13, fontweight='bold', color='#0369a1', pad=12)
        
        ax.set_xticks(range(len(v_ids)))
        ax.set_xticklabels(v_ids, rotation=45, ha='right', fontsize=6)
        ax.set_ylim(0, max(v_hours) * 1.3 if v_hours else 1)
        ax2.set_ylim(0, max(v_km) * 1.3 if v_km else 1)
        
        # Style
        ax.spines['top'].set_visible(False)
        ax2.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax2.spines['left'].set_visible(False)
        ax.spines['left'].set_color('#cbd5e1')
        ax2.spines['right'].set_color('#cbd5e1')
        ax.spines['bottom'].set_color('#cbd5e1')
        ax.tick_params(colors='#64748b', labelsize=7)
        ax.yaxis.grid(True, alpha=0.3, color='#cbd5e1')
        ax.set_axisbelow(True)
        
        # Color legend for categories + KM
        unique_cats = sorted(set(v_cats))
        legend_elements = [Patch(facecolor=CATEGORY_COLORS.get(cat, DEFAULT_COLOR), label=cat) for cat in unique_cats]
        legend_elements.append(km_line)
        
        ax.legend(handles=legend_elements, loc='upper right', fontsize=7, framealpha=0.9,
                 edgecolor='#e2e8f0', fancybox=True, ncol=min(len(legend_elements), 5))
        
        plt.tight_layout()
        
        # Save chart to buffer
        chart_buffer = BytesIO()
        fig.savefig(chart_buffer, format='png', dpi=150, bbox_inches='tight', facecolor='white')
        plt.close(fig)
        chart_buffer.seek(0)
        
        # Add chart to story — use full page width
        page_width = landscape(A4)[0] - 30*mm
        img = Image(chart_buffer, width=page_width, height=115*mm)
        story.append(img)
        
        # Add page break between ateliers
        story.append(PageBreak())
    
    doc.build(story)
    buffer.seek(0)
    return buffer

@app.route('/reports/atelier/<int:atelier_id>', methods=['POST'])
def atelier_performance(atelier_id):
    """Generate detailed performance report for single atelier."""
    try:
        data = request.json
        start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d').date()
        end_date = datetime.strptime(data.get('end_date'), '%Y-%m-%d').date()
        
        if check_future_date(start_date) or check_future_date(end_date):
            return jsonify({'error': 'Les dates ne peuvent pas être dans le futur.'}), 400
        
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
        
        if check_future_date(start_date) or check_future_date(end_date):
            return jsonify({'error': 'Les dates ne peuvent pas être dans le futur.'}), 400
        
        result = generate_atelier_performance_report(atelier_id, start_date, end_date)
        
        if not result:
            return jsonify({'error': 'No data found for this atelier'}), 404
        
        pdf_buffer = generate_atelier_pdf(result)
        
        filename = f"{result['project_id']}_{result['atelier_name'].replace(' ', '_')}_{start_date}.pdf"
        
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/reports/project-atelier-daily/pdf', methods=['POST'])
def project_atelier_daily_pdf():
    """Generate daily activity report PDF for specific project/atelier."""
    try:
        data = request.json
        project_id = int(data.get('project_id'))
        atelier_id = data.get('atelier_id')
        if atelier_id:
            atelier_id = int(atelier_id)
        else:
            atelier_id = 0
            
        target_date = datetime.strptime(data.get('date'), '%Y-%m-%d').date()
        
        if check_future_date(target_date):
            return jsonify({'error': 'La date ne peut pas être dans le futur.'}), 400

        result = generate_project_atelier_daily_report(project_id, atelier_id, target_date)
        
        if not result:
            return jsonify({'error': 'No data found for this project/atelier'}), 404
        
        pdf_buffer = generate_project_atelier_daily_pdf(result)
        
        # Filename format: ProjectName_Date.pdf
        project_name_clean = result['project_name'].replace(' ', '_')
        filename = f"{project_name_clean}_{target_date}.pdf"
        
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/reports/global-daily/pdf', methods=['POST'])
def global_daily_pdf():
    """Generate daily activity report PDF for ALL projects."""
    try:
        data = request.json
        target_date = datetime.strptime(data.get('date'), '%Y-%m-%d').date()
        
        if check_future_date(target_date):
            return jsonify({'error': 'La date ne peut pas être dans le futur.'}), 400

        result = generate_global_daily_report(target_date)
        
        if not result['projects']:
            return jsonify({'error': 'Aucune donnée trouvée pour cette date'}), 404
        
        pdf_buffer = generate_global_daily_pdf(result)
        
        filename = f"Rapport_Global_Tous_Projets_{target_date}.pdf"
        
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 400
def generate_atelier_pdf(data):
    """Generate professional PDF for atelier performance."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import PageBreak, KeepTogether
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from datetime import datetime
    
    pdf_buffer = io.BytesIO()
    
    # Custom page template with header/footer
    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            canvas.Canvas.__init__(self, *args, **kwargs)
            self.pages = []
            
        def showPage(self):
            self.pages.append(dict(self.__dict__))
            self._startPage()
            
        def save(self):
            page_count = len(self.pages)
            for page_num, page in enumerate(self.pages, 1):
                self.__dict__.update(page)
                self.draw_page_elements(page_num, page_count)
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)
            
        def draw_page_elements(self, page_num, page_count):
            # Footer
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor('#64748b'))
            self.drawRightString(landscape(A4)[0] - 20*mm, 15*mm, f"Page {page_num}/{page_count}")
            self.drawString(20*mm, 15*mm, f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}")
    
    doc = SimpleDocTemplate(
        pdf_buffer, 
        pagesize=landscape(A4), 
        topMargin=15*mm, 
        bottomMargin=20*mm, 
        leftMargin=15*mm, 
        rightMargin=15*mm
    )
    
    styles = getSampleStyleSheet()
    
    # Soft, eye-comfortable color palette
    PRIMARY_DARK = colors.HexColor('#3b82f6')      # Bright blue (was very dark)
    PRIMARY = colors.HexColor('#60a5fa')           # Light blue
    ACCENT = colors.HexColor('#0ea5e9')            # Softer cyan blue
    ACCENT_LIGHT = colors.HexColor('#38bdf8')      # Light cyan
    SUCCESS = colors.HexColor('#22c55e')           # Soft green
    WARNING = colors.HexColor('#f59e0b')           # Amber (unchanged)
    DANGER = colors.HexColor('#ef4444')            # Red (unchanged)
    LIGHT_BG = colors.HexColor('#f1f5f9')          # Very light blue-gray
    BORDER = colors.HexColor('#cbd5e1')            # Soft gray border
    HEADER_BG = colors.HexColor('#0284c7')         # Medium blue for headers
    SECTION_HEADER = colors.HexColor('#1e40af')    # Deep blue for section titles
    
    # Custom styles
    title_style = ParagraphStyle(
        'ModernTitle',
        parent=styles['Heading1'],
        fontSize=22,
        textColor=colors.white,
        spaceAfter=0,
        fontName='Helvetica-Bold',
        alignment=TA_CENTER
    )
    
    subtitle_style = ParagraphStyle(
        'Subtitle',
        parent=styles['Normal'],
        fontSize=9,
        textColor=ACCENT_LIGHT,
        fontName='Helvetica',
        alignment=TA_CENTER
    )
    
    section_title_style = ParagraphStyle(
        'SectionTitle',
        parent=styles['Heading2'],
        fontSize=12,
        textColor=SECTION_HEADER,
        spaceBefore=8,
        spaceAfter=6,
        fontName='Helvetica-Bold',
        borderPadding=(0, 0, 0, 3),
        borderColor=ACCENT,
        borderWidth=0,
        leftIndent=0
    )
    
    card_title_style = ParagraphStyle(
        'CardTitle',
        parent=styles['Normal'],
        fontSize=9,
        textColor=colors.HexColor('#475569'),       # Darker gray for readability
        fontName='Helvetica-Bold',
        alignment=TA_CENTER,
        spaceAfter=2
    )
    
    card_value_style = ParagraphStyle(
        'CardValue',
        parent=styles['Normal'],
        fontSize=18,
        textColor=colors.HexColor('#1e293b'),       # Very dark gray (almost black)
        fontName='Helvetica-Bold',
        alignment=TA_CENTER
    )
    
    story = []
    
    # === PROFESSIONAL HEADER WITH COLOR BAR ===
    header_table_data = [[Paragraph('RAPPORT DE PERFORMANCE', title_style)]]
    header_table = Table(header_table_data, colWidths=[landscape(A4)[0] - 30*mm])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HEADER_BG),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('LEFTPADDING', (0, 0), (-1, -1), 20),
        ('RIGHTPADDING', (0, 0), (-1, -1), 20),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 3*mm))
    
    # === PROJECT INFO SECTION ===
    info_data = [
        [
            Paragraph(f"<b>Atelier:</b> {data['atelier_name']}", styles['Normal']),
            Paragraph(f"<b>Projet:</b> {data['project_name']}", styles['Normal'])
        ],
        [
            Paragraph(f"<b>Période:</b> {data['date_range']}", styles['Normal']),
            Paragraph(f"<b>Engins:</b> {data['vehicle_count']}", styles['Normal'])
        ]
    ]
    info_table = Table(info_data, colWidths=[(landscape(A4)[0] - 30*mm) / 2] * 2)
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_BG),
        ('TEXTCOLOR', (0, 0), (-1, -1), PRIMARY),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 15),
        ('RIGHTPADDING', (0, 0), (-1, -1), 15),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 6*mm))
    
    # === OVERVIEW CARDS SECTION ===
    story.append(Paragraph('VUE D\'ENSEMBLE (TRAVAIL)', section_title_style))
    story.append(Spacer(1, 2*mm))
    
    overview_data = [
        [
            Paragraph('Engins', card_title_style),
            Paragraph('Cycles', card_title_style),
            Paragraph('KM Travaillé', card_title_style),
            Paragraph('Hrs Travaillé', card_title_style)
        ],
        [
            Paragraph(f"<b>{data['vehicle_count']}</b>", card_value_style),
            Paragraph(f"<b>{data['total_trips']}</b>", card_value_style),
            Paragraph(f"<b>{data['total_km_work']}</b>", card_value_style),
            Paragraph(f"<b>{format_hours(data['working_hours'])}</b>", card_value_style)
        ]
    ]
    
    col_width = (landscape(A4)[0] - 30*mm) / 4
    overview_table = Table(overview_data, colWidths=[col_width] * 4, rowHeights=[8*mm, 12*mm])
    overview_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('BACKGROUND', (0, 1), (-1, 1), colors.white),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 1, BORDER),
        ('BOX', (0, 0), (-1, -1), 1.5, HEADER_BG),
    ]))
    story.append(overview_table)
    story.append(Spacer(1, 5*mm))
    
    
    
    
    # === VEHICLE DETAILS TABLE ===
    story.append(Paragraph('DÉTAILS DES ENGINS', section_title_style))
    story.append(Spacer(1, 2*mm))
    
    table_headers = ['Engin', 'Cycles', 'KM Travaillé', 'Hrs Travaillé', 'KM Hors Travail', "Nbr d'attente", "Durée d'attente"]
    table_data = [table_headers]
    
    for v in data['vehicles']:
        m_type = v.get('movement_type', 'Move')
        trips_display = str(v['trips']) if m_type == 'Move' else 'N/A'
        
        table_data.append([
            f"{v['name']}\n({v['id']})",
            trips_display,
            v['km'],
            format_hours(v['working_hours']),
            v.get('km_out_of_range', 0),
            v.get('attente_count', 0),
            format_hours(v.get('duration_attente', 0))
        ])
    
    # Adjust column widths for better fit
    total_width = landscape(A4)[0] - 30*mm
    vehicle_table = Table(table_data, colWidths=[
        total_width * 0.22,  # Engin
        total_width * 0.10,  # Cycles
        total_width * 0.14,  # KM Travaillé
        total_width * 0.14,  # Hrs
        total_width * 0.16,  # KM Hors
        total_width * 0.12,  # Nb Attente
        total_width * 0.12   # Durée
    ])
    
    vehicle_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 1), (0, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ('BOX', (0, 0), (-1, -1), 1, HEADER_BG),
    ]))
    story.append(vehicle_table)
    story.append(Spacer(1, 5*mm))
    

    
    # Build PDF with custom canvas
    try:
        doc.build(story, canvasmaker=NumberedCanvas)
    except:
        # Fallback without numbered canvas
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

        # Check for future dates
        if check_future_date(start_date) or check_future_date(end_date):
            return jsonify({'error': 'Les dates ne peuvent pas être dans le futur'}), 400
        
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
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    
    pdf_buffer = io.BytesIO()
    # Use landscape for better table fit
    doc = SimpleDocTemplate(pdf_buffer, pagesize=landscape(A4), 
                           topMargin=15*mm, bottomMargin=15*mm,
                           leftMargin=10*mm, rightMargin=10*mm)
    
    styles = getSampleStyleSheet()
    
    # Professional color palette
    HEADER_BG = colors.HexColor('#0284c7')      # Blue header
    SECTION_BG = colors.HexColor('#1e40af')     # Dark blue for section
    LIGHT_BG = colors.HexColor('#f1f5f9')       # Light gray background
    BORDER = colors.HexColor('#cbd5e1')         # Soft border
    WARNING_BG = colors.HexColor('#fef08a')     # Light yellow warning
    WARNING_TEXT = colors.HexColor('#854d0e')   # Dark amber text
    
    section_style = ParagraphStyle(
        'SectionTitle',
        parent=styles['Heading2'],
        fontSize=11,
        textColor=SECTION_BG,
        spaceBefore=8,
        spaceAfter=4,
        fontName='Helvetica-Bold'
    )
    
    # Cell text style for wrapping long names
    cell_style = ParagraphStyle(
        'CellStyle',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        alignment=TA_LEFT
    )
    
    story = []
    
    # Professional Header Bar
    header_data = [['RAPPORT D\'ACTIVITÉ QUOTIDIEN (TOUS LES ENGINS)']]
    header_table = Table(header_data, colWidths=[landscape(A4)[0] - 20*mm])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 16),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 3*mm))
    
    # Date info bar
    date_str = target_date.strftime('%d %B %Y').replace('January', 'Janvier').replace('February', 'Février').replace('March', 'Mars').replace('April', 'Avril').replace('May', 'Mai').replace('June', 'Juin').replace('July', 'Juillet').replace('August', 'Août').replace('September', 'Septembre').replace('October', 'Octobre').replace('November', 'Novembre').replace('December', 'Décembre')
    info_data = [[f'Date: {date_str}', f'Nombre de véhicules: {len(records)}']]
    info_table = Table(info_data, colWidths=[(landscape(A4)[0] - 20*mm) / 2] * 2)
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_BG),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 5*mm))
    
    # Group by category
    categories_dict = {}
    for record in records:
        vehicle = record.vehicle_code
        vehicle_obj = vehicles_dict.get(vehicle)
        category = vehicle_obj.category if vehicle_obj else 'Autres'
        
        if category not in categories_dict:
            categories_dict[category] = []
        categories_dict[category].append(record)
    
    # Calculate column widths for landscape A4 (842 points wide - margins)
    total_width = landscape(A4)[0] - 20*mm
    col_widths = [
        total_width * 0.08,   # ID (short)
        total_width * 0.25,   # Nom (needs space for long names)
        total_width * 0.15,   # Matricule
        total_width * 0.13,   # Avant KM
        total_width * 0.13,   # Avant Heures
        total_width * 0.13,   # Après KM
        total_width * 0.13,   # Après Heures
    ]
    
    # Create tables for each category
    for category in sorted(categories_dict.keys()):
        # Section header
        story.append(Paragraph(f'<b>{category}</b>', section_style))
        
        # Table headers - cleaner format
        table_data = [[
            'ID',
            'Véhicule / Conducteur',
            'Matricule',
            'Avant 18:30\n(KM)',
            'Avant 18:30\n(Heures)',
            'Après 18:30\n(KM)',
            'Après 18:30\n(Heures)'
        ]]
        
        highlight_rows = []      # For >20km (yellow + bold)
        attention_rows = []      # For any movement after hours (just bold)
        row_idx = 1
        
        for record in categories_dict[category]:
            vehicle_obj = vehicles_dict.get(record.vehicle_code)
            vehicle_name = vehicle_obj.name if vehicle_obj else '-'
            matricule = vehicle_obj.matricule if vehicle_obj else '-'
            
            # Check if after-hours KM exceeds 20 (yellow highlight)
            if record.km_after > 20:
                highlight_rows.append(row_idx)
            # Check if ANY movement after hours (bold only)
            elif record.km_after > 0:
                attention_rows.append(row_idx)
            
            # Use Paragraph for long text to enable wrapping
            table_data.append([
                record.vehicle_code,
                Paragraph(vehicle_name, cell_style),
                matricule,
                f"{record.km_before:.2f}",
                format_decimal_hours(record.hours_before_20h),
                f"{record.km_after:.2f}",
                format_decimal_hours(record.hours_after_20h)
            ])
            row_idx += 1
        
        # Add totals row
        total_km_before = sum(r.km_before for r in categories_dict[category])
        total_hours_before = sum(r.hours_before_20h for r in categories_dict[category])
        total_km_after = sum(r.km_after for r in categories_dict[category])
        total_hours_after = sum(r.hours_after_20h for r in categories_dict[category])
        
        table_data.append([
            'TOTAL', '', '',
            f"{total_km_before:.2f}",
            format_decimal_hours(total_hours_before),
            f"{total_km_after:.2f}",
            format_decimal_hours(total_hours_after)
        ])
        
        # Create and style table
        table = Table(table_data, colWidths=col_widths)
        
        table_styles = [
            # Header row
            ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            
            # Data rows
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('ALIGN', (0, 1), (0, -1), 'CENTER'),   # ID centered
            ('ALIGN', (1, 1), (1, -1), 'LEFT'),     # Name left
            ('ALIGN', (2, 1), (2, -1), 'CENTER'),   # Matricule centered
            ('ALIGN', (3, 1), (-1, -1), 'CENTER'),  # Numbers centered
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            
            # Totals row
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#d1d5db')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            
            # Grid and alternating rows
            ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, LIGHT_BG]),
            
            # Padding
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]
        
        # Add bold for rows with ANY movement after hours (attention)
        for row in attention_rows:
            table_styles.append(('FONTNAME', (0, row), (-1, row), 'Helvetica-Bold'))
        
        # Add warning highlighting for >20km after hours (yellow + bold)
        for row in highlight_rows:
            table_styles.append(('BACKGROUND', (0, row), (-1, row), WARNING_BG))
            table_styles.append(('TEXTCOLOR', (0, row), (-1, row), WARNING_TEXT))
            table_styles.append(('FONTNAME', (0, row), (-1, row), 'Helvetica-Bold'))
        
        table.setStyle(TableStyle(table_styles))
        
        story.append(table)
        story.append(Spacer(1, 4*mm))
    
    # Footer note
    note_style = ParagraphStyle('Note', parent=styles['Normal'], fontSize=7, textColor=colors.HexColor('#64748b'))
    story.append(Paragraph('* Lignes jaunes = véhicules avec plus de 20 km après 18:30', note_style))
    
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


def format_hours(decimal_hours):
    """Convert decimal hours (e.g. 6.63) to Xh YYmin format (e.g. 6h 38min)."""
    hours = int(decimal_hours)
    minutes = int(round((decimal_hours - hours) * 60))
    if hours == 0 and minutes == 0:
        return "0h 00min"
    return f"{hours}h {minutes:02d}min"

def generate_project_atelier_daily_pdf(data):
    """Generate professional daily activity PDF for project/atelier."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import KeepTogether
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from datetime import datetime
    
    pdf_buffer = io.BytesIO()
    
    # Custom canvas for footer
    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            canvas.Canvas.__init__(self, *args, **kwargs)
            self.pages = []
            
        def showPage(self):
            self.pages.append(dict(self.__dict__))
            self._startPage()
            
        def save(self):
            page_count = len(self.pages)
            for page_num, page in enumerate(self.pages, 1):
                self.__dict__.update(page)
                self.draw_page_elements(page_num, page_count)
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)
            
        def draw_page_elements(self, page_num, page_count):
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor('#64748b'))
            self.drawRightString(landscape(A4)[0] - 20*mm, 15*mm, f"Page {page_num}/{page_count}")
            self.drawString(20*mm, 15*mm, f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}")
    
    doc = SimpleDocTemplate(
        pdf_buffer,
        pagesize=landscape(A4),
        topMargin=15*mm,
        bottomMargin=20*mm,
        leftMargin=15*mm,
        rightMargin=15*mm
    )
    
    styles = getSampleStyleSheet()
    
    # Colors
    HEADER_BG = colors.HexColor('#1e293b') # Dark slate for main header
    PROJECT_HEADER_BG = colors.HexColor('#0284c7') # Royal blue for sub-header
    BORDER = colors.HexColor('#cbd5e1')
    
    # Styles
    title_style = ParagraphStyle(
        'Title',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.white,
        spaceAfter=0,
        fontName='Helvetica-Bold',
        alignment=TA_CENTER
    )
    
    subtitle_style = ParagraphStyle(
        'Subtitle',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.HexColor('#334155'),
        fontName='Helvetica',
        alignment=TA_LEFT,
        spaceBefore=3,
        spaceAfter=3
    )
    
    table_header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontSize=9,
        textColor=colors.white,
        fontName='Helvetica-Bold'
    )
    
    total_table_style = ParagraphStyle(
        'TotalTable',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.HexColor('#1e293b'),
        fontName='Helvetica-Bold',
        alignment=TA_CENTER
    )
    
    atelier_title_style = ParagraphStyle(
        'AtelierTitle',
        parent=styles['Normal'],
        fontSize=11,
        textColor=colors.HexColor('#0369a1'),
        fontName='Helvetica-Bold',
        spaceBefore=10,
        spaceAfter=5
    )
    
    story = []
    
    # Header
    header_data = [['RAPPORT D\'ACTIVITÉ QUOTIDIEN (TOUS LES ENGINS)']]
    header_table = Table(header_data, colWidths=[landscape(A4)[0] - 30*mm])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 18),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 8*mm))
    
    # Info section
    date_str = data['date'].strftime('%d/%m/%Y')
    info_text = f"""
    <b>Projet:</b> {data['project_name']} - {data['province']}<br/>
    <b>Atelier:</b> {data['atelier_name']}<br/>
    <b>Date:</b> {date_str}
    """
    story.append(Paragraph(info_text, subtitle_style))
    story.append(Spacer(1, 5*mm))
    
    # Summary Box
    summary_data = [[
        Paragraph(f"<b>{data['vehicle_count']}</b> Engins", subtitle_style),
        Paragraph(f"<b>{format_hours(data['total_working_hours'])}</b> Heures Travaillées", subtitle_style),
        Paragraph(f"<b>{data['total_km']} km</b> Distance Totale", subtitle_style)
    ]]
    summary_table = Table(summary_data, colWidths=[(landscape(A4)[0] - 30*mm) / 3] * 3)
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f1f5f9')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('BOX', (0, 0), (-1, -1), 1, BORDER),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 7*mm))
    
    # Loop through Ateliers
    if data.get('ateliers'):
        for atelier in data['ateliers']:
            # Atelier Header
            story.append(Spacer(1, 5*mm))
            story.append(Paragraph(f"<b>ATELIER: {atelier['atelier_name']}</b>", atelier_title_style))
            story.append(Spacer(1, 3*mm))
            
            table_data = [[
                Paragraph('<b>Véhicule</b>', table_header_style),
                Paragraph('<b>Matricule</b>', table_header_style),
                Paragraph('<b>Avant 18:30 (KM)</b>', table_header_style),
                Paragraph('<b>H. Travail (Avant 18:30)</b>', table_header_style),
                Paragraph('<b>Après 18:30 (KM)</b>', table_header_style),
                Paragraph('<b>Total KM</b>', table_header_style),
                Paragraph('<b>Cycles</b>', table_header_style)
            ]]
            
            for v in atelier['vehicles']:
                cycles_val = v['cycles']
                category = v.get('category', '').lower()
                
                # STRICT LOGIC: Only 'camion' shows cycles.
                display_cycles = "N/A"
                if category == 'camion':
                    display_cycles = f"{cycles_val}"
                    
                table_data.append([
                    v['id'],
                    v.get('matricule', '-'),
                    f"{v['km_before']}",
                    format_hours(v['working_hours_before']),
                    f"{v['km_after']}",
                    f"{v['total_km']}",
                    display_cycles
                ])
            
            # Add atelier totals row
            table_data.append([
                Paragraph('<b>TOTAL ATELIER</b>', total_table_style),
                '',
                Paragraph(f"<b>{atelier['total_km_before']}</b>", total_table_style),
                Paragraph(f"<b>{format_hours(atelier['total_working_hours_before'])}</b>", total_table_style),
                Paragraph(f"<b>{atelier['total_km_after']}</b>", total_table_style),
                Paragraph(f"<b>{atelier['total_km']}</b>", total_table_style),
                Paragraph(f"<b>{atelier['total_cycles']}</b>", total_table_style)
            ])
            
            col_widths = [50*mm, 40*mm, 35*mm, 35*mm, 35*mm, 30*mm, 35*mm]
            vehicle_table = Table(table_data, colWidths=col_widths, repeatRows=1)
            vehicle_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), PROJECT_HEADER_BG),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
                ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e2e8f0')), # Slightly darker grey
                ('TEXTCOLOR', (0, -1), (-1, -1), colors.HexColor('#1e293b')),
                ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ]))
            story.append(vehicle_table)
            story.append(Spacer(1, 5*mm))
    else:
        story.append(Spacer(1, 10*mm))
        story.append(Paragraph("Aucune activité enregistrée pour cette date.", subtitle_style))
    
    doc.build(story, canvasmaker=NumberedCanvas)
    pdf_buffer.seek(0)
    return pdf_buffer


def generate_global_daily_pdf(data):
    """Generate professional daily activity PDF for ALL projects and ateliers."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import KeepTogether
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from datetime import datetime
    
    pdf_buffer = io.BytesIO()
    
    # Custom canvas for footer (Reused from project report)
    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            canvas.Canvas.__init__(self, *args, **kwargs)
            self.pages = []
            
        def showPage(self):
            self.pages.append(dict(self.__dict__))
            self._startPage()
            
        def save(self):
            page_count = len(self.pages)
            for page_num, page in enumerate(self.pages, 1):
                self.__dict__.update(page)
                self.draw_page_elements(page_num, page_count)
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)
            
        def draw_page_elements(self, page_num, page_count):
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor('#64748b'))
            self.drawRightString(landscape(A4)[0] - 20*mm, 15*mm, f"Page {page_num}/{page_count}")
            self.drawString(20*mm, 15*mm, f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}")
    
    doc = SimpleDocTemplate(
        pdf_buffer,
        pagesize=landscape(A4),
        topMargin=15*mm,
        bottomMargin=20*mm,
        leftMargin=15*mm,
        rightMargin=15*mm
    )
    
    styles = getSampleStyleSheet()
    
    # Colors
    HEADER_BG = colors.HexColor('#1e293b') # Darker for global
    PROJECT_HEADER_BG = colors.HexColor('#0284c7') # Blue for project sections
    ATELIER_SUBHEADER_BG = colors.HexColor('#f8f9fa')
    BORDER = colors.HexColor('#cbd5e1')
    
    # Styles
    title_style = ParagraphStyle(
        'Title',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.white,
        spaceAfter=0,
        fontName='Helvetica-Bold',
        alignment=TA_CENTER
    )
    
    section_title_style = ParagraphStyle(
        'SectionTitle',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.white,
        fontName='Helvetica-Bold',
        alignment=TA_LEFT,
        leftIndent=5*mm
    )
    
    atelier_title_style = ParagraphStyle(
        'AtelierTitle',
        parent=styles['Normal'],
        fontSize=11,
        textColor=colors.HexColor('#0369a1'),
        fontName='Helvetica-Bold',
        spaceBefore=10,
        spaceAfter=5
    )
    
    subtitle_style = ParagraphStyle(
        'Subtitle',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.HexColor('#334155'),
        fontName='Helvetica',
        alignment=TA_LEFT,
        spaceBefore=3,
        spaceAfter=3
    )
    
    table_header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontSize=9,
        textColor=colors.white,
        fontName='Helvetica-Bold'
    )
    
    story = []
    
    # Global Header
    header_data = [['RAPPORT D\'ACTIVITÉ QUOTIDIEN - TOUS LES PROJETS']]
    header_table = Table(header_data, colWidths=[landscape(A4)[0] - 30*mm])
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 20),
        ('TOPPADDING', (0, 0), (-1, -1), 15),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 15),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 10*mm))
    
    # Global Summary Box
    date_str = data['date'].strftime('%d/%m/%Y')
    summary_data = [
        [Paragraph(f"<b>Date:</b> {date_str}", subtitle_style), '', ''],
        [
            Paragraph(f"<b>{data['total_active_vehicles']}</b> Engins", subtitle_style),
            Paragraph(f"<b>{format_hours(data['total_working_hours'])}</b> Heures Travaillées", subtitle_style),
            Paragraph(f"<b>{data['total_km']} km</b> Distance Totale", subtitle_style)
        ]
    ]
    summary_table = Table(summary_data, colWidths=[(landscape(A4)[0] - 30*mm) / 3] * 3)
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
        ('SPAN', (0, 0), (2, 0)),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOX', (0, 0), (-1, -1), 1.5, HEADER_BG),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 15*mm))
    
    # Loop through Projects
    for project in data['projects']:
        # Project Title Bar
        p_header = [[Paragraph(f"PROJET: {project['project_name']} ({project['province']})", section_title_style)]]
        p_table = Table(p_header, colWidths=[landscape(A4)[0] - 30*mm])
        p_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), PROJECT_HEADER_BG),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(KeepTogether(p_table))
        
        # Loop through Ateliers in Project
        for atelier in project['ateliers']:
            # Atelier name
            story.append(Paragraph(f"Atelier: {atelier['atelier_name']}", atelier_title_style))
            
            # Vehicle table for this atelier
            if atelier['vehicles']:
                table_data = [[
                    Paragraph('<b>Véhicule</b>', table_header_style),
                    Paragraph('<b>Matricule</b>', table_header_style),
                    Paragraph('<b>Avant 18:30 (KM)</b>', table_header_style),
                    Paragraph('<b>H. Travail (Avant 18:30)</b>', table_header_style),
                    Paragraph('<b>Après 18:30 (KM)</b>', table_header_style),
                    Paragraph('<b>Total KM</b>', table_header_style),
                    Paragraph('<b>Cycles</b>', table_header_style)
                ]]
                
                for v in atelier['vehicles']:
                    cycles_val = v['cycles']
                    category = v.get('category', '').lower()
                    
                    # STRICT LOGIC: Only 'camion' shows cycles.
                    display_cycles = "N/A"
                    if category == 'camion':
                        display_cycles = f"{cycles_val}"
                        
                    table_data.append([
                        v['id'],
                        v.get('matricule', '-'),
                        f"{v['km_before']}",
                        format_hours(v['working_hours_before']),
                        f"{v['km_after']}",
                        f"{v['total_km']}",
                        display_cycles
                    ])
                
                # Atelier Totals Row
                table_data.append([
                    Paragraph('<b>TOTAL ATELIER</b>', total_table_style),
                    '',
                    Paragraph(f"<b>{atelier['km_before']}</b>", total_table_style),
                    Paragraph(f"<b>{format_hours(atelier['working_hours_before'])}</b>", total_table_style),
                    Paragraph(f"<b>{atelier['km_after']}</b>", total_table_style),
                    Paragraph(f"<b>{atelier['total_km']}</b>", total_table_style),
                    Paragraph(f"<b>{atelier['cycles']}</b>", total_table_style)
                ])
                
                col_widths = [50*mm, 40*mm, 35*mm, 35*mm, 35*mm, 30*mm, 35*mm]
                v_table = Table(table_data, colWidths=col_widths, repeatRows=1)
                v_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), PROJECT_HEADER_BG),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('FONTSIZE', (0, 0), (-1, -1), 8),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                    ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
                    ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e2e8f0')),
                    ('TEXTCOLOR', (0, -1), (-1, -1), colors.HexColor('#1e293b')),
                    ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                ]))
                story.append(v_table)
                story.append(Spacer(1, 5*mm))
        
        story.append(Spacer(1, 10*mm)) # Gap between projects
    
    doc.build(story, canvasmaker=NumberedCanvas)
    pdf_buffer.seek(0)
    return pdf_buffer


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

