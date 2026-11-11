// The app's texts that have no Arabic yet (src/locales/ar.ts), read with the
// TypeScript parser: JSX text, t()/alert()/confirmAction() texts, text-like
// props, and string literals that read as words. Run from mobile/:
//   node scripts/arabic-missing.js
// then add each one's Arabic to src/locales/ar.ts (the website's wording where
// it has the same text: locale/ar/LC_MESSAGES/django.po). Some it lists may be
// code rather than text people see — skip those.
const fs = require('fs');
const path = require('path');
const ts = require('typescript');

const found = new Set();
const add = (text) => {
  const clean = text.replace(/&apos;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();
  if (clean.length >= 2 && /[A-Za-z]{2}/.test(clean)) found.add(clean);
};
const TEXT_PROPS = new Set(['placeholder', 'label', 'title', 'accessibilityLabel', 'confirmLabel', 'text', 'emptyText']);
const looksLikeWords = (s) =>
  (/^[A-Z][a-z]/.test(s) && !/^[A-Z][a-zA-Z]+$/.test(s) ? true : /^[A-Z][a-z]+$/.test(s)) ||
  (/ /.test(s) && /[a-z]{2}/.test(s) && !/[_/\\]|^\.|https?:|\.\w+$|^[a-z]+(-[a-z]+)+$/.test(s));

function walk(dir) {
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    if (fs.statSync(full).isDirectory()) walk(full);
    else if (/\.tsx?$/.test(name) && !/\.d\.ts$/.test(name) && !full.includes(`${path.sep}locales${path.sep}`)
             && !full.endsWith(`${path.sep}i18n.ts`)) read(full);
  }
}

function read(file) {
  const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true,
    file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const visit = (node) => {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) return;
    if (ts.isJsxText(node)) add(node.text);
    else if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      const parent = node.parent;
      const text = node.text;
      const callee = parent && ts.isCallExpression(parent) ? parent.expression.getText(source) : '';
      if (['t', 'alert', 'confirmAction', 'setError', 'setNotice', 'setNextError', 'setSignError', 'Alert.alert'].includes(callee)) add(text);
      else if (parent && ts.isJsxAttribute(parent) && TEXT_PROPS.has(parent.name.getText(source))) add(text);
      else if (parent && (ts.isPropertyAssignment(parent) && parent.name === node)) { /* a key */ }
      else if (parent && ts.isElementAccessExpression(parent)) { /* obj['key'] */ }
      else if (parent && ts.isCallExpression(parent) && /^(require|StyleSheet|router\.|apiRequest|fetch|withOfflineCopy|withCountryCopy|cacheScreenData|Ionicons|new File|new Date|encodeURIComponent|getItem|setItem)/.test(callee)) { /* code */ }
      else if (looksLikeWords(text)) add(text);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
}

walk(path.join(process.cwd(), 'src'));
const dictionary = fs.readFileSync(path.join(process.cwd(), 'src', 'locales', 'ar.ts'), 'utf8');
const known = new Set([...dictionary.matchAll(/^ {2}("(?:[^"\\]|\\.)*"):/gm)].map((m) => JSON.parse(m[1])));
const SKIP = /^\.m-signature|^True$|^useSession must/;
const missing = [...found].filter((text) => !known.has(text) && !SKIP.test(text)).sort();
console.log(missing.length ? missing.join('\n') : 'Every text has its Arabic.');
process.exitCode = missing.length ? 1 : 0;
