import { describe, expect, it } from 'vitest';
import { spawnProc } from '../../src/util/process.js';

describe('util/process.spawnProc', () => {
  it('spawns /bin/echo and returns a handle', () => {
    const { child } = spawnProc({ command: '/bin/echo', args: ['hello'] });
    expect(child.pid).toBeGreaterThan(0);
    expect(typeof child.kill).toBe('function');
  });

  it('killGraceful terminates a long-running child', async () => {
    const { child, killGraceful } = spawnProc({
      command: '/bin/sh', args: ['-c', 'sleep 30'], killGraceMs: 100,
    });
    expect(child.exitCode).toBeNull();
    killGraceful();
    await new Promise<void>((res) => {
      const t = setInterval(() => {
        if (child.exitCode !== null || child.signalCode !== null) {
          clearInterval(t); res();
        }
      }, 50);
    });
    expect(child.exitCode !== null || child.signalCode !== null).toBe(true);
  });

  it('respects an AbortSignal by killing the child', async () => {
    const ac = new AbortController();
    const { child } = spawnProc({
      command: '/bin/sh', args: ['-c', 'sleep 30'], signal: ac.signal,
    });
    expect(child.exitCode).toBeNull();
    ac.abort();
    await new Promise<void>((res) => {
      const t = setInterval(() => {
        if (child.exitCode !== null || child.signalCode !== null) {
          clearInterval(t); res();
        }
      }, 50);
    });
    expect(child.exitCode !== null || child.signalCode !== null).toBe(true);
  });
});