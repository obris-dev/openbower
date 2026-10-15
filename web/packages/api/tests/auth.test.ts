import assert from "node:assert/strict";
import { test } from "node:test";

import { credentialPostIsSameSite, idpResumeUrl, resumeTargetsLoopback } from "../src/auth.ts";

const HOSTED_AUTH = "https://auth.example.io";

test("a credential post is same-site from the hosted app, from a dev stack on loopback, and nowhere else", () => {
  // The hosted app and the identity service share a registrable domain.
  assert.equal(credentialPostIsSameSite("app.example.io", HOSTED_AUTH), true);
  assert.equal(credentialPostIsSameSite("app.example.io:443", HOSTED_AUTH), true);
  // A dev stack: the web app and a local identity service, both loopback.
  assert.equal(credentialPostIsSameSite("localhost:3003", "http://localhost:8001"), true);
  assert.equal(credentialPostIsSameSite("127.0.0.1:3003", "http://localhost:8001"), true);
  // A self-hosted install against the hosted identity service: cross-site.
  assert.equal(credentialPostIsSameSite("localhost:3003", HOSTED_AUTH), false);
  // An install on its own domain: cross-site too.
  assert.equal(credentialPostIsSameSite("bower.acme.com", HOSTED_AUTH), false);
  // Garbage never reads as same-site.
  assert.equal(credentialPostIsSameSite("", HOSTED_AUTH), false);
  assert.equal(credentialPostIsSameSite("app.example.io", "not a url"), false);
});

test("the resume target is read off the authorize request's redirect_uri", () => {
  const loopback = "/oauth/authorize/?client_id=app&redirect_uri=" + encodeURIComponent("http://localhost:8002/v1/auth/callback");
  const hosted = "/oauth/authorize/?client_id=app&redirect_uri=" + encodeURIComponent("https://api.example.io/v1/auth/callback");
  assert.equal(resumeTargetsLoopback(loopback), true);
  assert.equal(resumeTargetsLoopback(hosted), false);
  assert.equal(resumeTargetsLoopback("/oauth/authorize/?client_id=app"), false);
  assert.equal(resumeTargetsLoopback(null), false);
  assert.equal(resumeTargetsLoopback("not a url and no query"), false);
});

test("a resume stays on the identity service: relative kept, its own origin kept, a foreign origin dropped", () => {
  const base = "http://localhost:8001";
  assert.equal(idpResumeUrl("/oauth/authorize/?x=1"), `${base}/oauth/authorize/?x=1`);
  assert.equal(idpResumeUrl(`${base}/oauth/authorize/?x=1`), `${base}/oauth/authorize/?x=1`);
  assert.equal(idpResumeUrl("https://evil.example/anything"), `${base}/`);
  assert.equal(idpResumeUrl("//evil.example/anything"), `${base}/`);
  assert.equal(idpResumeUrl(null), `${base}/`);
});
