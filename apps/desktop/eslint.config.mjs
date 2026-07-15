import base from '@repo/eslint-config';

export default [
  ...base,
  {
    ignores: ['runtime/**', 'out/**'],
  },
  {
    files: ['forge.config.cjs'],
    rules: {
      '@typescript-eslint/no-require-imports': 'off',
    },
  },
];
