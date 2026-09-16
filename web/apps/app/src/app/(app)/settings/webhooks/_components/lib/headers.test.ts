import assert from "node:assert/strict";
import { test } from "node:test";

import { MAX_WEBHOOK_HEADERS } from "@bower/api";

import { contentfulHeaders, headersProblem, urlProblem } from "./headers.ts";

test("blank rows are not headers; contentful rows are trimmed by name only", () => {
  assert.deepEqual(contentfulHeaders([{ name: "", value: "" }]), []);
  assert.deepEqual(contentfulHeaders([{ name: " Authorization ", value: " Bearer x " }]), [
    { name: "Authorization", value: " Bearer x " },
  ]);
});

test("headersProblem names the first broken row and its cause", () => {
  assert.equal(headersProblem([]), null);
  assert.equal(headersProblem([{ name: "", value: "" }]), null);
  assert.deepEqual(headersProblem([{ name: "", value: "x" }]), { index: 0, message: "Name this header." });
  assert.deepEqual(headersProblem([{ name: "X Bad", value: "x" }]), {
    index: 0,
    message: "Header names use letters, digits, and dashes.",
  });
  assert.deepEqual(headersProblem([{ name: "X-Token", value: "" }]), { index: 0, message: "Enter a value for X-Token." });
  assert.deepEqual(
    headersProblem([
      { name: "Authorization", value: "a" },
      { name: "authorization", value: "b" },
    ]),
    { index: 1, message: "Header names must be unique." },
  );
});

test("reserved names and unprintable values are caught before the server sees them", () => {
  // The server refuses both; catching them here keeps its refusal a
  // belt, never the first thing a user hears.
  assert.deepEqual(headersProblem([{ name: "Webhook-Signature", value: "v1,x" }]), {
    index: 0,
    message: "The Webhook-Signature header is set by every delivery.",
  });
  assert.equal(headersProblem([{ name: "X-A", value: "a\nb" }])?.message, "Header values use printable characters only, on one line.");
  assert.equal(headersProblem([{ name: "X-A", value: "Bearer a.b-c" }]), null);
});

test("the header cap mirrors the contract", () => {
  const rows = Array.from({ length: MAX_WEBHOOK_HEADERS + 1 }, (_, i) => ({ name: `X-${i}`, value: "v" }));
  assert.equal(headersProblem(rows)?.message, `A destination sends at most ${MAX_WEBHOOK_HEADERS} headers.`);
  assert.equal(headersProblem(rows.slice(0, MAX_WEBHOOK_HEADERS)), null);
});

test("urlProblem admits http(s) absolute URLs only", () => {
  assert.equal(urlProblem("https://hooks.example.com/in"), null);
  assert.equal(urlProblem("http://localhost:9000/x"), null);
  assert.equal(urlProblem(""), "Enter the URL to deliver to.");
  assert.equal(urlProblem("hooks.example.com/x"), "Enter an http or https URL.");
  assert.equal(urlProblem("ftp://hooks.example.com/x"), "Enter an http or https URL.");
  assert.match(urlProblem(`https://hooks.example.com/${"a".repeat(3000)}`) ?? "", /capped/);
});

test("urlProblem mirrors the server's host rule, so its field 400 stays unreachable", () => {
  // The server's URL validator wants a dotted name with a real top-level
  // label, localhost, or an IP literal; labels never carry underscores.
  // new URL() accepts all of these, so the mirror must refuse them.
  assert.equal(urlProblem("https://hooks"), "Enter a full host name, like hooks.example.com.");
  assert.equal(urlProblem("http://receiver:8080/hook"), "Enter a full host name, like hooks.example.com.");
  assert.equal(urlProblem("https://my_host.example.com/x"), "Enter a full host name, like hooks.example.com.");
  assert.equal(urlProblem("http://host.docker.internal:8787/ok"), null);
  assert.equal(urlProblem("http://10.0.0.5:9/x"), null);
  assert.equal(urlProblem("http://[::1]:9/x"), null);
});
