/**
 * Live screen view for the private dashboard.
 *
 * The desktop is captured only while a browser is actually watching. The first
 * viewer starts the capture loop and the last one to disconnect stops it, so
 * closing the page ends every screenshot this feature takes.
 */

import type { IncomingMessage, ServerResponse } from 'node:http';
import { spawn } from 'node:child_process';
import { access, constants } from 'node:fs/promises';
import { delimiter, join } from 'node:path';
import type { Plugin } from 'vite';

const STREAM_PATH = '/veyra-screen.mjpeg';
const STATUS_PATH = '/veyra-screen.json';
const BOUNDARY = 'veyraframe';
const CAPTURE_TIMEOUT_MS = 5_000;
const MAX_CONSECUTIVE_FAILURES = 3;

type CaptureSettings = { fps: number; quality: number; scale: number };

type Capturer = {
  /** Executable that writes a JPEG frame to standard output. */
  executable: string;
  argv: (settings: CaptureSettings) => string[];
};

/** Wayland first, then X11; both write JPEG to stdout, so no frame touches disk. */
const CAPTURERS: Capturer[] = [
  {
    executable: 'grim',
    argv: ({ quality, scale }) => [
      '-t', 'jpeg', '-q', String(quality), '-s', String(scale), '-',
    ],
  },
  {
    executable: 'import',
    argv: ({ quality, scale }) => [
      '-silent', '-window', 'root',
      '-resize', `${Math.round(scale * 100)}%`,
      '-quality', String(quality), 'jpeg:-',
    ],
  },
];

const viewers = new Set<ServerResponse>();
let resolvedCapturer: { executable: string; command: string; argv: Capturer['argv'] } | null = null;
let capturing = false;
let frameCount = 0;
let lastFrameAt: number | null = null;
let lastError: string | null = null;

function clamp(value: number, low: number, high: number) {
  return Math.min(high, Math.max(low, value));
}

function readNumber(raw: string | undefined, fallback: number) {
  const value = Number(raw);
  return Number.isFinite(value) ? value : fallback;
}

/** Frame rate, JPEG quality, and output scale, overridable per instance. */
function readSettings(): CaptureSettings {
  return {
    fps: clamp(readNumber(process.env.VEYRA_SCREEN_FPS, 12), 1, 15),
    quality: clamp(readNumber(process.env.VEYRA_SCREEN_QUALITY, 70), 10, 95),
    scale: clamp(readNumber(process.env.VEYRA_SCREEN_SCALE, 0.75), 0.1, 1),
  };
}

function describe(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function sleep(milliseconds: number) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function findExecutable(name: string) {
  for (const directory of (process.env.PATH ?? '').split(delimiter)) {
    if (!directory) continue;
    const candidate = join(directory, name);
    try {
      await access(candidate, constants.X_OK);
      return candidate;
    } catch {
      // Try the next PATH entry.
    }
  }
  return null;
}

/** Resolve the capture utility once, but keep looking while none is installed. */
async function selectCapturer() {
  if (resolvedCapturer) return resolvedCapturer;

  for (const capturer of CAPTURERS) {
    const command = await findExecutable(capturer.executable);
    if (command) {
      resolvedCapturer = { executable: capturer.executable, command, argv: capturer.argv };
      return resolvedCapturer;
    }
  }
  return null;
}

function captureFrame(command: string, argv: string[]): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, argv, { stdio: ['ignore', 'pipe', 'pipe'] });
    const image: Buffer[] = [];
    const failure: Buffer[] = [];

    const timeout = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error('screen capture timed out'));
    }, CAPTURE_TIMEOUT_MS);

    child.stdout.on('data', (chunk: Buffer) => image.push(chunk));
    child.stderr.on('data', (chunk: Buffer) => failure.push(chunk));
    child.on('error', (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.on('close', (code) => {
      clearTimeout(timeout);
      const frame = Buffer.concat(image);
      if (code === 0 && frame.length > 0) {
        resolve(frame);
        return;
      }
      const detail = Buffer.concat(failure).toString('utf8').trim();
      reject(new Error(detail || `capture exited with status ${code}`));
    });
  });
}

/**
 * Send one frame to every viewer.
 *
 * Each part is written whole or not at all, so a viewer that cannot keep up
 * simply misses frames instead of receiving a truncated image.
 */
function broadcast(frame: Buffer) {
  const header = Buffer.from(
    `--${BOUNDARY}\r\nContent-Type: image/jpeg\r\n` +
    `Content-Length: ${frame.length}\r\n\r\n`,
  );

  for (const viewer of viewers) {
    if (viewer.writableEnded || viewer.writableNeedDrain) continue;
    viewer.write(header);
    viewer.write(frame);
    viewer.write('\r\n');
  }
}

function closeViewers() {
  for (const viewer of [...viewers]) {
    viewers.delete(viewer);
    viewer.end();
  }
}

/** Capture while at least one viewer is connected, then stop. */
async function captureLoop() {
  if (capturing) return;
  capturing = true;

  let failures = 0;
  try {
    while (viewers.size > 0) {
      const settings = readSettings();
      const startedAt = Date.now();

      try {
        const capturer = await selectCapturer();
        if (!capturer) {
          throw new Error(
            'No supported screen capture utility is installed '
            + '(tried grim and import).',
          );
        }
        const frame = await captureFrame(capturer.command, capturer.argv(settings));
        frameCount += 1;
        lastFrameAt = Date.now();
        lastError = null;
        failures = 0;
        broadcast(frame);
      } catch (error) {
        lastError = describe(error);
        failures += 1;
        // A transient failure is worth retrying; a persistent one should
        // surface in the browser instead of spawning captures forever.
        if (failures >= MAX_CONSECUTIVE_FAILURES) {
          closeViewers();
          break;
        }
      }

      const remaining = 1000 / settings.fps - (Date.now() - startedAt);
      if (remaining > 0) await sleep(remaining);
    }
  } finally {
    capturing = false;
  }
}

function handleStream(request: IncomingMessage, response: ServerResponse) {
  request.socket.setNoDelay(true);
  request.socket.setTimeout(0);

  // WebKit rejects multipart/x-mixed-replace in fetch, even though it can
  // display it in an img. The dashboard parses the framing itself, so use a
  // plain binary response for fetch and keep native MJPEG for direct links.
  const binary = new URL(request.url ?? '/', 'http://localhost').searchParams.get('transport') === 'fetch';
  response.writeHead(200, {
    'Content-Type': binary ? 'application/octet-stream' : `multipart/x-mixed-replace; boundary=${BOUNDARY}`,
    'X-Content-Type-Options': 'nosniff',
    'Cache-Control': 'no-store, no-cache, must-revalidate, private',
    Pragma: 'no-cache',
    Connection: 'close',
  });

  viewers.add(response);

  const drop = () => {
    if (viewers.delete(response)) response.end();
  };
  request.on('close', drop);
  response.on('close', drop);
  response.on('error', drop);

  void captureLoop();
}

async function handleStatus(response: ServerResponse) {
  const capturer = await selectCapturer();
  const settings = readSettings();

  response.setHeader('Content-Type', 'application/json; charset=utf-8');
  response.setHeader('Cache-Control', 'no-store');
  response.end(JSON.stringify({
    schema_version: 1,
    available: capturer !== null,
    tool: capturer?.executable ?? null,
    stream_path: STREAM_PATH,
    settings,
    viewers: viewers.size,
    frames: frameCount,
    last_frame_at: lastFrameAt ? new Date(lastFrameAt).toISOString() : null,
    error: capturer
      ? lastError
      : 'No supported screen capture utility is installed (tried grim and import).',
  }));
}

export function veyraScreenStream(): Plugin {
  return {
    name: 'veyra-screen-stream',
    enforce: 'pre' as const,
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const path = request.url?.split('?', 1)[0];

        if (path === STATUS_PATH) {
          void handleStatus(response);
          return;
        }
        if (path === STREAM_PATH) {
          handleStream(request, response);
          return;
        }
        next();
      });

      // Never leave a capture loop running behind a restarted dev server.
      server.httpServer?.on('close', closeViewers);
    },
  };
}
