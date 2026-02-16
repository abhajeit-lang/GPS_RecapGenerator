"""
Report generation functions for hierarchy-based analytics.
New reports: Comparative Atelier, Atelier Performance, Vehicle Efficiency, Project Overview
"""
from datetime import datetime, date
from models import db, VehicleActivity, Vehicle, Atelier, Project
from sqlalchemy import func

def get_date_range_data(atelier_id, start_date, end_date):
    """
    Get aggregated metrics for an atelier over a date range.
    
    Returns dict with:
    - total_trips, total_km, total_working_time, total_course_time,
    - total_attente_time, total_arret_time, vehicle_count, vehicles_data
    """
    # Get vehicles in this atelier
    vehicles = Vehicle.query.filter_by(atelier_id=atelier_id).all()
    vehicle_ids = [v.id for v in vehicles]
    
    if not vehicle_ids:
        return None
    
    # Query activities for these vehicles in date range
    activities = VehicleActivity.query.filter(
        VehicleActivity.vehicle_code.in_(vehicle_ids),
        VehicleActivity.date >= start_date,
        VehicleActivity.date <= end_date
    ).all()
    
    # Aggregate metrics
    # Smart cycle detection now handles fractional cycles (0.5 or 1.0)
    # No need to divide by 2 anymore
    total_trips = sum(a.trip_count for a in activities)
    total_trips = round(total_trips, 1)  # Round to 1 decimal for display
    
    total_km_raw = sum(a.km_before + a.km_after for a in activities)
    total_km_out = sum(a.km_out_of_range for a in activities)
    total_km_work = total_km_raw - total_km_out
    
    total_course = sum(a.duration_course for a in activities)  # seconds
    total_attente = sum(a.duration_attente for a in activities)
    total_arret = sum(a.duration_arret for a in activities)
    
    total_working_time = total_course + total_attente  # seconds
    total_time = total_working_time + total_arret
    
    # Per-vehicle breakdown
    vehicles_data = []
    for v in vehicles:
        v_activities = [a for a in activities if a.vehicle_code == v.id]
        if v_activities:
            v_trips = sum(a.trip_count for a in v_activities)
            v_trips = round(v_trips, 1)  # Already fractional from smart detection
            
            v_km_total = sum(a.km_before + a.km_after for a in v_activities)
            v_km_out_of_range = sum(a.km_out_of_range for a in v_activities)
            v_km_in_range = v_km_total - v_km_out_of_range
            
            v_attente_count = sum(a.attente_count for a in v_activities)
            v_course = sum(a.duration_course for a in v_activities)
            v_attente = sum(a.duration_attente for a in v_activities)
            v_arret = sum(a.duration_arret for a in v_activities)
            v_working = v_course + v_attente
            v_total = v_working + v_arret
            
            vehicles_data.append({
                'id': v.id,
                'name': v.name,
                'movement_type': v.movement_type or 'Move',
                'trips': v_trips,
                'attente_count': v_attente_count,
                'duration_attente': round(v_attente, 2),
                'km': round(v_km_in_range, 2),
                'km_out_of_range': round(v_km_out_of_range, 2),
                'working_hours': round(v_working, 2),
                'efficiency': round((v_working / v_total * 100) if v_total > 0 else 0, 1),
                'utilization': round((v_course / v_working * 100) if v_working > 0 else 0, 1)
            })
    
    # Recalculate avg distance based on LEGS or CYCLES? 
    # avg distance per Cycle = Total Work KM / Total Cycles
    avg_trip_distance = round(total_km_work / total_trips, 2) if total_trips > 0 else 0
    efficiency_rate = round((total_working_time / total_time * 100) if total_time > 0 else 0, 1)
    utilization_rate = round((total_course / total_working_time * 100) if total_working_time > 0 else 0, 1)
    
    # Find best producer (max cycles) and lowest activity (min cycles > 0)
    best_producer = "N/A"
    lowest_activity = "N/A"
    
    if vehicles_data:
        # Best producer (highest trip count)
        max_vehicle = max(vehicles_data, key=lambda v: v['trips'])
        best_producer = f"{max_vehicle['id']} ({max_vehicle['trips']} cycles)"
        
        # Lowest activity (lowest trip count, excluding 0)
        active_vehicles = [v for v in vehicles_data if v['trips'] > 0]
        if active_vehicles:
            min_vehicle = min(active_vehicles, key=lambda v: v['trips'])
            lowest_activity = f"{min_vehicle['id']} ({min_vehicle['trips']} cycles)"
    
    return {
        'atelier_id': atelier_id,
        'vehicle_count': len(vehicles),
        'total_trips': total_trips, # Now represents Cycles
        'total_km': round(total_km_raw, 2), # Show raw total in overview
        'total_km_work': round(total_km_work, 2),
        'total_km_out': round(total_km_out, 2),
        'avg_trip_distance': avg_trip_distance,
        'working_hours': round(total_working_time, 2),
        'course_hours': round(total_course, 2),
        'attente_hours': round(total_attente, 2),
        'arret_hours': round(total_arret, 2),
        'efficiency_rate': efficiency_rate,
        'utilization_rate': utilization_rate,
        'best_producer': best_producer,
        'lowest_activity': lowest_activity,
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
