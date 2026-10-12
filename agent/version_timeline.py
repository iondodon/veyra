"""Bounded, read-only history of the running version's first-parent lineage."""
import json
from pathlib import Path
import re
import subprocess

from release_notes import validate_notes


class HistoryUnavailable(Exception):
    pass


class VersionTimeline:
    PAGE_SIZE = 20

    def __init__(self, root):
        self.root = Path(root)
        try:
            self.current = self.git('rev-parse', '--verify', 'HEAD').strip()
        except HistoryUnavailable:
            self.current = None

    def git(self, *args):
        try:
            result = subprocess.run(['git', '--no-optional-locks', *args], cwd=self.root,
                                    capture_output=True, timeout=3, check=True)
            return result.stdout.decode('utf-8', errors='replace')
        except (OSError, subprocess.SubprocessError) as error:
            raise HistoryUnavailable('Version history is unavailable. Try again or open Recovery.') from error

    def notes(self, commit):
        # Read only the versioned public handoff, never state or credential files.
        try:
            size = int(self.git('cat-file', '-s', f'{commit}:agent/release_notes.json'))
            if size > 16000:
                return None
            return validate_notes(json.loads(self.git('show', f'{commit}:agent/release_notes.json')))
        except (HistoryUnavailable, ValueError, TypeError):
            return None

    def page(self, offset=0):
        if not isinstance(offset, int) or not 0 <= offset <= 100000:
            raise ValueError('Invalid timeline cursor.')
        if not self.current:
            raise HistoryUnavailable('Version history is unavailable. Open Recovery to check the repository.')
        raw = self.git('log', '--first-parent', f'--skip={offset}', f'--max-count={self.PAGE_SIZE + 1}',
                       '--format=%H%x00%cI%x00%s', self.current, '--')
        rows = raw.splitlines()
        versions = []
        for row in rows[:self.PAGE_SIZE]:
            fields = row.split('\0', 2)
            if len(fields) != 3 or not re.fullmatch(r'[0-9a-f]{40,64}', fields[0]):
                raise HistoryUnavailable('Version history could not be read.')
            commit, date, title = fields
            versions.append({'commit': commit, 'date': date, 'title': title,
                             'current': commit == self.current, 'notes': self.notes(commit)})
        return {'current': self.current, 'versions': versions,
                'next_offset': offset + self.PAGE_SIZE if len(rows) > self.PAGE_SIZE else None}
