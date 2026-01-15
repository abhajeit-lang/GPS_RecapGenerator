"""
API endpoints for advanced reports.
Add these routes to app.py after the existing hierarchy routes.
"""

# Add after line ~690 in app.py:

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
