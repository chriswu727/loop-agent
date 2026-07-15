const path = require('node:path');
const { MakerDeb } = require('@electron-forge/maker-deb');
const { MakerSquirrel } = require('@electron-forge/maker-squirrel');
const { MakerZIP } = require('@electron-forge/maker-zip');
const { FuseV1Options, FuseVersion } = require('@electron/fuses');
const { FusesPlugin } = require('@electron-forge/plugin-fuses');

/** @type {import('@electron-forge/shared-types').ForgeConfig} */
const config = {
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

module.exports = config;
