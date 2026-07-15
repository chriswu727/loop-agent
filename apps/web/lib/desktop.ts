'use client';

import { useSyncExternalStore } from 'react';

const noDesktopState = () => null;
const noSubscription = () => () => undefined;

export function useDesktopState() {
  const bridge = typeof window === 'undefined' ? undefined : window.loopDesktop;
  return useSyncExternalStore(
    bridge?.onStateChange ?? noSubscription,
    bridge?.getCachedState ?? noDesktopState,
    noDesktopState,
  );
}
