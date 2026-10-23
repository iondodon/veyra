"""Authenticated, read-only course browser; invoked after web chat authentication."""
import html
import mimetypes
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

COURSE = 'Adrian Cantrill - AWS Certified Solutions Architect - Associate (SAA-C03) (7.2023)'
ROOT = Path.home() / 'Downloads' / COURSE


def resolve(root, url):
    parts = unquote(urlsplit(url).path).removeprefix('/videos').split('/')
    target = root
    for part in parts:
        if not part:
            continue
        if part.startswith('.') or '\\' in part or '\x00' in part:
            raise ValueError('Invalid path')
        target = target / part
        if target.is_symlink():
            raise ValueError('Symlinks are not shared')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Outside share')
    return target


def byte_range(value, size):
    if not value:
        return 0, size - 1
    if not value.startswith('bytes=') or ',' in value:
        raise ValueError('Unsupported range')
    left, right = value[6:].split('-', 1)
    start = int(left) if left else max(0, size - int(right))
    end = min(int(right), size - 1) if left and right else size - 1
    if start < 0 or start > end or start >= size or (not left and int(right) <= 0):
        raise ValueError('Unsatisfiable range')
    return start, end


def serve(handler, root=None):
    root = ROOT if root is None else root
    try:
        target = resolve(root, handler.path)
        if target.is_dir():
            rows = []
            for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
                if entry.name.startswith('.') or entry.is_symlink():
                    continue
                name = entry.name + ('/' if entry.is_dir() else '')
                link = '/videos/' + quote(str(entry.relative_to(root))) + ('/' if entry.is_dir() else '')
                rows.append('<li><a href="' + link + '">' + html.escape(name) + '</a></li>')
            body = ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Course videos</title></head><body><nav><a href="/">Chat</a> · <a href="/videos/">Course home</a> · <a href="../">Up</a></nav><h1>Course videos</h1><p>Uses Veyra’s existing connection and sign-in. Tap a video to play. Unsupported formats may need VLC (network stream with Veyra authentication).</p><ul>' + ''.join(rows) + '</ul></body></html>').encode()
            return handler.respond(200, body, 'text/html; charset=utf-8')
        # Never render course HTML or scripts within Veyra's authenticated origin.
        if target.suffix.lower() not in ('.mp4', '.m4v', '.mov', '.mkv', '.webm', '.mp3', '.m4a', '.wav'):
            return handler.respond(403, {'error': 'Only media files are shared.'})
        with target.open('rb') as source:
            size = target.stat().st_size
            try:
                start, end = byte_range(handler.headers.get('Range'), size)
            except (ValueError, TypeError):
                handler.send_response(416)
                handler.send_header('Content-Range', f'bytes */{size}')
                handler.send_header('Content-Length', '0')
                handler.end_headers()
                return
            handler.send_response(206 if handler.headers.get('Range') else 200)
            handler.send_header('Content-Type', mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            handler.send_header('Accept-Ranges', 'bytes')
            handler.send_header('Content-Length', str(max(0, end - start + 1)))
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('X-Content-Type-Options', 'nosniff')
            if handler.headers.get('Range'):
                handler.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            handler.end_headers()
            if handler.command == 'HEAD':
                return
            source.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = source.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)
    except (BrokenPipeError, ConnectionResetError, TimeoutError):
        pass
    except ValueError:
        handler.respond(403, {'error': 'Path not shared.'})
    except OSError:
        handler.respond(404, {'error': 'Course folder or file not found.'})
