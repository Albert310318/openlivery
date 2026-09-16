import assert from "node:assert/strict";
import { test } from "node:test";
import { resolveHostedServer } from "../src/hostedServer";

const template = "https://{workspace}.inbox.example";
test("a workspace name, copied hostname and HTTPS address resolve to the same server", () => {
  for (const input of ["sample-team", " SAMPLE-Team ", "sample-team.inbox.example", "https://sample-team.inbox.example/"]) {
    assert.equal(resolveHostedServer(input, template), "https://sample-team.inbox.example");
  }
});

test("copied addresses cannot send sign-in credentials to a different host or endpoint", () => {
  for (const input of ["", "a.b", "-team", "team-", "a".repeat(64),
    "http://sample-team.inbox.example", "https://sample-team.inbox.example.attacker.example",
    "https://attacker.example/sample-team.inbox.example", "https://user:password@sample-team.inbox.example",
    "https://sample-team.inbox.example:8443", "https://sample-team.inbox.example/api",
    "https://sample-team.inbox.example?next=elsewhere", "https://sample-team.inbox.example#fragment",
    "https://nested.sample-team.inbox.example", "https://inbox.example"]) {
    assert.equal(resolveHostedServer(input, template), null, input);
  }
});
