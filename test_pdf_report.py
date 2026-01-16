from app import generate_atelier_pdf
import io

def test_pdf_generation():
    # Mock Data
    data = {
        'atelier_name': 'Test Atelier',
        'project_name': 'Test Project',
        'date_range': '2026-01-01 to 2026-01-31',
        'vehicle_count': 2,
        'total_trips': 10,
        'total_km': 100.5,
        'avg_trip_distance': 10.05,
        'working_hours': 50.0,
        'efficiency_rate': 85.0,
        'utilization_rate': 70.0,
        'vehicles': [
            {
                'id': 'V01', 'name': 'Truck', 'movement_type': 'Move',
                'trips': 10, 'km': 100.0, 'working_hours': 40.0,
                'efficiency': 90.0, 'utilization': 80.0
            },
            {
                'id': 'V02', 'name': 'Excavator', 'movement_type': 'Sur place',
                'trips': 0, 'km': 0.5, 'working_hours': 10.0,
                'efficiency': 60.0, 'utilization': 30.0
            }
        ]
    }
    
    try:
        print("Generating PDF...")
        pdf_buffer = generate_atelier_pdf(data)
        content = pdf_buffer.getvalue()
        
        if len(content) > 0 and content.startswith(b'%PDF'):
            print("PDF Generated Successfully")
            print(f"Size: {len(content)} bytes")
            # Save for manual inspection if needed
            with open('test_report.pdf', 'wb') as f:
                f.write(content)
            print("Verified: PDF header and content present.")
        else:
            print("FAILED: Output is not a valid PDF")
            
    except Exception as e:
        print(f"FAILED with error: {e}")

if __name__ == "__main__":
    test_pdf_generation()
