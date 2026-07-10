import { describe, expect, it } from 'vitest';
import { ENQUEUE_AFTER_MS, EnqueueGate } from './enqueue-gate.js';

describe('EnqueueGate', () => {
  it('fires once after 20 s of uninterrupted playing', () => {
    const gate = new EnqueueGate();
    gate.serverMiss('k1', 0, true);
    expect(gate.tick(ENQUEUE_AFTER_MS - 1)).toBeNull();
    expect(gate.tick(ENQUEUE_AFTER_MS)).toBe('k1');
    expect(gate.tick(ENQUEUE_AFTER_MS * 2)).toBeNull(); // never twice
  });

  it('does not fire when the track is skipped early', () => {
    const gate = new EnqueueGate();
    gate.serverMiss('k1', 0, true);
    gate.tick(15_000);
    gate.trackChanged(); // skipped at 15 s
    expect(gate.armed).toBe(false);
    expect(gate.tick(60_000)).toBeNull();
  });

  it('pause FREEZES the accumulator instead of resetting it', () => {
    const gate = new EnqueueGate();
    gate.serverMiss('k1', 0, true);
    gate.playback(false, 10_000); // paused at 10 s of listening
    expect(gate.tick(100_000)).toBeNull(); // long pause adds nothing
    gate.playback(true, 100_000); // resume
    expect(gate.tick(105_000)).toBeNull(); // 15 s total
    expect(gate.tick(110_000)).toBe('k1'); // 20 s total
  });

  it('starts paused when the miss arrives while not playing', () => {
    const gate = new EnqueueGate();
    gate.serverMiss('k1', 0, false);
    expect(gate.tick(30_000)).toBeNull(); // nothing accumulated
    gate.playback(true, 30_000);
    expect(gate.tick(50_000)).toBe('k1');
  });

  it('a second miss for the same fired key stays quiet', () => {
    const gate = new EnqueueGate();
    gate.serverMiss('k1', 0, true);
    gate.tick(ENQUEUE_AFTER_MS);
    gate.serverMiss('k1', 50_000, true); // e.g. a replayed lookup
    expect(gate.tick(100_000)).toBeNull();
  });

  it('a miss for a NEW key re-arms', () => {
    const gate = new EnqueueGate();
    gate.serverMiss('k1', 0, true);
    gate.tick(ENQUEUE_AFTER_MS);
    gate.trackChanged();
    gate.serverMiss('k2', 100_000, true);
    expect(gate.tick(100_000 + ENQUEUE_AFTER_MS)).toBe('k2');
  });
});
