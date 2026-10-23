"""Authenticated, read-only course browser; invoked after web chat authentication."""
import html
import mimetypes
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, parse_qs

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


MEDIA = {'.mp4', '.m4v', '.mov', '.mkv', '.webm', '.mp3', '.m4a', '.wav'}


def media_url(root, path):
    return '/videos/' + quote(path.relative_to(root).as_posix())


def natural_key(path):
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r'(\d+)', path.name)]


def page(title, content):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>' + html.escape(title) + ' · Veyra</title>'
            '<link rel="stylesheet" href="/videos.css"></head><body>'
            '<header><a href="/">Veyra</a><nav aria-label="Navigation">'
            '<a href="/">Chat</a><a href="/videos/">Videos</a>'
            '<a href="/dashboard">Dashboard</a></nav></header><main>'
            + content + '</main></body></html>').encode()


def browse(root, target):
    entries = sorted((p for p in target.iterdir() if not p.name.startswith('.')
                      and not p.is_symlink() and (p.is_dir() or p.suffix.lower() in MEDIA)),
                     key=lambda p: (not p.is_dir(), natural_key(p)))
    rows = []
    for entry in entries:
        directory = entry.is_dir()
        link = media_url(root, entry) + ('/' if directory else '?watch=1')
        label = 'Folder' if directory else entry.suffix[1:].upper()
        rows.append('<li><a href="' + html.escape(link, quote=True) + '"><span class="kind">'
                    + label + '</span><span>' + html.escape(entry.name) + '</span></a></li>')
    back = ('<a class="button" href="' + html.escape(media_url(root, target.parent) + '/', quote=True)
            + '">← Parent folder</a>') if target != root else ''
    title = 'Videos' if target == root else target.name
    return page(title, '<p class="eyebrow">YOUR VIDEO LIBRARY</p><h1>' + html.escape(title)
                + '</h1><p>Browse your course folders and watch here in Veyra. '
                'Uses the same connection and sign-in as chat.</p>' + back
                + '<ul class="library">' + ''.join(rows) + '</ul>'
                + ('<p>No videos or folders here yet.</p>' if not rows else ''))


def watch(root, target):
    siblings = sorted((p for p in target.parent.iterdir() if p.is_file() and not p.is_symlink()
                       and not p.name.startswith('.') and p.suffix.lower() in MEDIA), key=natural_key)
    index = siblings.index(target)
    links = []
    for offset, label in ((-1, '← Previous'), (1, 'Next →')):
        if 0 <= index + offset < len(siblings):
            links.append('<a class="button" href="' + html.escape(media_url(root, siblings[index + offset])
                         + '?watch=1', quote=True) + '">' + label + '</a>')
    src = html.escape(media_url(root, target), quote=True)
    tag = 'audio' if target.suffix.lower() in {'.mp3', '.m4a', '.wav'} else 'video'
    return page(target.name, '<a href="' + html.escape(media_url(root, target.parent) + '/', quote=True)
                + '">← Back to folder</a><h1>' + html.escape(target.stem) + '</h1><'
                + tag + ' controls playsinline preload="metadata" src="' + src + '">'
                'Your browser cannot play this file.</' + tag + '><nav class="playback" aria-label="Lessons">'
                + ''.join(links) + '</nav><p>Use the player controls to seek or enter full screen. '
                'Playback depends on your browser’s codec support; Veyra does not convert videos.</p>'
                '<a href="' + src + '">Open original media</a>')


def serve(handler, root=None):
    root = ROOT if root is None else root
    try:
        target = resolve(root, handler.path)
        if target.is_dir():
            return handler.respond(200, browse(root, target), 'text/html; charset=utf-8')
        # Never render course HTML or scripts within Veyra's authenticated origin.
        if target.suffix.lower() not in MEDIA:
            return handler.respond(403, {'error': 'Only media files are shared.'})
        if parse_qs(urlsplit(handler.path).query).get('watch') == ['1'] and target.is_file():
            return handler.respond(200, watch(root, target), 'text/html; charset=utf-8')
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
