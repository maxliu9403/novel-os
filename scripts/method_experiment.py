"""Isolated method experiment CLI. Planning/status/export never invoke models."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))

from method_experiments import ExperimentLab
from pydantic import ValidationError


def read_json(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 8_000_000:
        raise ValueError('JSON input must be a regular file under 8 MB')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON field')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('plan', 'run', 'status', 'export', 'assess'):
        sub = commands.add_parser(command)
        sub.add_argument('--root', type=Path, required=True, help='isolated experiment directory, not a novel project')
        if command == 'plan':
            sub.add_argument('--spec', type=Path, required=True)
            sub.add_argument('--baseline-system', type=Path, default=ROOT / 'agents/scribe/prompt.md')
        elif command == 'run':
            sub.add_argument('--allow-model-calls', action='store_true')
            sub.add_argument('--max-model-calls', type=int, required=True, help='cumulative cap including failed/uncertain calls')
        elif command == 'assess':
            sub.add_argument('--reviews', type=Path, nargs=2, required=True)
    args = parser.parse_args(argv)
    try:
        lab = ExperimentLab(args.root)
        if args.command == 'plan':
            result = lab.plan(read_json(args.spec), baseline_system=args.baseline_system.read_text(encoding='utf-8'))
            result = {k: result[k] for k in ('input_sha256', 'planned_windows', 'planned_model_calls', 'experiment_scope')}
        elif args.command == 'run':
            result = lab.run(max_model_calls=args.max_model_calls, allow_model_calls=args.allow_model_calls)
        elif args.command == 'status':
            result = lab.status()
        elif args.command == 'export':
            result = lab.export_blind()
        else:
            result = lab.assess([read_json(p) for p in args.reviews])
    except ValidationError as exc:
        print(json.dumps({'error': 'invalid experiment input', 'fields': [list(e['loc']) for e in exc.errors(include_input=False)]}))
        return 2
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
