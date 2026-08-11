import { spawn, type ChildProcess } from 'node:child_process';
import type { Readable } from 'node:stream';
import {
  BaseProvider,
  type ProviderEvent,
  type SendMessageInput,
  type SendMessageResult,
} from './base.js';
import type { CustomProvider } from '../config/schema.js';
import { parseNdjson } from '../stream/ndjson.js';
import { logger } from '../util/logger.js';

/**
 * Claude Code provider — long-lived child-pool mode.
 *
 * Spawns ``claude --print --input-format stream-json --output-format stream-json
 * --verbose`` as a *persistent* per-session subprocess; subsequent turns in the
 * same agentd session reuse the same child by writing one NDJSON prompt
 * to stdin and reading a fresh result frame from stdout. This avoids the
 * per-turn cost of fork + MCP/CLAUDE.md loading + history replay (~1–8s on
 * cold cache), which is what made the previous per-call oneshot implementation
 * look dead on mobile clients.
 *
 * Protocol details:
 *   - Initial spawn CLI args:
 *       --print --input-format stream-json --output-format stream-json --verbose
 *       --session-id <uuid>           (one stable UUID per agentd session)
 *   - Stdin (one JSONLine per turn):
 *       { "type":"user",
 *         "message":{"role":"user","content":"<prompt>"} }
 *   - Stdout: stream-json NDJSON events until a `result` frame
 *     (sometimes with `is_error`) is emitted, after which the child is
 *     idle and waiting for the next prompt.
 *
 * Lifetime / cleanup:
 *   - child exit removes the slot from the pool. ``sessions.close(id)`` is
 *     NOT called from here — the SessionManager is the single source of
 *     truth and is already wired to TtlSweeper.
 *   - ``provider.shutdown()`` (added below via ``init``) walks all slots,
 *     sends SIGTERM, waits a grace period, and SIGKILL stragglers.
 */
export function buildClaudeProvider(
  discovered: { command: string; version: string } | null,
  cfg: CustomProvider | null,
): BaseProvider {
  const command = cfg?.command ?? discovered?.command ?? 'claude';
  const version = discovered?.version ?? 'unknown';
  return new ClaudeProvider(command, version);
}

interface PoolSlot {
  cliSessionId: string;
  child: ChildProcess;
  busy: boolean;
  lastUsedAt: number;
  /** Resolvers waiting for the previous turn to finish before they can write. */
  waiters: Array<() => void>;
  /** Captured stderr used for error messages once child exits non-zero. */
  stderrBuf: string;
  /**
   * Single async iterator over child.stdout shared by all turns in this
   * session.  Each turn reads frames until the next `result` then returns
   * without draining the rest of the stream — so the next turn can pick
   * up where this one stopped.  Created lazily on first send.
   */
  stdoutReader: AsyncIterableIterator<Buffer> | null;
  /** Leftover bytes in the stdout tail that didn't form a complete line yet. */
  stdoutTail: string;
  /** Becomes true when the child exits / EOFs stdout. */
  stdoutEof: boolean;
}

export class ClaudeProvider extends BaseProvider {
  readonly id = 'claude';
  readonly label = 'Claude Code';
  readonly protocol = 'stream-json' as const;

  /**
   * Per-agentd-session pool. Keyed by `input.sessionId` (the agentd UUID).
   * Sized by `maxConcurrentPerProvider` and reaped by TTL sweep in the
   * SessionManager — ClaudeProvider reuses the existing `sessions.close(id)`
   * to keep eviction consistent across layers.
   */
  private pool = new Map<string, PoolSlot>();

  /**
   * Concurrency cap. Defaults to 4 to mirror `cfg.maxConcurrentPerProvider`
   * from the daemon. Callers (daemon.ts) override this via init().
   */
  private maxPoolSize = 4;

  /** Idle TTL before a slot is reaped. Mirrors `cfg.sessionTtlSeconds`. */
  private idleTtlMs = 30 * 60 * 1000;

  /**
   * Maximum wall-clock time a single ``send()`` is allowed to occupy a
   * slot before the watchdog kills the child.  Without this, a wedged
   * Anthropic connection or a missed `result` frame would leave the slot
   * `busy=true` forever and the next acquire would spawn another cold
   * child while the old one lingers orphaned.
   *
   * 90s is comfortably above Anthropic's max streaming chat latency (~30s
   * for the longest real responses) but well below LiveKit's WS heartbeat
   * drop threshold so the worker can cleanly disconnect and the slot can
   * be reaped at session close.
   */
  private sendTimeoutMs = 90_000;

  /** Sweeper interval. ``unref()``-ed so it never blocks process exit. */
  private idleSweeper: NodeJS.Timeout | null = null;

  /**
   * The sentinel session id used by the prewarmed slot. When the first real
   * conversation arrives the prewarmed slot is re-keyed off this id.
   * Exposed so callers (daemon.ts) don't have to know about the literal
   * string.  See ``prewarm()`` below.
   */
  static readonly PREWARM_SESSION_ID = '__openvox_prewarm__';

  /** Optional logger label so subsystems are filterable. */
  private log = logger.child({ subsystem: 'claude-pool' });

  /**
   * Extra args injected between `command` and the claude CLI flags.
   * Used by tests to make the spawn look like `node fixture-path --print …`
   * while keeping the production call shape (`claude --print …`).
   */
  readonly extraSpawnArgs: ReadonlyArray<string>;

  constructor(
    readonly command: string,
    readonly cliVersion: string,
    extraSpawnArgs: ReadonlyArray<string> = [],
  ) {
    super();
    this.extraSpawnArgs = extraSpawnArgs;
  }

  /** Called by daemon bootstrap with the runtime config knobs. */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  override async init(cfg: any): Promise<void> {
    if (cfg && typeof cfg === 'object') {
      if (typeof cfg.maxPoolSize === 'number' && cfg.maxPoolSize > 0) {
        this.maxPoolSize = cfg.maxPoolSize;
      }
      if (typeof cfg.idleTtlMs === 'number' && cfg.idleTtlMs > 0) {
        this.idleTtlMs = cfg.idleTtlMs;
      }
    }
    // Lazy start the idle sweeper; non-blocking.
    if (!this.idleSweeper) {
      this.idleSweeper = setInterval(() => this.evictIdle(), 30_000);
      if (typeof this.idleSweeper.unref === 'function') this.idleSweeper.unref();
    }
  }

  async shutdown(): Promise<void> {
    if (this.idleSweeper) {
      clearInterval(this.idleSweeper);
      this.idleSweeper = null;
    }
    const slots = Array.from(this.pool.values());
    this.pool.clear();
    await Promise.all(
      slots.map(async (slot) => {
        await this.killSlot(slot, 5_000);
      }),
    );
  }

  // ── public send() ─────────────────────────────────────────────────────

  async send(input: SendMessageInput): Promise<SendMessageResult> {
    // Prefer the pre-extracted single-turn prompt when the route hands it
    // to us (chat.ts). Falling back to scanning the message array keeps
    // any other caller (tests, future routes) working unchanged.
    const prompt =
      input.prompt ?? (input.messages ? lastUserText(input.messages) : '');
    const sessionId = input.sessionId ?? `anon-${randomToken()}`;

    // Acquire (or create) a slot, then queue the prompt behind any in-flight turn.
    const slot = await this.acquireSlot(sessionId, input.resumeCliSessionId);
    if (!slot) {
      const max = this.maxPoolSize;
      return {
        events: (async function* (): AsyncGenerator<ProviderEvent, void, void> {
          yield { type: 'error', message: `claude pool exhausted (max ${max}); refusing new session`, fatal: false };
          yield { type: 'done' };
        })(),
      };
    }
    slot.busy = true;
    slot.lastUsedAt = Date.now();

    // Watchdog: if this turn doesn't complete within ``sendTimeoutMs``, the
    // claude CLI has hung (network drop, deadlock, missed `result` frame).
    // Kill the child so the *next* acquireSlot() spawns fresh state
    // instead of inheriting the wedged slot's `busy=true` permanently.
    const sendTimeoutMs = this.sendTimeoutMs;
    let watchdogFired = false;
    const watchdog = setTimeout(() => {
      watchdogFired = true;
      this.log.warn(
        { sessionId, pid: slot.child.pid, lastStderr: slot.stderrBuf.slice(-256) },
        'claude-pool: send() watchdog fired, killing child',
      );
      try {
        slot.child.kill('SIGTERM');
      } catch {
        /* ignore */
      }
    }, sendTimeoutMs);
    if (typeof watchdog.unref === 'function') watchdog.unref();

    // Set up cancellation: abort signal kills the child immediately.
    if (input.signal) {
      const onAbort = () => {
        try {
          slot.child.kill('SIGTERM');
        } catch {
          /* already dead */
        }
      };
      if (input.signal.aborted) onAbort();
      else input.signal.addEventListener('abort', onAbort, { once: true });
    }

    // Build the per-call event iterator. The slot's stdout is read by a
    // single shared async iterator per child; we slice out the events that
    // belong to THIS turn.
    const iterator = readTurnFromSlot(slot, prompt, input.resumeCliSessionId);

    return {
      events: (async function* () {
        try {
          // Surface the watchdog as a fatal error event so the consumer
          // (chat.ts / livekit worker) gets a clean termination and can
          // decide to retry on a fresh child.
          if (watchdogFired) {
            yield {
              type: 'error',
              message: `claude child wedged — send() watchdog killed it after ${sendTimeoutMs}ms`,
              fatal: true,
              restart: true,
            };
            yield { type: 'done' };
            return;
          }
          for await (const evt of iterator) {
            yield evt;
            if (evt.type === 'done' || (evt.type === 'error' && evt.fatal)) break;
          }
        } finally {
          clearTimeout(watchdog);
          slot.busy = false;
          slot.lastUsedAt = Date.now();
          // Wake the next turn waiting on this slot.
          const next = slot.waiters.shift();
          if (next) next();
        }
      })(),
    };
  }

  // ── pool mechanics ────────────────────────────────────────────────────

  private async acquireSlot(
    sessionId: string,
    resumeCliSessionId: string | undefined,
  ): Promise<PoolSlot | null> {
    // 1. Reuse existing slot if not busy and CLI session matches (or we have
    //    no resumeCliSessionId so any healthy slot is acceptable).
    const existing = this.pool.get(sessionId);
    if (existing && !existing.busy && !existing.child.killed && existing.child.exitCode === null) {
      if (
        resumeCliSessionId &&
        existing.cliSessionId !== resumeCliSessionId
      ) {
        await this.evictSlot(sessionId, existing);
      } else {
        return existing;
      }
    }
    if (existing) {
      // Stale slot — drop before creating a new one.
      await this.evictSlot(sessionId, existing);
    }

    // 2. Adopt the prewarmed boot slot before spawning cold. The prewarm
    //    slot lives under PREWARM_SESSION_ID with a still-empty CLI history
    //    and a captured cliSessionId the CLI emitted during boot. If the
    //    caller doesn't pin a specific cliSessionId, we re-key the slot
    //    under the real session id and forward the captured one so the
    //    next prompt continues the prewarmed conversation — i.e. **the
    //    first real conversation now skips cold-start entirely**.
    if (!resumeCliSessionId) {
      const prewarmed = this.pool.get(ClaudeProvider.PREWARM_SESSION_ID);
      if (
        prewarmed &&
        !prewarmed.busy &&
        !prewarmed.child.killed &&
        prewarmed.child.exitCode === null
      ) {
        this.pool.delete(ClaudeProvider.PREWARM_SESSION_ID);
        prewarmed.cliSessionId = prewarmed.cliSessionId; // keep captured
        this.pool.set(sessionId, prewarmed);
        this.log.info(
          {
            newSessionId: sessionId,
            cliSessionId: prewarmed.cliSessionId,
            pid: prewarmed.child.pid,
          },
          'claude-pool: adopted prewarm slot into fresh session',
        );
        return prewarmed;
      }
    }

    // 3. Pool size cap: if at capacity, reclaim the oldest-idle slot.
    if (this.pool.size >= this.maxPoolSize) {
      const evicted = this.evictOldestIdle();
      if (!evicted) return null;
    }

    // 4. Spawn a new child (cold-start path) when nothing else fits.
    const cliSessionId = randomUUID();
    return await this.spawnSlot(sessionId, cliSessionId, resumeCliSessionId);
  }

  /**
   * Boot-time warm-up: spawn one idle claude CLI child up front so the
   * first real conversation in a fresh daemon doesn't pay the cold-start
   * tax.  The slot lives under ``PREWARM_SESSION_ID``; the first real
   * ``send()`` adopts it (see ``acquireSlot``).
   *
   * Best-effort: a failed prewarm never blocks daemon startup.  Caller
   * is responsible for invoking this in ``daemon.ts`` *before* the HTTP
   * server starts accepting traffic.
   */
  async prewarm(): Promise<void> {
    if (this.pool.has(ClaudeProvider.PREWARM_SESSION_ID)) return;
    if (this.pool.size >= this.maxPoolSize) return;
    try {
      const slot = await this.spawnSlot(
        ClaudeProvider.PREWARM_SESSION_ID,
        randomUUID(),
        undefined,
      );
      slot.lastUsedAt = Date.now();
      this.log.info({ pid: slot.child.pid }, 'claude-pool: prewarmed');
    } catch (err) {
      this.log.warn({ err }, 'claude-pool: prewarm failed (continuing)');
    }
  }

  private async spawnSlot(
    sessionId: string,
    cliSessionId: string,
    resumeCliSessionId: string | undefined,
  ): Promise<PoolSlot> {
    const args: string[] = [
      ...this.extraSpawnArgs,
      '--print',
      '--input-format', 'stream-json',
      '--output-format', 'stream-json',
      '--verbose',
    ];
    if (resumeCliSessionId) {
      args.push('--resume', resumeCliSessionId);
    } else {
      args.push('--session-id', cliSessionId);
    }

    const child = spawn(this.command, args, {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env },
    });

    // Stderr capture stays — it's the only way for users to debug child errors.
    // We never attach stdout listeners here: the only consumer of
    // `child.stdout` is `parseNdjson` inside `readTurnFromSlot`, which uses
    // a single async-iterator reader. Adding a `.on('data', …)` listener
    // would race with it and lose data.
    child.on('exit', (code) => {
      this.pool.delete(sessionId);
      this.log.debug(
        { sessionId, code, stderr: slot.stderrBuf.slice(-256) },
        'claude-pool: child exited',
      );
    });

    const slot: PoolSlot = {
      cliSessionId,
      child,
      busy: false,
      lastUsedAt: Date.now(),
      waiters: [],
      stderrBuf: '',
      stdoutReader: null,
      stdoutTail: '',
      stdoutEof: false,
    };

    child.stderr?.on('data', (chunk: Buffer) => {
      slot.stderrBuf += chunk.toString('utf8');
      if (slot.stderrBuf.length > 4096) slot.stderrBuf = slot.stderrBuf.slice(-4096);
    });

    child.once('exit', (code) => {
      this.pool.delete(sessionId);
      // If exit was unexpected and a future send asks for this session, the
      // pool miss in acquireSlot() will simply spawn a fresh child.
      this.log.debug(
        { sessionId, code, stderr: slot.stderrBuf.slice(-256) },
        'claude-pool: child exited',
      );
    });

    this.pool.set(sessionId, slot);
    return slot;
  }

  private evictOldestIdle(): PoolSlot | null {
    const now = Date.now();
    let victimKey: string | null = null;
    let victimSlot: PoolSlot | null = null;
    let oldest = Infinity;
    for (const [k, s] of this.pool) {
      if (s.busy) continue;
      if (s.lastUsedAt < oldest) {
        oldest = s.lastUsedAt;
        victimKey = k;
        victimSlot = s;
      }
    }
    if (victimSlot && victimKey !== null) {
      void this.evictSlot(victimKey, victimSlot);
      return victimSlot;
    }
    // No idle slot — caller should block or fail.
    this.log.warn(
      { size: this.pool.size, max: this.maxPoolSize, now },
      'claude-pool: no idle slot to evict',
    );
    return null;
  }

  /**
   * Pop the oldest idle slot and re-key it under ``newSessionId``.
   * If the slot's CLI session id was captured during prewarm we forward
   * it as ``resumeCliSessionId`` so the next prompt carries the prior
   * context — the session effectively continues through the warmup.
   */
  private tryAdoptOldestIdle(
    newSessionId: string,
    resumeCliSessionId: string | undefined,
  ): PoolSlot | null {
    const slot = this.evictOldestIdle();
    if (!slot) return null;
    // Detach from old key (if any) and re-key.
    for (const [k, v] of this.pool) {
      if (v === slot) {
        this.pool.delete(k);
        break;
      }
    }
    slot.cliSessionId = resumeCliSessionId ?? slot.cliSessionId;
    this.pool.set(newSessionId, slot);
    this.log.info(
      { newSessionId, cliSessionId: slot.cliSessionId, pid: slot.child.pid },
      'claude-pool: adopted idle slot into real session',
    );
    return slot;
  }

  private async evictSlot(sessionId: string, slot: PoolSlot): Promise<void> {
    if (this.pool.get(sessionId) === slot) this.pool.delete(sessionId);
    await this.killSlot(slot, 1_000);
  }

  private evictIdle(): void {
    const now = Date.now();
    for (const [k, s] of Array.from(this.pool)) {
      if (!s.busy && now - s.lastUsedAt > this.idleTtlMs) {
        this.log.info({ sessionId: k, idleMs: now - s.lastUsedAt }, 'claude-pool: reap idle');
        void this.evictSlot(k, s);
      }
    }
  }

  private killSlot(slot: PoolSlot, graceMs: number): Promise<void> {
    return new Promise<void>((resolve) => {
      const child = slot.child;
      if (child.exitCode !== null) {
        resolve();
        return;
      }
      let forced = false;
      const onExit = () => {
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(() => {
        if (forced) return;
        forced = true;
        try {
          if (!child.killed && child.exitCode === null) child.kill('SIGKILL');
        } catch {
          /* ignore */
        }
      }, graceMs);
      if (typeof timer.unref === 'function') timer.unref();
      child.once('exit', onExit);
      try {
        child.kill('SIGTERM');
      } catch {
        /* already dead */
      }
    });
  }
}

// ── module-private helpers ─────────────────────────────────────────────

/**
 * Read events belonging to **one** turn from a slot's stdout.
 *
 * The slot owns a single persistent async iterator over its child's stdout;
 * we share it across turns by reading whole NDJSON lines out of a tail buffer
 * we maintain ourselves (parseNdjson drains the whole stream at once, which
 * is incompatible with multi-turn reuse).  Stop conditions for one turn:
 *
 *   - we see a `result` event (mapped to `done` or `usage` by mapClaudeEvent)
 *   - the child stdout EOFs (slot.stdoutEof)
 *   - the child process exits
 *   - we see a fatal error frame
 *
 * Anything left in `slot.stdoutTail` after the turn returns is preserved
 * for the *next* turn to read from.
 */
async function* readTurnFromSlot(
  slot: PoolSlot,
  prompt: string,
  resumeCliSessionId: string | undefined,
): AsyncGenerator<ProviderEvent, void, void> {
  const stdin = slot.child.stdin;

  if (!stdin || stdin.destroyed || stdin.writableEnded) {
    yield {
      type: 'error',
      message: `claude child stdin unavailable (exit code=${slot.child.exitCode ?? '?'})`,
      fatal: false,
    };
    yield { type: 'done' };
    return;
  }

  // Write the prompt.  After this, the child will stream a sequence of
  // NDJSON events ending with `result` (the per-turn boundary).
  const payload = {
    type: 'user',
    message: { role: 'user', content: prompt },
  };
  try {
    stdin.write(JSON.stringify(payload) + '\n');
  } catch (err) {
    yield { type: 'error', message: `claude stdin write failed: ${String(err)}`, fatal: false };
    yield { type: 'done' };
    return;
  }

  // Lazy-allocate the per-slot stdout iterator.  Calling Symbol.asyncIterator
  // multiple times on the same Readable would give independent iterators
  // each missing pieces of the stream, so we MUST cache one for the life
  // of the slot.
  if (!slot.stdoutReader) {
    slot.stdoutReader = (slot.child.stdout as Readable)[Symbol.asyncIterator]();
  }

  let ended = false;
  try {
    while (!ended && !slot.stdoutEof && slot.child.exitCode === null) {
      // Try to extract one complete line from stdoutTail.
      let nlIdx = slot.stdoutTail.indexOf('\n');
      while (nlIdx === -1 && !slot.stdoutEof && slot.child.exitCode === null) {
        // No complete line yet — pull another chunk from the persistent reader.
        const next = await slot.stdoutReader.next();
        if (next.done) {
          slot.stdoutEof = true;
          break;
        }
        slot.stdoutTail += next.value.toString('utf8');
        nlIdx = slot.stdoutTail.indexOf('\n');
      }

      if (nlIdx === -1) {
        // EOF without a trailing newline — try to flush whatever we have.
        if (slot.stdoutTail.trim().length > 0) {
          const trail = slot.stdoutTail.trim();
          slot.stdoutTail = '';
          try {
            const obj = JSON.parse(trail) as unknown;
            const evt = mapClaudeEvent(obj as Record<string, unknown>);
            if (evt) {
              yield evt;
              if (evt.type === 'done' || (evt.type === 'error' && evt.fatal)) {
                ended = true;
              }
            }
          } catch {
            /* malformed trailing line — ignore */
          }
        }
        break;
      }

      const line = slot.stdoutTail.slice(0, nlIdx).replace(/\r$/, '').trim();
      slot.stdoutTail = slot.stdoutTail.slice(nlIdx + 1);
      if (line.length === 0) continue;

      let obj: unknown;
      try {
        obj = JSON.parse(line);
      } catch {
        continue;
      }
      if (!obj || typeof obj !== 'object' || Array.isArray(obj)) continue;

      const evt = mapClaudeEvent(obj as Record<string, unknown>);
      if (!evt) continue;
      yield evt;
      // The `result` raw frame (regardless of whether mapClaudeEvent mapped
      // it to `usage` or `done`) is always the turn boundary.  Without this
      // check we'd hang waiting for the next frame from a multi-turn child
      // that has already finished its current turn.
      const rawType = String((obj as Record<string, unknown>)['type'] ?? '');
      if (
        rawType === 'result' ||
        evt.type === 'done' ||
        (evt.type === 'error' && evt.fatal)
      ) {
        ended = true;
        break;
      }
    }
  } catch (err) {
    yield { type: 'error', message: `claude stdout read: ${String(err)}`, fatal: true };
  }

  if (!ended) yield { type: 'done', stopReason: slot.stdoutEof ? 'eof' : 'end_turn' };
}

/** Convert a Claude Code stream-json event to a ProviderEvent. */
function mapClaudeEvent(obj: Record<string, unknown>): ProviderEvent | null {
  const type = String(obj['type'] ?? '');
  if (type === 'system') {
    const sid = obj['session_id'];
    if (typeof sid === 'string') return { type: 'session_id', id: sid };
    return null;
  }
  if (type === 'assistant') {
    const msg = obj['message'] as
      | { content?: Array<{ type: string; text?: string }> }
      | undefined;
    const text = msg?.content?.find((c) => c.type === 'text')?.text;
    if (text) return { type: 'text', delta: text };
    return null;
  }
  if (type === 'result') {
    const usage = obj['usage'] as
      | { input_tokens?: number; output_tokens?: number }
      | undefined;
    if (usage) {
      return {
        type: 'usage',
        inputTokens: usage.input_tokens ?? 0,
        outputTokens: usage.output_tokens ?? 0,
      };
    }
    return { type: 'done', stopReason: 'end_turn' };
  }
  if (type === 'error') {
    return { type: 'error', message: String(obj['message'] ?? 'unknown error') };
  }
  return null;
}

function lastUserText(
  messages: ReadonlyArray<{ role: string; content: string }>,
): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m && m.role === 'user' && m.content) return m.content;
  }
  return '';
}

function randomUUID(): string {
  return globalThis.crypto.randomUUID();
}

function randomToken(): string {
  return globalThis.crypto.randomUUID().replace(/-/g, '').slice(0, 8);
}
