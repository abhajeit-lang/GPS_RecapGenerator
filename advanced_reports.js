// Advanced Reports JavaScript Functions
// Add this code before the closing </script> tag in index.html

// ===== ADVANCED REPORTS FUNCTIONS =====
function switchReportType(reportType) {
    // Hide all report contents
    document.querySelectorAll('.report-content').forEach(el => el.style.display = 'none');

    // Show selected report
    document.getElementById(`report-${reportType}`).style.display = 'block';

    // Update button states
    document.querySelectorAll('#advanced-reports .tab-btn').forEach(btn => btn.classList.remove('active'));
    event.target.classList.add('active');
}

function loadAteliersForComparative() {
    if (!hierarchyData || !hierarchyData.projects) return;

    const container = document.getElementById('comparativeAtelierList');
    let html = '<div style="font-weight: bold; margin-bottom: 10px;">Cochez les ateliers à comparer:</div>';

    hierarchyData.projects.forEach(p => {
        if (p.ateliers.length > 0) {
            html += `<div style="margin-bottom: 10px;">
                <div style="font-weight: bold; color: #667eea; margin-bottom: 5px;">${p.name}</div>`;

            p.ateliers.forEach(a => {
                html += `<label style="display: block; padding: 5px; cursor: pointer;">
                    <input type="checkbox" class="comp-atelier-check" value="${a.id}">
                    🏭 ${a.name} <span style="color:#999; font-size: 0.9em;">(${a.vehicle_count} engins)</span>
                </label>`;
            });

            html += '</div>';
        }
    });

    if (hierarchyData.projects.length === 0) {
        html = '<p style="color:#999;">Aucun atelier disponible. Créez d\'abord des projets et ateliers.</p>';
    }

    container.innerHTML = html;
}

function generateComparativeReport() {
    const startDate = document.getElementById('comp_start_date').value;
    const endDate = document.getElementById('comp_end_date').value;
    const checked = document.querySelectorAll('.comp-atelier-check:checked');
    const atelierIds = Array.from(checked).map(cb => parseInt(cb.value));

    if (!startDate || !endDate) {
        alert('Veuillez sélectionner les dates de début et fin.');
        return;
    }

    if (atelierIds.length === 0) {
        alert('Sélectionnez au moins un atelier.');
        return;
    }

    document.getElementById('advReportLoading').style.display = 'block';
    document.getElementById('advReportMessage').innerHTML = '';

    fetch('/reports/comparative', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            atelier_ids: atelierIds,
            start_date: startDate,
            end_date: endDate
        })
    })
        .then(r => r.json())
        .then(data => {
            document.getElementById('advReportLoading').style.display = 'none';

            if (data.error) {
                document.getElementById('advReportMessage').innerHTML = `<p style="color:#dc3545;">${data.error}</p>`;
                return;
            }

            displayComparativeResults(data.data, data.date_range);
        })
        .catch(err => {
            document.getElementById('advReportLoading').style.display = 'none';
            document.getElementById('advReportMessage').innerHTML = `<p style="color:#dc3545;">Erreur: ${err.message}</p>`;
        });
}

function displayComparativeResults(results, dateRange) {
    const container = document.getElementById('comparativeTable');
    const resultsDiv = document.getElementById('comparativeResults');

    if (!results || results.length === 0) {
        container.innerHTML = '<p>Aucune donnée pour cette période.</p>';
        resultsDiv.style.display = 'block';
        return;
    }

    // Build table
    let html = `<p style="margin-bottom: 15px; color: #667eea; font-weight: bold;">Période: ${dateRange}</p>`;
    html += '<table style="width: 100%; border-collapse: collapse; background: white;">';
    html += `<thead>
        <tr style="background: #667eea; color: white;">
            <th style="padding: 10px; text-align: left; border: 1px solid #ddd;">Rang</th>
            <th style="padding: 10px; text-align: left; border: 1px solid #ddd;">Atelier</th>
            <th style="padding: 10px; text-align: left; border: 1px solid #ddd;">Projet</th>
            <th style="padding: 10px; text-align: center; border: 1px solid #ddd;">Véhicules</th>
            <th style="padding: 10px; text-align: center; border: 1px solid #ddd;">Trajets</th>
            <th style="padding: 10px; text-align: center; border: 1px solid #ddd;">Dist. Moy/Trajet</th>
            <th style="padding: 10px; text-align: center; border: 1px solid #ddd;">Heures Travail</th>
            <th style="padding: 10px; text-align: center; border: 1px solid #ddd;">Efficacité %</th>
            <th style="padding: 10px; text-align: center; border: 1px solid #ddd;">Utilisation %</th>
        </tr>
    </thead><tbody>`;

    results.forEach((r, idx) => {
        const rankIcon = idx === 0 ? '🥇' : idx === 1 ? '🥈' : idx === 2 ? '🥉' : (idx + 1);
        const rowBg = idx % 2 === 0 ? '#f8f9fa' : 'white';

        html += `<tr style="background: ${rowBg};">
            <td style="padding: 8px; border: 1px solid #ddd; text-align: center; font-size: 18px;">${rankIcon}</td>
            <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">${r.atelier_name}</td>
            <td style="padding: 8px; border: 1px solid #ddd;">${r.project_name}</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">${r.vehicle_count}</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">${r.total_trips}</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">${r.avg_trip_distance} km</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">${r.working_hours} h</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: center; font-weight: bold; color: ${r.efficiency_rate >= 80 ? '#28a745' : r.efficiency_rate >= 60 ? '#ffc107' : '#dc3545'};">${r.efficiency_rate}%</td>
            <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">${r.utilization_rate}%</td>
        </tr>`;
    });

    html += '</tbody></table>';
    container.innerHTML = html;
    resultsDiv.style.display = 'block';
}

// Extend switchTab to load ateliers when opening advanced reports
const originalSwitchTab = window.switchTab;
window.switchTab = function (tabName) {
    originalSwitchTab(tabName);
    if (tabName === 'advanced-reports') {
        loadAteliersForComparative();
    }
};
