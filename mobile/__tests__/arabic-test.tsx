import { afterEach, describe, expect, it, jest } from '@jest/globals';
import { render, screen } from '@testing-library/react-native';
import fs from 'fs';
import path from 'path';

import { ThemedText } from '@/components/themed-text';
import { setLanguage, t } from '@/i18n';
import { AR } from '@/locales/ar';

// The language is kept in a file on the phone: in memory here.
jest.mock('expo-file-system', () => {
  class File {
    exists = false;
    create() {}
    textSync() {
      return '';
    }
    writeSync() {}
  }
  return { File, Paths: { document: 'documents' } };
});

describe('the app in Arabic', () => {
  afterEach(() => setLanguage('en'));

  it('translates, fills in the slots, and leaves names and server text as they are', () => {
    setLanguage('ar');
    expect(t('Sign out')).toBe(AR['Sign out']);
    expect(t('{count} new messages', { count: 3 })).toBe('3 رسائل جديدة');
    expect(t(' · urgent')).toBe(' ' + AR['· urgent']);  // the spaces around a piece stay
    expect(t('Walkthrough Gym')).toBe('Walkthrough Gym');
    setLanguage('en');
    expect(t('{count} new messages', { count: 3 })).toBe('3 new messages');
  });

  it('every text on screen goes through the dictionary, read from the right', async () => {
    setLanguage('ar');
    await render(<ThemedText>Lead: {'Omar'}</ThemedText>);
    const text = screen.getByText(/Omar/);
    expect(screen.getByText(`${AR['Lead:']} Omar`)).toBeTruthy();
    expect(text.props.style).toEqual(expect.arrayContaining([expect.objectContaining({ textAlign: 'right' })]));
  });

  it('keeps the company name in English', () => {
    setLanguage('ar');
    expect(t('Profit Sports Solutions')).toBe('Profit Sports Solutions');
  });

  it("has Arabic for every t('…') in the app — a new text can't stay English unnoticed", () => {
    const missing: string[] = [];
    const walk = (dir: string) => {
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
});
