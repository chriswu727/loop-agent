let previousFocus = null;

export function openDialog(document) {
  throw new Error("not implemented");
}

export function closeDialog(document) {
  throw new Error("not implemented");
}

export function installDialog(document) {
  document.getElementById("open-dialog").addEventListener("click", () => openDialog(document));
  document.getElementById("close-dialog").addEventListener("click", () => closeDialog(document));
}
