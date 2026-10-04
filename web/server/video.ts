/** Encode live JPEG captures as fragmented H.264 MP4 for native video players. */
import { spawn } from 'node:child_process';
import type { ServerResponse } from 'node:http';

export function encodeVideo(response: ServerResponse, fps: number) {
  const encoder = spawn('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-f', 'image2pipe', '-vcodec', 'mjpeg',
    '-framerate', String(fps), '-probesize', '32', '-analyzeduration', '0', '-i', 'pipe:0', '-an',
    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
    '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency',
    '-profile:v', 'baseline', '-level:v', '3.1', '-pix_fmt', 'yuv420p', '-g', String(Math.ceil(fps)), '-threads', '2',
    '-movflags', 'empty_moov+default_base_moof+frag_keyframe',
    '-flush_packets', '1', '-f', 'mp4', 'pipe:1',
  ], { stdio: ['pipe', 'pipe', 'pipe'] });
  let closed = false;
  let detail = '';
  const close = () => {
    if (closed) return;
    closed = true;
    encoder.stdin.destroy();
    encoder.stdout.unpipe(response);
    encoder.kill('SIGKILL');
    response.end();
  };
  encoder.stderr.on('data', (chunk: Buffer) => { detail = (detail + chunk.toString()).slice(-2048); });
  encoder.stdin.on('error', close);
  encoder.on('error', close);
  encoder.on('close', (code) => {
    if (!closed && code !== 0) console.error('Live video encoder stopped:', detail);
    close();
  });
  response.on('close', close);
  response.on('error', close);
  encoder.stdout.pipe(response);
  return {
    write(frame: Buffer) {
      // Bound input buffering for viewers on slow connections.
      if (!closed && !encoder.stdin.writableNeedDrain) encoder.stdin.write(frame);
    },
    end: close,
  };
}
