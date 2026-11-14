import { BASE_LEAD_MS } from '@kashi/view/timing-offset';
import { describe, expect, it } from 'vitest';
import { BASE_LEAD_MS as SERVER_READ_COPY } from '../shared/timing-offset.js';

describe('the lead the server audit reads', () => {
  it('the copy at the old path (read by apps/server/tests/test_audit.py) is the view\'s lead', () => {
    // A temporary twin: the module moved to packages/view, the server test
    // still reads apps/overlay/src/shared/timing-offset.ts. See that file.
    expect(SERVER_READ_COPY).toBe(BASE_LEAD_MS);
  });
});
