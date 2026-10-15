"""Reopen each saved format in a separate Blender process using the PR12 inspector."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inspect_vehicle import inspect

parser = argparse.ArgumentParser()
parser.add_argument('--input', required=True, type=Path)
parser.add_argument('--report', required=True, type=Path)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
report = inspect(args.input)
report['source_sha256'] = hashlib.sha256(args.input.read_bytes()).hexdigest()
report['independent_reopen'] = True
args.report.write_text(json.dumps(report, indent=2))
