import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import hooks from 'eslint-plugin-react-hooks'

export default [
  { ignores: ['node_modules/**', 'dist/**', 'src/generated/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  { files: ['**/*.{ts,tsx}'], languageOptions: { globals: { ...globals.browser, ...globals.node } }, plugins: { 'react-hooks': hooks }, rules: { ...hooks.configs.recommended.rules } },
  { files: ['src/ui/**/*.{ts,tsx}'], rules: { 'no-restricted-imports': ['error', { patterns: ['../app/*', '../pages/*', '../mock/*', '../generated/*'] }] } },
  { files: ['**/*.{js,mjs}'], languageOptions: { globals: globals.node } },
]
