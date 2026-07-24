/**
 * Idle-TTL sweeper — kills sessions whose last activity is older than `ttlSec`.
 *
 * Implemented as a setInterval that fires every ttl/4 (clamped between 1s and 30s).
 */

import type { SessionManager } from './manager.js';

export interface TtlOptions {
  ttlSeconds: number;
  onExpired?: (id: string) => void;
}

export class TtlSweeper {
  private timer: NodeJS.Timeout | null = null;

  constructor(
    private readonly sessions: SessionManager,
    private readonly opts: TtlOptions,
  ) {}

  start(): void {
    if (this.timer) return;
    const intervalMs = Math.min(30_000, Math.max(1_000, Math.floor((this.opts.ttlSeconds * 1000) / 4)));
    this.timer = setInterval(() => this.tick().catch(() => undefined), intervalMs);
    if (typeof this.timer.unref === 'function') this.timer.unref();
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  async tick(): Promise<void> {
    const now = Date.now();
    const ttlMs = this.opts.ttlSeconds * 1000;
    for (const s of this.sessions.list()) {
      const last = Date.parse(s.lastActiveAt);
      if (Number.isFinite(last) && now - last > ttlMs) {
        await this.sessions.close(s.id);
        this.opts.onExpired?.(s.id);
      }
    }
  }
}
