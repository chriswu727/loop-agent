import { describe, expect, it } from 'vitest';
import { isTrustedRendererUrl, secureWindowOptions } from '../src/security.js';

describe('desktop renderer boundary', () => {
  it('allows only the bundled shell and fixed loopback web origin', () => {
    expect(isTrustedRendererUrl('loop-desktop://app/onboarding.html')).toBe(true);
    expect(isTrustedRendererUrl('http://127.0.0.1:3000/tasks/123')).toBe(true);
    expect(isTrustedRendererUrl('http://localhost:3000')).toBe(false);
    expect(isTrustedRendererUrl('https://example.com')).toBe(false);
    expect(isTrustedRendererUrl('not a url')).toBe(false);
  });

  it('keeps renderer privileges disabled', () => {
    const options = secureWindowOptions('/tmp/preload.cjs');
    expect(options.webPreferences).toMatchObject({
      allowRunningInsecureContent: false,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    });
  });
});
