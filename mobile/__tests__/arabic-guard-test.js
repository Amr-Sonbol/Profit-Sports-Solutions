// Every t('…') in the app has its Arabic (src/locales/ar.ts) — so a new text can't
// stay English unnoticed. Plain JavaScript: it reads the source files with Node,
// which the app's own (phone) types don't include. For plain JSX text, run
// `node scripts/arabic-missing.js`.
const fs = require('fs');
const path = require('path');

const { AR } = require('../src/locales/ar');

test("every t('…') in the app has its Arabic", () => {
  const missing = [];
  const walk = (dir) => {
    for (const name of fs.readdirSync(dir)) {
      const full = path.join(dir, name);
      if (fs.statSync(full).isDirectory()) {
        walk(full);
      } else if (/\.tsx?$/.test(name) && !full.includes('locales') && !name.startsWith('i18n')) {
        for (const match of fs.readFileSync(full, 'utf8').matchAll(/\bt\(\s*(['"])((?:(?!\1)[^\\]|\\.)*)\1/g)) {
          const text = match[2].replace(/\\'/g, "'").trim();
          if (text && !(text in AR)) {
            missing.push(`${name}: ${text}`);
          }
        }
      }
    }
  };
  walk(path.join(__dirname, '..', 'src'));
  expect(missing).toEqual([]);
});
