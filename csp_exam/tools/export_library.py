"""Export private, portable problem packages offline (stop the CSP website first)."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.core import store
from csp_exam.core.problem_export import build_zip
from csp_exam.core.runtime_lock import data_lock


def export(output, selected=None):
    with data_lock(store.DATA_DIR):
        return build_zip(output, selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pid", action="append")
    args = parser.parse_args()
    print(json.dumps(export(args.output, args.pid), ensure_ascii=False))


if __name__ == "__main__":
    main()
