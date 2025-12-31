from report_logic import load_file, process_dataframe
df = load_file('sample.csv')
result = process_dataframe(df, include_date=True)
print(f"Result DataFrame shape: {result.shape}")
print(f"Result columns: {result.columns.tolist()}")
if len(result) > 0:
    print(f"Vehicles with time_before_seconds > 0: {(result['time_before_seconds'] > 0).sum()}")
    print(result[result['time_before_seconds'] > 0][['vehicle', 'time_before_hhmm', 'time_after_hhmm', 'km_before', 'km_after']].head(10))
else:
    print("Result is empty!")
