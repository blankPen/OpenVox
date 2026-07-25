#!/usr/bin/env node
// Mock ACP agent — speaks minimal JSON-RPC + NDJSON over stdio.
//
// Behaviour is driven by env vars so each test can configure it independently:
//
//   MOCK_MODE=hello        — wait for `initialize`, then emit session_id +
//                            two text deltas + close. Default mode.
//
//   MOCK_MODE=text-only    — same as `hello` but skips the session_id event.
//
//   MOCK_MODE=tool         — emit a tool_call event after initialize.
//
//   MOCK_MODE=exit-fast    — read one line, then immediately exit 0
//                            (simulates an early-disconnect upstream).
//
//   MOCK_EXIT_AFTER_MS=N   — after writing responses, wait N ms then exit 0.
//
// Lines written are NDJSON objects (one JSON object per line).
// Lines read from stdin are ignored except to count them and react to the
// first one (which should be `initialize`).
import { readFileSync } from 'node:fs';

const MODE = process.env.MOCK_MODE ?? 'hello';
const EXIT_AFTER_MS = Number(process.env.MOCK_EXIT_AFTER_MS ?? 0);

function writeEvent(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

let buf = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  buf += chunk;
  // React as soon as a complete line arrives.
  let idx;
  while ((idx = buf.indexOf('\n')) !== -1) {
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (line.length === 0) continue;
    handleLine(line);
  }
});

process.stdin.on('end', () => {
  // stdin closed by parent — flush any pending and exit gracefully.
  if (buf.trim().length > 0) handleLine(buf.trim());
  finish();
});

function handleLine(line) {
  let parsed;
  try {
    parsed = JSON.parse(line);
  } catch {
    return;
  }
  const method = parsed?.method;
  if (method !== 'initialize') return; // ignore everything else for the mock

  // Always echo a 'ready' text delta after initialize.
  writeEvent({ type: 'text', delta: 'agent ready' });

  if (MODE === 'text-only') {
    setTimeout(() => {
      writeEvent({ type: 'text', delta: 'hello from mock acp' });
      writeEvent({ type: 'text', delta: ' — done' });
      finish();
    }, 50);
    return;
  }

  if (MODE === 'tool') {
    // session_id first, then a tool_call event, then text.
    setTimeout(() => {
      writeEvent({ type: 'session_id', id: 'mock-session-1' });
      writeEvent({
        type: 'tool_call',
        id: 'call_001',
        name: 'bash',
        args: { cmd: 'ls -la' },
      });
      writeEvent({ type: 'text', delta: 'tool finished' });
      finish();
    }, 50);
    return;
  }

  if (MODE === 'exit-fast') {
    // Receive initialize, do not respond — just exit. Parent should
    // see EOF on stdout and the consumer should still get a `done` event.
    setTimeout(() => process.exit(0), 20);
    return;
  }

  // default MODE === 'hello'
  setTimeout(() => {
    writeEvent({ type: 'session_id', id: 'mock-session-1' });
  }, 10);
  setTimeout(() => {
    writeEvent({ type: 'text', delta: 'hello from mock acp' });
    writeEvent({ type: 'text', delta: ' — done' });
    finish();
  }, 50);
}

function finish() {
  if (EXIT_AFTER_MS > 0) {
    setTimeout(() => process.exit(0), EXIT_AFTER_MS);
  } else {
    // Give the consumer a moment to drain, then close stdout.
    setTimeout(() => {
      try {
        process.stdout.end();
      } catch {
        /* ignore */
      }
      setTimeout(() => process.exit(0), 10);
    }, 10);
  }
}

// Optional: log to stderr when AGENTD_DEBUG=1 to help debug tests.
if (process.env.AGENTD_DEBUG) {
  process.stderr.write(`[mock-acp-agent] mode=${MODE} pid=${process.pid}\n`);
}