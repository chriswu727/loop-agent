import path from 'node:path';
import { MakerDeb } from '@electron-forge/maker-deb';
import { MakerSquirrel } from '@electron-forge/maker-squirrel';
import { MakerZIP } from '@electron-forge/maker-zip';
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
    new MakerSquirrel({ name: 'LoopDesktop' }, ['win32']),
    new MakerZIP({}, ['darwin']),
    new MakerDeb(
      {
        options: {
          bin: 'loop-desktop',
          homepage: 'https://github.com/chriswu727/loop-agent',
          maintainer: 'Loop contributors',
          name: 'loop-desktop',
        },
      },
      ['linux'],
    ),
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
