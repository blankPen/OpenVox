import { describe, expect, it } from 'vitest';
import { Readable } from 'node:stream';
import { parseNdjson } from '../../src/stream/ndjson.js';

async function streamFromString(s: string): Promise<Readable> {
  return Readable.from([Buffer.from(s, 'utf8')]);
}

describe('stream/ndjson.parseNdjson', () => {
  it('parses simple newline-delimited JSON', async () => {
    const src = await streamFromString('{"a":1}\n{"b":2}\n');
    const out: Array<Record<string, unknown>> = [];
    for await (const obj of parseNdjson(src)) out.push(obj);
    expect(out).toEqual([{ a: 1 }, { b: 2 }]);
  });

  it('tolerates CR/LF line endings', async () => {
    const src = await streamFromString('{"a":1}\r\n{"b":2}\r\n');
    const out: Array<Record<string, unknown>> = [];
    for await (const obj of parseNdjson(src)) out.push(obj);
    expect(out).toEqual([{ a: 1 }, { b: 2 }]);
  });

  it('skips empty lines', async () => {
    const src = await streamFromString('\n\n{"a":1}\n\n');
    const out: Array<Record<string, unknown>> = [];
    for await (const obj of parseNdjson(src)) out.push(obj);
    expect(out).toEqual([{ a: 1 }]);
  });

  it('drops malformed lines silently', async () => {
    const src = await streamFromString('{"a":1}\nnot json\n{"b":2}\n');
    const out: Array<Record<string, unknown>> = [];
    for await (const obj of parseNdjson(src)) out.push(obj);
    expect(out).toEqual([{ a: 1 }, { b: 2 }]);
  });

  it('flushes a trailing line without newline', async () => {
    const src = await streamFromString('{"a":1}\n{"b":2}');
    const out: Array<Record<string, unknown>> = [];
    for await (const obj of parseNdjson(src)) out.push(obj);
    expect(out).toEqual([{ a: 1 }, { b: 2 }]);
  });

  it('handles chunked input across yields', async () => {
    const src = Readable.from((async function* () {
      yield Buffer.from('{"a":', 'utf8');
      yield Buffer.from('1}\n{"b":2', 'utf8');
      yield Buffer.from('}\n', 'utf8');
    })());
    const out: Array<Record<string, unknown>> = [];
    for await (const obj of parseNdjson(src)) out.push(obj);
    expect(out).toEqual([{ a: 1 }, { b: 2 }]);
  });

  it('ignores non-object JSON values', async () => {
    const src = await streamFromString('1\n"hello"\n[1,2,3]\n{"a":1}\n');
    const out: Array<Record<string, unknown>> = [];
    for await (const obj of parseNdjson(src)) out.push(obj);
    expect(out).toEqual([{ a: 1 }]);
  });
});