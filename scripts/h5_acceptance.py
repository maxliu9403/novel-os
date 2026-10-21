"""Inspect ZIPs and validate externally supplied H5 integration evidence."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

from h5_acceptance import inspect_package, build_acceptance_plan, verify_importer_receipt
from scripts.method_experiment import read_json
from pydantic import ValidationError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('package', type=Path)
    plan = commands.add_parser('plan')
    plan.add_argument('--cases', type=Path, required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('--plan', type=Path, required=True)
    verify.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            result = inspect_package(args.package)
        elif args.command == 'plan':
            cases = read_json(args.cases)
            for case in cases:
                for field in ('package', 'baseline'):
                    if case.get(field):
                        case[field] = str(args.cases.resolve().parent / case[field])
            result = build_acceptance_plan(cases)
        else:
            result = verify_importer_receipt(read_json(args.plan), read_json(args.receipt))
    except ValidationError as exc:
        print(json.dumps({'error': 'invalid evidence schema', 'fields': [list(e['loc']) for e in exc.errors(include_input=False)]}))
        return 2
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
