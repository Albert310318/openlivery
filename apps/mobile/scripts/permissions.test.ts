import assert from "node:assert/strict";
import { test } from "node:test";
import type { Session } from "../src/api";
import { hasPermission } from "../src/permissions";

test("current server grants control access and a role name never overrides them", () => {
  const session = { role: "admin", permissions: ["reports.view"] } as Session;
  assert.equal(hasPermission(session, "reports.view"), true);
  assert.equal(hasPermission(session, "teams.manage"), false);
  assert.equal(hasPermission({ ...session, permissions: [] }, "reports.view"), false);
  assert.equal(hasPermission({ ...session, permissions: undefined }, "reports.view"), false);
});
