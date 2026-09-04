// Share concurrent reads and briefly reuse results. Scope keys by account/token.
export class ReadCache {
  private entries = new Map<string, { expires: number; value: unknown }>();
  private pending = new Map<string, Promise<unknown>>();
  private generation = 0;

  clear(): void {
    this.generation += 1;
    this.entries.clear();
    this.pending.clear();
  }

  async read<T>(key: string, ttl: number, load: () => Promise<T>): Promise<T> {
    const entry = this.entries.get(key);
    if (entry && entry.expires > Date.now()) return entry.value as T;
    const existing = this.pending.get(key);
    if (existing) return existing as Promise<T>;
    const generation = this.generation;
    const promise = load().then((value) => {
      if (generation === this.generation && ttl > 0) {
        this.entries.delete(key);
        this.entries.set(key, { expires: Date.now() + ttl, value });
        if (this.entries.size > 100) this.entries.delete(this.entries.keys().next().value!);
      }
      return value;
    });
    this.pending.set(key, promise);
    try { return await promise; }
    finally { if (this.pending.get(key) === promise) this.pending.delete(key); }
  }
}

export function refreshDelay(attempt: number): number | null {
  return attempt < 6 ? Math.min(10000 * 2 ** attempt, 30000) : null;
}
