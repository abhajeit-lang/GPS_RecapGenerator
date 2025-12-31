from report_logic import load_file, process_dataframe, parse_datetime
import pandas as pd

df = load_file('sample.csv')
course_df = df[df['CAA'] == 'Course'].head(5)
print("Course records:", len(course_df))
print(course_df[['Code', 'Heure de départ', 'Heure d\'arrêt', 'KM']])
print("\nTesting process_dataframe:")
result = process_dataframe(df)
print("Result shape:", result.shape)
print("Result columns:", result.columns.tolist())
if len(result) > 0:
    print(result.head())
