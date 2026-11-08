"""Import an author-authorized editorial revision without a model or engine call.

Preview by default. The complete candidate is validated, then committed under the
normal Workshop lock with revision/hash guards and an immutable source snapshot.
This is an administrative import, not automatic literary review or approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import workshop
from api.services import ProjectService
from core.novel_classification import parse_classification_block
from core.narrative_format import parse_narrative_format_block


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def revise(service, session_id, *, revision, source_sha256, prompt, note,
           words_per_chapter=None, apply=False):
    if not note.strip():
        raise ValueError('Describe the author-authorized changes before importing.')
    request = dict(revision=revision, source_sha256=source_sha256,
                   prompt_sha256=digest(prompt), note=note,
                   words_per_chapter=words_per_chapter)
    request_sha = digest(json.dumps(request, ensure_ascii=False, sort_keys=True))
    with service._locked(session_id) as data:
        for record in data.get('editorial_revisions', []):
            if record['request_sha256'] == request_sha:
                return {**record, 'already_applied': True}
        service._available(data, revision)
        if (data['status'] != 'ready' or data.get('project_id')
                or not data.get('ready_for_confirmation') or data.get('questions')
                or data.get('input_needs_revision') or data.get('design_needs_revision')
                or data.get('restart_required')):
            raise ValueError('Only a complete idle, unlaunched skeleton can be revised.')
        if digest(data['prompt_md']) != source_sha256:
            raise ValueError('Source skeleton hash changed; review the current version.')
        if prompt == data['prompt_md']:
            raise ValueError('Candidate has no changes.')
        before_format = parse_narrative_format_block(data['prompt_md'])
        after_format = parse_narrative_format_block(prompt)
        # This narrow import supports prose/word-allocation edits. Repartitioning
        # a book requires a separately reviewed chapter/volume remapping.
        before_structure, after_structure = before_format.to_dict(), after_format.to_dict()
        for structure in (before_structure, after_structure):
            for key in ('target_words', 'chapter_target_words', 'format_id'):
                structure.pop(key)
            for volume in structure['volumes']:
                volume.pop('target_words')
        if before_structure != after_structure:
            raise ValueError('Chapter, volume or format changes require a separate replan.')
        if parse_classification_block(prompt) != parse_classification_block(data['prompt_md']):
            raise ValueError('Editorial revision cannot change publication classification.')
        if words_per_chapter is not None:
            if type(words_per_chapter) is not int or words_per_chapter < 1:
                raise ValueError('Expected a positive average chapter word target.')
            if after_format.target_words != after_format.total_chapters * words_per_chapter:
                raise ValueError('Confirmed average and complete-book target disagree.')
        elif before_format.target_words != after_format.target_words:
            raise ValueError('A changed word target requires the author-confirmed average.')
        with tempfile.TemporaryDirectory(prefix='workshop-editorial-') as folder:
            candidate = Path(folder) / 'prompt.md'
            candidate.write_text(prompt, encoding='utf-8')
            validated = workshop._validate_candidate(candidate)
        record = {**request, 'request_sha256': request_sha, 'new_revision': revision + 1,
                  'chapters': validated['chapters'], 'target_words': validated['target_words']}
        if not apply:
            return {**record, 'preview': True}
        root = service._path(session_id)
        version = root / 'versions' / f'{revision + 1}.md'
        if version.is_symlink() or version.parent.is_symlink():
            raise ValueError('Unsafe version path.')
        if version.exists() and version.read_text(encoding='utf-8') != prompt:
            raise ValueError('The target version already contains another candidate.')
        backup = service._history_path(session_id, f'editorial-{request_sha}.json')
        backup.parent.mkdir(exist_ok=True)
        # On an interrupted import, keep the original backup and reuse the same
        # staged candidate. session.json is the single atomic commit point.
        if not backup.exists():
            workshop._atomic(backup, json.dumps(data, ensure_ascii=False, indent=2))
        version.parent.mkdir(exist_ok=True)
        workshop._atomic(version, prompt)
        if words_per_chapter is not None:
            data['input']['words_per_chapter'] = words_per_chapter
        data.update(revision=revision + 1, prompt_md=prompt, prepared=None,
                    last_error=None, job_id=None, active_operation=None,
                    active_turn_request=None, paused_at=None, resumed_from_revision=None,
                    delivery_run_id=None, delivery_source_revision=None,
                    delivery_published_run_id=None)
        if data.get('naming'):
            data['naming'].update(source_sha256='', confirmed_title=None, candidates=[])
        data.setdefault('editorial_revisions', []).append(record)
        data['messages'].append({'role': 'assistant', 'content':
            '已按作者授权保存编辑修订版；原版与讨论记录保留。\n\n' + note +
            '\n\n本次未调用模型、未启动正文。结构校验不代表正文质量已通过。',
            'questions': []})
        service._log(data, 'editorial_revision', '已保存经授权的骨架编辑修订；尚未启动正文。',
                     source_sha256=source_sha256, prompt_sha256=digest(prompt))
        service._save(data)
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projects-root', type=Path, required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--revision', type=int, required=True)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--prompt', type=Path, required=True)
    parser.add_argument('--note', required=True)
    parser.add_argument('--words-per-chapter', type=int)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    result = revise(workshop.WorkshopService(ProjectService(args.projects_root)), args.session,
                    revision=args.revision, source_sha256=args.source_sha256,
                    prompt=args.prompt.read_text(encoding='utf-8'), note=args.note,
                    words_per_chapter=args.words_per_chapter, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
