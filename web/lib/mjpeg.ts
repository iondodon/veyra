/** Decode our length-delimited MJPEG stream independently of browser img support.
 * Some embedded browsers display only the first multipart image, or never fire
 * its load event until the connection ends. Explicit frames avoid both issues.
 */
export async function* jpegFrames(body: ReadableStream<Uint8Array>): AsyncGenerator<Uint8Array> {
  const reader = body.getReader();
  let buffer = new Uint8Array(0);
  let length: number | null = null;
  const decoder = new TextDecoder();
  const maxFrame = 16 * 1024 * 1024;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) throw new Error('The desktop stream closed. Reconnect to try again.');
      const joined = new Uint8Array(buffer.length + value.length);
      joined.set(buffer); joined.set(value, buffer.length); buffer = joined;
      while (true) {
        if (length === null) {
          let end = -1;
          for (let i = 0; i < buffer.length - 3; i++) {
            if (buffer[i] === 13 && buffer[i + 1] === 10 && buffer[i + 2] === 13 && buffer[i + 3] === 10) { end = i; break; }
          }
          if (end < 0) {
            if (buffer.length > 4096) throw new Error('Invalid desktop frame header.');
            break;
          }
          const header = decoder.decode(buffer.subarray(0, end));
          const match = /(?:^|\r\n)Content-Length:\s*(\d+)\s*(?:\r\n|$)/i.exec(header);
          length = match ? Number(match[1]) : 0;
          if (!length || length > maxFrame) throw new Error('Invalid desktop frame length.');
          buffer = buffer.slice(end + 4);
        }
        if (buffer.length < length) break;
        const frame = buffer.slice(0, length);
        buffer = buffer.slice(length);
        length = null;
        yield frame;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
