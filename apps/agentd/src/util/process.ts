/**
 * Subprocess helpers — spawn + graceful kill.
 *
 * Kept small so providers can compose freely; the heavy lifting lives in
 * `stream/ndjson.ts` and `stream/openai-shape.ts`.
 */
import { spawn, type ChildProcess } from 'node:child_process';

export interface SpawnOptions {
  command: string;
  args: string[];
  env?: NodeJS.ProcessEnv;
  signal?: AbortSignal;
  /** ms before SIGKILL after SIGTERM. Default 5000. */
  killGraceMs?: number;
}

export interface SpawnedProc {
  child: ChildProcess;
  /** Send SIGTERM, then SIGKILL after `killGraceMs` if still alive. */
  killGraceful(): void;
}

/**
 * Spawn a subprocess with an AbortSignal listener.
 * Returns the child plus a `killGraceful` helper that handles the
 * SIGTERM → SIGKILL dance used by SessionManager.close().
 */
export function spawnProc(opts: SpawnOptions): SpawnedProc {
  const child = spawn(opts.command, opts.args, {
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, ...(opts.env ?? {}) },
  }) as ChildProcess;

  const grace = opts.killGraceMs ?? 5_000;
  let killTimer: NodeJS.Timeout | null = null;
  const killGraceful = () => {
    try {
      child.kill('SIGTERM');
    } catch {
      /* already dead */
    }
    if (killTimer) return;
    killTimer = setTimeout(() => {
      try {
        if (!child.killed && child.exitCode === null) child.kill('SIGKILL');
      } catch {
        /* ignore */
      }
    }, grace);
    if (typeof killTimer.unref === 'function') killTimer.unref();
  };

  if (opts.signal) {
    if (opts.signal.aborted) killGraceful();
    else {
      opts.signal.addEventListener('abort', killGraceful, { once: true });
    }
  }

  return { child, killGraceful };
}