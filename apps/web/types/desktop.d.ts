import type { LoopDesktopApi } from '@repo/desktop-contract';

declare global {
  interface Window {
    loopDesktop?: LoopDesktopApi;
  }
}

export {};
