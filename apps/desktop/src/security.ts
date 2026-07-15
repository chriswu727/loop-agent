import type { BrowserWindowConstructorOptions } from 'electron';

export const LOCAL_WEB_ORIGIN = 'http://127.0.0.1:3000';
export const DESKTOP_ORIGIN_PREFIX = 'loop-desktop://app/';

export function isTrustedRendererUrl(url: string): boolean {
  if (url.startsWith(DESKTOP_ORIGIN_PREFIX)) return true;
  try {
    return new URL(url).origin === LOCAL_WEB_ORIGIN;
  } catch {
    return false;
  }
}

export function secureWindowOptions(preload: string): BrowserWindowConstructorOptions {
  return {
    backgroundColor: '#0b0d12',
    height: 900,
    minHeight: 640,
    minWidth: 880,
    show: false,
    title: 'Loop',
    width: 1280,
    webPreferences: {
      allowRunningInsecureContent: false,
      contextIsolation: true,
      nodeIntegration: false,
      preload,
      sandbox: true,
      spellcheck: true,
      webSecurity: true,
    },
  };
}
