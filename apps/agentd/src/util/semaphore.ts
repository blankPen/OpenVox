/**
 * Counting semaphore — limits concurrent in-flight operations per key.
 *
 * Used to cap concurrent sessions per provider.
 */
export class Semaphore {
  private acquired = 0;
  private waiters: Array<() => void> = [];

  constructor(private readonly capacity: number) {}

  async acquire(): Promise<void> {
    if (this.acquired < this.capacity) {
      this.acquired += 1;
      return;
    }
    await new Promise<void>((res) => this.waiters.push(res));
    this.acquired += 1;
  }

  release(): void {
    this.acquired = Math.max(0, this.acquired - 1);
    const next = this.waiters.shift();
    if (next) next();
  }

  get capacityValue(): number {
    return this.capacity;
  }

  get inFlight(): number {
    return this.acquired;
  }

  async run<T>(fn: () => Promise<T>): Promise<T> {
    await this.acquire();
    try {
      return await fn();
    } finally {
      this.release();
    }
  }
}

/** A map of `name → Semaphore` — one slot per provider id. */
export class SemaphoreTable {
  private readonly sems = new Map<string, Semaphore>();

  constructor(private readonly makeCapacity: (name: string) => number) {}

  get(name: string): Semaphore {
    let s = this.sems.get(name);
    if (!s) {
      s = new Semaphore(this.makeCapacity(name));
      this.sems.set(name, s);
    }
    return s;
  }
}
