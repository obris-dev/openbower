// A signal's banner: one at a time, the one that stops new rounds first,
// and none for a state this bundle cannot name.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  LISTENER_STATUS,
  LOG_STATUS,
  UNKNOWN_LISTENER_STATUS,
  UNKNOWN_LOG_STATUS,
  type LogDetail,
} from "@bower/api";

import { BANNER_FULL, BANNER_HELD, BANNER_LISTENER_GONE, BANNER_RECONNECT } from "../copy.ts";
import { BANNER_ACTION, isHeld, signalBanner } from "./signal-banner.ts";

function log(status: LogDetail["status"], listenerStatus: string | null): LogDetail {
  const listener = listenerStatus === null ? null : { status: listenerStatus };
  return { status, listener } as unknown as LogDetail;
}

test("each state that stops new rounds has its banner and its action", () => {
  assert.deepEqual(signalBanner(log(LOG_STATUS.LISTENER_GONE, null)), {
    tone: "danger",
    text: BANNER_LISTENER_GONE,
    action: null,
  });
  assert.deepEqual(signalBanner(log(LOG_STATUS.RECONNECT, LISTENER_STATUS.ACTIVE)), {
    tone: "warning",
    text: BANNER_RECONNECT,
    action: BANNER_ACTION.RECONNECT,
  });
  assert.equal(signalBanner(log(LOG_STATUS.FULL, LISTENER_STATUS.ACTIVE))?.text, BANNER_FULL);
  assert.deepEqual(signalBanner(log(LOG_STATUS.ACTIVE, LISTENER_STATUS.HELD)), {
    tone: "warning",
    text: BANNER_HELD,
    action: BANNER_ACTION.EDIT,
  });
});

test("when several hold, the one that stops new rounds first wins", () => {
  assert.equal(signalBanner(log(LOG_STATUS.RECONNECT, LISTENER_STATUS.HELD))?.action, BANNER_ACTION.RECONNECT);
  assert.equal(signalBanner(log(LOG_STATUS.FULL, LISTENER_STATUS.HELD))?.text, BANNER_FULL);
});

test("a signal that fills, retries on its own, or reads unknown shows none", () => {
  // FAILS if an unknown state claims something: a stale bundle cannot know
  // what a person should do about it.
  assert.equal(signalBanner(null), null);
  assert.equal(signalBanner(log(LOG_STATUS.ACTIVE, LISTENER_STATUS.ACTIVE)), null);
  assert.equal(signalBanner(log(LOG_STATUS.ACTIVE, LISTENER_STATUS.RETRYING)), null);
  assert.equal(signalBanner(log(UNKNOWN_LOG_STATUS, UNKNOWN_LISTENER_STATUS)), null);
});

test("the pill reads paused exactly when the held banner shows", () => {
  // FAILS if the pill and the banner disagree: Active beside "paused until
  // it is edited".
  for (const [status, listener] of [
    [LOG_STATUS.ACTIVE, LISTENER_STATUS.HELD],
    [LOG_STATUS.ACTIVE, LISTENER_STATUS.ACTIVE],
    [LOG_STATUS.FULL, LISTENER_STATUS.HELD],
    [LOG_STATUS.RECONNECT, LISTENER_STATUS.HELD],
  ] as const) {
    const shown = signalBanner(log(status, listener))?.action === BANNER_ACTION.EDIT;
    assert.equal(isHeld(log(status, listener)), shown, `${status} / ${listener}`);
  }
});
