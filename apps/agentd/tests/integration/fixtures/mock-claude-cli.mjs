#!/usr/bin/env node
// Mock Claude CLI — speaks stream-json over stdin/stdout in the same shape as
// the real `claude --print --input-format stream-json --output-format
// stream-json --verbose`.
//
// Behaviour driven by env vars:
//
//   MOCK_MODE=multi-turn — accept N user messages on stdin (one per line, JSON
//                          {type:"user",message:{role:"user",content:"..."}})
//                          and for each emit:
//                          {type:"system", session_id:"<cli sid>"}
//                          {type:"assistant", message:{content:[{type:"text", text:"echo: <prompt>"}]}}
//                          {type:"result", usage:{input_tokens,output_tokens}}
//                          Default behaviour. The process keeps running, ready
//                          for the next prompt.
//
//   MOCK_MODE=text-only — same as multi-turn, only one turn then idle.
//
//   MOCK_MODE=exit-fast — read first prompt, respond with `error` NDJSON frame
//                          then exit 1 immediately.
//
// Echo strategy: each turn emits `echo: <prompt>` so the test can assert
// exactly that the *same* persistent child serviced both turns and that the
// second turn's text comes from stdin (proving the long-lived protocol).
import process from 'node:process';

const MODE = process.env.MOCK_MODE ?? 'multi-turn';
const FORCE_CLI_SID = process.env.MOCK_CLI_SESSION_ID ?? 'mock-claude-cli-session';
const MAX_TURNS = Number(process.env.MOCK_MAX_TURNS ?? 3);

// Swallow the claude-CLI-specific flags the provider spawns us with so the
// node binary doesn't barf on `--print --input-format stream-json ...`. Any
// `--session-id <uuid>` we DO honour — that's our stable test session id.
for (let i = 2; i < process.argv.length; i++) {
  const arg = process.argv[i];
  if (arg === '--session-id' && i + 1 < process.argv.length) {
    const sid = process.argv[i + 1];
    if (sid) process.env.MOCK_CLI_SESSION_ID = sid;
    i++;
  }
}

if (process.env.AGENTD_DEBUG) {
  process.stderr.write(`[mock-claude-cli] mode=${MODE} sid=${FORCE_CLI_SID} argv=${JSON.stringify(process.argv.slice(2))}\n`);
}

let processed = 0;
let buf = '';

function writeEvent(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  buf += chunk;
  let idx;
  while ((idx = buf.indexOf('\n')) !== -1) {
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (line.length === 0) continue;
    handleLine(line);
  }
});

process.stdin.on('end', () => {
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
  if (parsed?.type !== 'user') return;

  processed += 1;

  // First frame on every (re)connection/reuse: a `system` event carrying the
  // current cli session id. This mirrors what real Claude CLI emits.
  writeEvent({ type: 'system', session_id: FORCE_CLI_SID });

  if (MODE === 'exit-fast') {
    writeEvent({ type: 'error', message: 'mock: forced early exit after first prompt' });
    finish(/* exitCode */ 1);
    return;
  }

  // Always emit an assistant text turn echoing the prompt — so the assertion
  // can check the second turn produced output without spawning a new child.
  const promptText =
    parsed?.message?.content ?? '';
  writeEvent({
    type: 'assistant',
    message: {
      role: 'assistant',
      content: [{ type: 'text', text: `echo: ${promptText}` }],
    },
  });
  writeEvent({
    type: 'result',
    usage: { input_tokens: 1, output_tokens: promptText.length },
    is_error: false,
  });

  if (MODE === 'text-only') {
    finish();
  } else if (MODE === 'multi-turn' && processed >= MAX_TURNS) {
    finish();
  }
}

function finish(exitCode = 0) {
  setTimeout(() => {
    try {
      process.stdout.end();
    } catch {
      /* ignore */
    }
    setTimeout(() => process.exit(exitCode), 10);
  }, 10);
}

if (process.env.AGENTD_DEBUG) {
  process.stderr.write(`[mock-claude-cli] mode=${MODE} sid=${FORCE_CLI_SID} pid=${process.pid}\n`);
}
