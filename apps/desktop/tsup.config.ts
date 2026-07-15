import { defineConfig } from 'tsup';

export default defineConfig({
  clean: true,
  entry: {
    main: 'src/main.ts',
    preload: 'src/preload.ts',
  },
  external: ['electron'],
  format: ['cjs'],
  minify: false,
  outExtension: () => ({ js: '.cjs' }),
  sourcemap: true,
  splitting: false,
  target: 'node22',
});
