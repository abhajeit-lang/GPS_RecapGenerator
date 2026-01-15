from app import app
from datetime import date
import io

def test_pdf_generation():
    with app.test_client() as client:
        # Data for request
        payload = {
            'start_date': '2026-01-12',
            'end_date': '2026-01-12'
        }
        
        # Atelier ID 1 is 'At1' from previous diagnostics
        response = client.post('/reports/atelier/1/pdf', json=payload)
        
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print("Success! PDF generated.")
            # Save to file to verify
            with open("test_atelier_report.pdf", "wb") as f:
                f.write(response.data)
            print("Saved to test_atelier_report.pdf")
        else:
            print("Failed.")
            print(response.get_json())

if __name__ == "__main__":
    test_pdf_generation()
