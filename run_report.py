import argparse
from pathlib import Path
from report_logic import generate_reports


def main():
    p = argparse.ArgumentParser(description="Vehicle activity report generator")
    p.add_argument("input", help="Input CSV or Excel file")
    p.add_argument("--output-dir", default="out", help="Output directory")
    p.add_argument("--period", choices=["daily","monthly"], default="daily", help="Report period")
    p.add_argument("--format", choices=["csv","xlsx"], default="csv", help="Output file format")
    args = p.parse_args()

    infile = Path(args.input)
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    generate_reports(infile, outdir, period=args.period, out_format=args.format)

if __name__ == '__main__':
    main()
