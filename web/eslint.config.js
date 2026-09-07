// eslint.config.js — flat config (ESLint 9) для веб-редакции (Vue 3 + Vite).
// Задача — ловить РЕАЛЬНЫЕ ошибки (необъявленные переменные, битые шаблоны Vue),
// а не воевать со стилем: форматирование отдано prettier (eslint-config-prettier
// в конце отключает конфликтующие стилевые правила). Внедряем мягко: часть шумных
// правил приглушена до warn/off, чтобы линтер был полезным, а не игнорируемым.
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import prettier from 'eslint-config-prettier'
import globals from 'globals'

export default [
  { ignores: ['dist/**', 'android/**', 'node_modules/**', '*.config.js'] },
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        //Подставляется Vite на сборке (build/buildStamp.js). Для линтера это обычный
        //глобал: объявить надо, иначе `no-undef` ругается на живой и рабочий код.
        __BUILD_STAMP__: 'readonly',
      },
    },
    rules: {
      // У нас есть одностраничные компоненты вроде Login.vue / Profile.vue — это норма.
      'vue/multi-word-component-names': 'off',
      // Пропсы без дефолта — осознанный приём в наших компонентах.
      'vue/require-default-prop': 'off',
      // Неиспользуемое — предупреждение (не блок), аргументы с префиксом _ игнорируем.
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      // Чистая раскладка атрибутов — дело форматтера (prettier), не линтера: глушим,
      // чтобы предупреждения линтера означали реальные проблемы, а не переносы строк.
      'vue/first-attribute-linebreak': 'off',
      'vue/attributes-order': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/max-attributes-per-line': 'off',
    },
  },
  prettier,
]
