"""Bounded, session-local progress derived solely from runtime events."""
from copy import deepcopy
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class TaskProgress:
    def __init__(self):
        self.tasks = []

    def start(self, text, continuation=False):
        if continuation and self.tasks:
            self.tasks[0]['state'] = 'working'
            self.tasks[0]['finished'] = None
            self.add('resume', 'Command decision received')
            return
        self.tasks.insert(0, dict(request=text[:1000] or 'Image request', started=now(),
                                  finished=None, state='working', events=[]))
        del self.tasks[10:]
        self.add('start', 'Request received')

    def add(self, kind, text):
        if not self.tasks:
            return
        self.tasks[0]['events'].append(dict(kind=kind, text=text[:8000], at=now()))
        del self.tasks[0]['events'][:-100]

    def event(self, event):
        if not self.tasks:
            return
        task = self.tasks[0]
        kind = event['type']
        if kind == 'approval':
            task['state'] = 'approval'
            self.add('approval', event['text'])
        elif kind == 'clear_approval' and task['state'] == 'approval':
            task['state'] = 'working'
        elif kind == 'message':
            subtype = event.get('kind', 'message')
            if event.get('role') == 'tool' or subtype == 'error':
                self.add(subtype, event['text'])
            elif event.get('role', 'assistant') == 'assistant' and subtype == 'message':
                self.add('reply', event['text'])
        elif kind == 'done' and task['state'] != 'approval':
            task['state'] = 'finished'
            task['finished'] = now()
            self.add('done', 'Request finished. See results below; this does not imply all commands succeeded.')

    def interrupt(self):
        if self.tasks and self.tasks[0]['state'] in ('working', 'approval'):
            self.tasks[0]['state'] = 'interrupted'
            self.tasks[0]['finished'] = now()
            self.add('interrupted', 'Runtime disconnected; unfinished work was interrupted.')

    def snapshot(self):
        return deepcopy(self.tasks)
