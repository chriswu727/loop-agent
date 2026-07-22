import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { closeDialog, installDialog, openDialog } from "../dialog.mjs";

function fakeDocument() {
  const listeners = {};
  const opener = { focusCount: 0, addEventListener(type, fn) { listeners[`open:${type}`] = fn; }, focus() { this.focusCount++; } };
  const close = { focusCount: 0, addEventListener(type, fn) { listeners[`close:${type}`] = fn; }, focus() { this.focusCount++; } };
  const dialog = { hidden: true };
  return {
    activeElement: opener,
    listeners,
    opener,
    close,
    dialog,
    getElementById(id) { return { "open-dialog": opener, "close-dialog": close, "settings-dialog": dialog }[id]; },
    addEventListener(type, fn) { listeners[`document:${type}`] = fn; },
  };
}

test("HTML exposes a labelled modal dialog", () => {
  const html = fs.readFileSync("index.html", "utf8");
  assert.match(html, /role=["']dialog["']/);
  assert.match(html, /aria-modal=["']true["']/);
  assert.match(html, /aria-labelledby=["']dialog-title["']/);
});

test("open and close manage visibility and focus", () => {
  const document = fakeDocument();
  openDialog(document);
  assert.equal(document.dialog.hidden, false);
  assert.equal(document.close.focusCount, 1);
  closeDialog(document);
  assert.equal(document.dialog.hidden, true);
  assert.equal(document.opener.focusCount, 1);
});

test("Escape closes an open dialog", () => {
  const document = fakeDocument();
  installDialog(document);
  openDialog(document);
  document.listeners["document:keydown"]({ key: "Escape" });
  assert.equal(document.dialog.hidden, true);
});
