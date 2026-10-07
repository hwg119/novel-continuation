"""Resolve a candidate baseline without losing the saved-text overwrite guard."""
import json
from pathlib import Path


def latest_candidate(project_dir, number, saved_body):
    matches = []
    for path in (Path(project_dir) / 'runs' / 'web_jobs').glob('*.json'):
        try:
            job = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        result = job.get('result') or {}
        if (job.get('kind') == 'revise' and job.get('status') == 'completed'
                and result.get('chapter_number') == number
                and isinstance(result.get('candidate'), str) and result['candidate'].strip()
                and str(result.get('original_source', result.get('source', ''))).strip() == saved_body.strip()):
            matches.append(job)
    if not matches:
        return None
    return max(matches, key=lambda job: (job.get('finished_epoch') or 0, job.get('created_at') or '', job['id']))
