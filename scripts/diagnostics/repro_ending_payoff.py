"""Replay the ending gate from a project's read-only snapshot in a disposable directory."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'core'))
from ending_quality import evaluate_ending


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('project', type=Path)
    parser.add_argument('--chapter', type=int, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='novel-ending-replay-') as temp:
        root = Path(temp)
        for relative in ['outputs/state/story_state.json', 'outputs/input/ending_contract.json']:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.project / relative, destination)
        report = evaluate_ending(root, as_of_chapter=args.chapter)
        print(json.dumps({'status': report.status, 'critical': report.critical, 'warnings': report.warnings}, indent=2))
        return 0 if report.status == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
