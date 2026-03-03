"""
Report generation functions for hierarchy-based analytics.
New reports: Comparative Atelier, Atelier Performance, Vehicle Efficiency, Project Overview
"""
from datetime import datetime, date
from models import db, VehicleActivity, Vehicle, Atelier, Project, RealWorkData
from sqlalchemy import func

def get_date_range_data(atelier_id, start_date, end_date):
    """
    Get aggregated metrics for an atelier over a date range, including real work hours.
    """
    # Get vehicles in this atelier
    vehicles = Vehicle.query.filter_by(atelier_id=atelier_id).all()
    vehicle_ids = [v.id for v in vehicles]
    
    if not vehicle_ids:
        return None
    
    # Query activities
    activities = VehicleActivity.query.filter(
        VehicleActivity.vehicle_code.in_(vehicle_ids),
        VehicleActivity.date >= start_date,
        VehicleActivity.date <= end_date
    ).all()

    # Query REAL work data
    real_data_records = RealWorkData.query.filter(
        RealWorkData.vehicle_id.in_(vehicle_ids),
        RealWorkData.date >= start_date,
        RealWorkData.date <= end_date
    ).all()
    
    # Map real hours by vehicle
    real_hours_map = {}
    for r in real_data_records:
        if r.vehicle_id not in real_hours_map:
            real_hours_map[r.vehicle_id] = 0.0
        real_hours_map[r.vehicle_id] += r.hours_real

    # Aggregate metrics
    total_trips = sum(a.trip_count for a in activities)
    total_trips = round(total_trips, 1)
    
    total_km_raw = sum(a.km_before + a.km_after for a in activities)
    total_km_out = sum(a.km_out_of_range for a in activities)
    total_km_work = total_km_raw - total_km_out
    
    total_course = sum(a.duration_course for a in activities)
    total_attente = sum(a.duration_attente for a in activities)
    total_arret = sum(a.duration_arret for a in activities)
    
    total_working_time = total_course + total_attente
    total_time = total_working_time + total_arret
    
    total_real_hours = sum(real_hours_map.values())
    
    # Per-vehicle breakdown
    vehicles_data = []
    for v in vehicles:
        v_activities = [a for a in activities if a.vehicle_code == v.id]
        v_real_h = real_hours_map.get(v.id, 0.0)
        
        if v_activities or v_real_h > 0:
            v_trips = sum(a.trip_count for a in v_activities)
            v_km_total = sum(a.km_before + a.km_after for a in v_activities)
            v_km_out = sum(a.km_out_of_range for a in v_activities)
            v_working = sum(a.duration_course + a.duration_attente for a in v_activities)
            v_total = v_working + sum(a.duration_arret for a in v_activities)
            
            discrepancy = v_working - v_real_h
            
            vehicles_data.append({
                'id': v.id,
                'name': v.name,
                'category': v.category or 'Autre',
                'movement_type': v.movement_type or 'Move',
                'trips': round(v_trips, 1),
                'km': round(v_km_total - v_km_out, 2),
                'total_km': round(v_km_total, 2),
                'working_hours': round(v_working, 2),
                'real_hours': round(v_real_h, 2),
                'discrepancy': round(discrepancy, 2),
                'efficiency': round((v_working / v_total * 100) if v_total > 0 else 0, 1)
            })
    
    avg_trip_distance = round(total_km_work / total_trips, 2) if total_trips > 0 else 0
    efficiency_rate = round((total_working_time / total_time * 100) if total_time > 0 else 0, 1)
    
    return {
        'atelier_id': atelier_id,
        'vehicle_count': len(vehicles),
        'total_trips': total_trips,
        'total_km': round(total_km_raw, 2),
        'total_km_work': round(total_km_work, 2),
        'total_km_out': round(total_km_out, 2),
        'avg_trip_distance': avg_trip_distance,
        'working_hours': round(total_working_time, 2),
        'real_hours': round(total_real_hours, 2),
        'discrepancy': round(total_working_time - total_real_hours, 2),
        'course_hours': round(total_course, 2),
        'attente_hours': round(total_attente, 2),
        'arret_hours': round(total_arret, 2),
        'efficiency_rate': efficiency_rate,
        'vehicles': vehicles_data
    }


def generate_comparative_report(atelier_ids, start_date, end_date):
    """
    Generate comparative analysis for multiple ateliers.
    Returns list of dicts, one per atelier with comparative metrics.
    """
    results = []
    
    for atelier_id in atelier_ids:
        atelier = Atelier.query.get(atelier_id)
        if not atelier:
            continue
            
        data = get_date_range_data(atelier_id, start_date, end_date)
        if data:
            data['atelier_name'] = atelier.name
            data['project_name'] = atelier.project.name if atelier.project else 'N/A'
            results.append(data)
    
    # Sort by efficiency descending
    results.sort(key=lambda x: x['efficiency_rate'], reverse=True)
    
    return results


def generate_atelier_performance_report(atelier_id, start_date, end_date):
    """
    Detailed performance report for a single atelier.
    """
    atelier = Atelier.query.get(atelier_id)
    if not atelier:
        return None
    
    data = get_date_range_data(atelier_id, start_date, end_date)
    if not data:
        return None
    
    data['atelier_name'] = atelier.name
    data['project_name'] = atelier.project.name if atelier.project else 'N/A'
    data['project_id'] = atelier.project.id if atelier.project else 'Unknown'
    data['date_range'] = f"{start_date} to {end_date}"
    
    return data


def generate_project_overview(project_id, start_date, end_date):
    """
    High-level overview for a project (all its ateliers).
    """
    project = Project.query.get(project_id)
    if not project:
        return None
    
    ateliers_data = []
    total_trips = 0
    total_working = 0
    
    for atelier in project.ateliers:
        data = get_date_range_data(atelier.id, start_date, end_date)
        if data:
            data['atelier_name'] = atelier.name
            ateliers_data.append(data)
            total_trips += data['total_trips']
            total_working += data['working_hours']
    
    # Calculate percentages
    for data in ateliers_data:
        data['pct_trips'] = round((data['total_trips'] / total_trips * 100) if total_trips > 0 else 0, 1)
        data['pct_working'] = round((data['working_hours'] / total_working * 100) if total_working > 0 else 0, 1)
    
    # Sort by efficiency
    ateliers_data.sort(key=lambda x: x['efficiency_rate'], reverse=True)
    
    return {
        'project_name': project.name,
        'project_id': project_id,
        'total_ateliers': len(project.ateliers),
        'total_vehicles': sum(len(a.vehicles) for a in project.ateliers),
        'total_trips': total_trips,
        'total_working_hours': round(total_working, 2),
        'ateliers': ateliers_data,
        'date_range': f"{start_date} to {end_date}"
    }


def generate_project_atelier_daily_report(project_id, atelier_id, target_date):
    """
    Generate daily activity report for specific project/atelier.
    Shows vehicles with movement before/after 18:30 similar to daily activity report.
    """
    project = Project.query.get(project_id)
    if not project:
        return None
        
    target_ateliers = []
    if atelier_id and int(atelier_id) > 0:
        atelier = Atelier.query.get(atelier_id)
        if not atelier or atelier.project_id != project_id:
            return None
        target_ateliers = [atelier]
        display_name = atelier.name
    else:
        target_ateliers = project.ateliers
        display_name = "Tous les Ateliers"
        atelier_id = 0
    
    if not target_ateliers:
        return None

    ateliers_data = []
    total_km_before = 0
    total_km_after = 0
    total_cycles = 0
    total_working_hours = 0
    total_working_hours_before = 0
    total_real_hours_all = 0
    total_vehicles = 0
    
    for atelier in target_ateliers:
        vehicles = atelier.vehicles
        if not vehicles:
            continue
            
        vehicle_ids = [v.id for v in vehicles]
        activities = VehicleActivity.query.filter(
            VehicleActivity.vehicle_code.in_(vehicle_ids),
            VehicleActivity.date == target_date
        ).all()
        
        atelier_vehicles = []
        atelier_km_before = 0
        atelier_km_after = 0
        atelier_cycles = 0
        atelier_working_hours = 0
        atelier_working_hours_before = 0
        atelier_real_hours = 0
        
        # Fetch RealWorkData for this date and atelier
        real_data_records = RealWorkData.query.filter(
            RealWorkData.vehicle_id.in_(vehicle_ids),
            RealWorkData.date == target_date
        ).all()
        real_hours_map = {r.vehicle_id: r.hours_real for r in real_data_records}
        
        for v in vehicles:
            v_activity = next((a for a in activities if a.vehicle_code == v.id), None)
            real_h = real_hours_map.get(v.id, 8.0) # Default to 8.0 if no entry? 
            # User said "make 8h the value as default". 
            # If there's no record in db, should we show 8h? 
            # In get_real_work_data route, we return 8.0 as default.
            
            km_before = 0
            km_after = 0
            cycles = 0
            working_hours = 0
            working_hours_before = 0
            
            if v_activity:
                km_before = v_activity.km_before
                km_after = v_activity.km_after
                cycles = v_activity.trip_count
                working_hours = v_activity.duration_course + v_activity.duration_attente
                working_hours_before = v_activity.hours_before_20h
                
                atelier_km_before += km_before
                atelier_km_after += km_after
                atelier_cycles += cycles
                atelier_working_hours += working_hours
                atelier_working_hours_before += working_hours_before
                
            atelier_vehicles.append({
                'id': v.id,
                'name': v.name,
                'matricule': v.matricule,
                'category': v.category,
                'km_before': round(km_before, 2),
                'working_hours_before': round(working_hours_before, 2),
                'km_after': round(km_after, 2),
                'total_km': round(km_before + km_after, 2),
                'cycles': round(cycles, 1),
                'working_hours': round(working_hours, 2),
                'real_hours': round(real_h, 2),
                'discrepancy': round(working_hours - real_h, 2)
            })
            atelier_real_hours += real_h
            
        # Sort vehicles A-Z
        atelier_vehicles.sort(key=lambda x: x['id'])
        
        ateliers_data.append({
            'atelier_name': atelier.name,
            'vehicles': atelier_vehicles,
            'total_km_before': round(atelier_km_before, 2),
            'total_km_after': round(atelier_km_after, 2),
            'total_km': round(atelier_km_before + atelier_km_after, 2),
            'total_cycles': round(atelier_cycles, 1),
            'total_working_hours': round(atelier_working_hours, 2),
            'total_working_hours_before': round(atelier_working_hours_before, 2),
            'total_real_hours': round(atelier_real_hours, 2)
        })
        
        total_km_before += atelier_km_before
        total_km_after += atelier_km_after
        total_cycles += atelier_cycles
        total_working_hours += atelier_working_hours
        total_working_hours_before += atelier_working_hours_before
        total_real_hours_all += atelier_real_hours
        total_vehicles += len(vehicles)

    if not ateliers_data:
        return None

    return {
        'project_id': project_id,
        'project_name': project.name,
        'province': project.province or 'N/A',
        'atelier_id': atelier_id,
        'atelier_name': display_name,
        'date': target_date,
        'ateliers': ateliers_data,
        'total_km_before': round(total_km_before, 2),
        'total_km_after': round(total_km_after, 2),
        'total_km': round(total_km_before + total_km_after, 2),
        'total_cycles': round(total_cycles, 1),
        'total_working_hours': round(total_working_hours, 2),
        'total_working_hours_before': round(total_working_hours_before, 2),
        'total_real_hours': round(total_real_hours_all, 2),
        'vehicle_count': total_vehicles
    }


def generate_global_daily_report(target_date):
    """
    Generate daily activity report for ALL projects and ALL ateliers.
    Groups results by Project -> Atelier.
    """
    projects = Project.query.all()
    
    global_data = {
        'date': target_date,
        'projects': [],
        'total_km_before': 0,
        'total_km_after': 0,
        'total_km': 0,
        'total_working_hours': 0,
        'total_active_vehicles': 0
    }
    
    for project in projects:
        project_data = {
            'project_id': project.id,
            'project_name': project.name,
            'province': project.province or 'N/A',
            'ateliers': []
        }
        
        for atelier in project.ateliers:
            vehicles = Vehicle.query.filter_by(atelier_id=atelier.id).all()
            if not vehicles:
                continue
                
            vehicle_ids = [v.id for v in vehicles]
            activities = VehicleActivity.query.filter(
                VehicleActivity.vehicle_code.in_(vehicle_ids),
                ).all()
            
            # Fetch RealWorkData
            real_data_records = RealWorkData.query.filter(
                RealWorkData.vehicle_id.in_(vehicle_ids),
                RealWorkData.date == target_date
            ).all()
            real_hours_map = {r.vehicle_id: r.hours_real for r in real_data_records}
            
            # Removed "if not activities: continue" to show ateliers with no recorded activity
                
            atelier_data = {
                'atelier_name': atelier.name,
                'vehicles': [],
                'km_before': 0,
                'km_after': 0,
                'total_km': 0,
                'total_working_hours': 0,
                'working_hours_before': 0,
                'cycles': 0,
                'real_hours': 0
            }
            
            for v in vehicles:
                v_activity = next((a for a in activities if a.vehicle_code == v.id), None)
                
                km_b = 0
                km_a = 0
                working_hours = 0
                working_hours_before = 0
                cyc = 0
                
                if v_activity:
                    km_b = v_activity.km_before
                    km_a = v_activity.km_after
                    working_hours = v_activity.duration_course + v_activity.duration_attente
                    working_hours_before = v_activity.hours_before_20h  # Using hours_before_20h as proxy for before 18:30
                    cyc = v_activity.trip_count
                    
                atelier_data['vehicles'].append({
                    'id': v.id,
                    'name': v.name,
                    'matricule': v.matricule,
                    'category': v.category,
                    'km_before': round(km_b, 2),
                    'working_hours_before': round(working_hours_before, 2),
                    'km_after': round(km_a, 2),
                    'total_km': round(km_b + km_a, 2),
                    'cycles': round(cyc, 1),
                    'working_hours': round(working_hours, 2),
                    'real_hours': round(real_hours_map.get(v.id, 8.0), 2),
                    'discrepancy': round(working_hours - real_hours_map.get(v.id, 8.0), 2)
                })
                
                # Accumulate totals
                v_real = real_hours_map.get(v.id, 8.0)
                atelier_data['km_before'] += km_b
                atelier_data['km_after'] += km_a
                atelier_data['total_km'] += (km_b + km_a)
                atelier_data['total_working_hours'] += working_hours
                atelier_data['working_hours_before'] += working_hours_before
                atelier_data['cycles'] += cyc
                atelier_data['real_hours'] += v_real
                    
            if atelier_data['vehicles']:
                # Sort vehicles alphabetically by ID
                atelier_data['vehicles'].sort(key=lambda x: x['id'])
                
                # Round atelier totals
                atelier_data['km_before'] = round(atelier_data['km_before'], 2)
                atelier_data['km_after'] = round(atelier_data['km_after'], 2)
                atelier_data['total_km'] = round(atelier_data['total_km'], 2)
                atelier_data['total_working_hours'] = round(atelier_data['total_working_hours'], 2)
                atelier_data['working_hours_before'] = round(atelier_data['working_hours_before'], 2)
                atelier_data['cycles'] = round(atelier_data['cycles'], 1)
                atelier_data['real_hours'] = round(atelier_data['real_hours'], 2)
                
                project_data['ateliers'].append(atelier_data)
                
                # Update global totals
                global_data['total_km_before'] += atelier_data['km_before']
                global_data['total_km_after'] += atelier_data['km_after']
                global_data['total_km'] += atelier_data['total_km']
                global_data['total_working_hours'] += atelier_data['total_working_hours']
                global_data['total_active_vehicles'] += len(vehicles) # Count all assigned vehicles
                
        if project_data['ateliers']:
            global_data['projects'].append(project_data)
            
    # Round global totals
    global_data['total_km_before'] = round(global_data['total_km_before'], 2)
    global_data['total_km_after'] = round(global_data['total_km_after'], 2)
    global_data['total_km'] = round(global_data['total_km'], 2)
    global_data['total_working_hours'] = round(global_data['total_working_hours'], 2)
    
    return global_data

