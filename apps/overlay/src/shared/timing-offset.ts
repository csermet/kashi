/**
 * NOT the source. The lead and the user's offset live in the shared view
 * (@kashi/view/timing-offset, packages/view/src/timing-offset.ts) since client
 * review E.4.
 *
 * This file stays only because apps/server/tests/test_audit.py reads its TEXT
 * to pin the audit tools' mirror of the lead (apps/server/audit/moment.py
 * BASE_LEAD_MS), and the server was out of bounds for the change that moved
 * the module. lead-mirror.test.ts pins the literal below to the view's value.
 * Delete this file once the server test reads packages/view/src/timing-offset.ts.
 */
export const BASE_LEAD_MS = 200;
