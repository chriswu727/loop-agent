import path from 'node:path';
import { FuseV1Options, FuseVersion } from '@electron/fuses';
import { FusesPlugin } from '@electron-forge/plugin-fuses';
import type { ForgeConfig } from '@electron-forge/shared-types';

const config: ForgeConfig = {
  packagerConfig: {
    appBundleId: 'com.loopagent.desktop',
    appCategoryType: 'public.app-category.developer-tools',
    asar: true,
    executableName: 'loop-desktop',
    extraResource: [path.resolve('runtime')],
    ignore: [/^\/node_modules($|\/)/, /^\/out($|\/)/, /^\/runtime($|\/)/, /^\/test($|\/)/],
    prune: false,
  },
  makers: [
    {
      name: '@electron-forge/maker-squirrel',
      platforms: ['win32'],
      config: { name: 'LoopDesktop' },
    },
    { name: '@electron-forge/maker-zip', platforms: ['darwin'], config: {} },
    {
      name: '@electron-forge/maker-deb',
      platforms: ['linux'],
      config: {
        options: {
          bin: 'loop-desktop',
          homepage: 'https://github.com/chriswu727/loop-agent',
          maintainer: 'Loop contributors',
          name: 'loop-desktop',
        },
      },
    },
  ],
  plugins: [
    new FusesPlugin({
      version: FuseVersion.V1,
      resetAdHocDarwinSignature: process.platform === 'darwin',
      strictlyRequireAllFuses: true,
      [FuseV1Options.RunAsNode]: false,
      [FuseV1Options.EnableCookieEncryption]: true,
      [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
      [FuseV1Options.EnableNodeCliInspectArguments]: false,
      [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
      [FuseV1Options.OnlyLoadAppFromAsar]: true,
      [FuseV1Options.LoadBrowserProcessSpecificV8Snapshot]: false,
      [FuseV1Options.GrantFileProtocolExtraPrivileges]: false,
      [FuseV1Options.WasmTrapHandlers]: true,
    }),
  ],
};

export default config;
