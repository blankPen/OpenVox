/**
 * Minimal NDJSON parser over a Node readable stream.
 *
 * Yields one parsed JS object per non-empty line. Tolerates CR/LF.
 * Lines that fail JSON.parse are silently dropped (stderr fallback).
 */

import type { Readable } from 'node:stream';

export async function* parseNdjson(stream: Readable): AsyncGenerator<Record<string, unknown>, void, void> {
  let buf = '';
  for await (const chunk of stream as AsyncIterable<Buffer>) {
    buf += chunk.toString('utf8');
    let idx: number;
    while ((idx = buf.indexOf('\n')) !== -1) {
      const line = buf.slice(0, idx).replace(/\r$/, '').trim();
      buf = buf.slice(idx + 1);
      if (line.length === 0) continue;
      try {
        const obj = JSON.parse(line) as unknown;
        if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
          yield obj as Record<string, unknown>;
        }
      } catch {
        // swallow malformed line
      }
    }
  }
  // Flush trailing line.
  const trailing = buf.trim();
  if (trailing.length > 0) {
    try {
      const obj = JSON.parse(trailing) as unknown;
      if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
        yield obj as Record<string, unknown>;
      }
    } catch {
      /* swallow */
    }
  }
}
