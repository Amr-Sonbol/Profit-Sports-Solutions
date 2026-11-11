import { File, Paths } from 'expo-file-system';
import { Alert, type AlertButton, type AlertOptions } from 'react-native';

import { AR } from '@/locales/ar';

/**
 * The app's language: the person's country's (Arabic in Egypt and Saudi
 * Arabia — /api/me "app_language"), right to left for Arabic. Texts are
 * written in English in the code and looked up in one Arabic dictionary
 * (locales/ar.ts) as they're shown — ThemedText and TextField do it for
 * whatever they show; anything else calls t(). Kept on the phone, so the
 * sign-in screen is in the last language too.
 */
export type Language = 'en' | 'ar';

const SAVED = 'app-language.txt';

function saved(): Language {
  try {
    const file = new File(Paths.document, SAVED);
    return file.exists && file.textSync() === 'ar' ? 'ar' : 'en';
  } catch {
    return 'en';
  }
}

let current: Language = saved();

export function language(): Language {
  return current;
}

export function isRTL() {
  return current === 'ar';
}

export function setLanguage(next: Language) {
  if (next === current) {
    return;
  }
  current = next;
  try {
    const file = new File(Paths.document, SAVED);
    if (!file.exists) {
      file.create();
    }
    file.writeSync(next);
  } catch {
    // Storage unavailable: the next start just begins in English.
  }
}

/**
 * The text in the app's language, with {name} filled in from `values`. Spaces
 * around it are kept (a piece of a sentence next to a number); anything not in
 * the dictionary — a customer's name, what the server already translated —
 * comes back as it is.
 */
export function t(text: string, values?: Record<string, string | number>): string {
  let out = text;
  if (current === 'ar') {
    const core = text.trim();
    const found = core ? AR[core] : undefined;
    if (found !== undefined) {
      out = text.slice(0, text.indexOf(core)) + found + text.slice(text.indexOf(core) + core.length);
    }
  }
  if (values) {
    for (const [name, value] of Object.entries(values)) {
      out = out.split(`{${name}}`).join(String(value));
    }
  }
  return out;
}

/** For dates and times: Arabic month and day names with Western digits, as on the website; else the phone's own. */
export function locale(): string | undefined {
  return current === 'ar' ? 'ar-u-nu-latn' : undefined;
}

/** Alert.alert, its title, message and buttons in the app's language. */
export function alert(title: string, message?: string, buttons?: AlertButton[], options?: AlertOptions) {
  Alert.alert(
    t(title), message === undefined ? undefined : t(message),
    buttons?.map((button) => ({ ...button, text: button.text === undefined ? undefined : t(button.text) })),
    options,
  );
}
