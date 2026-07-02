import next from 'eslint-config-next';

// Next 16 ships a native flat config (parser + TS + core-web-vitals rules).
// Use it directly — layering the monorepo base's typed rules on top conflicts
// with Next's parser. FlatCompat is gone (it crashed on the plugin).
const eslintConfig = [
  { ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts', 'eslint.config.mjs'] },
  ...next,
];

export default eslintConfig;
