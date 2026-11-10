import Constants from 'expo-constants';
import { Platform } from 'react-native';

import { apiRequest } from '@/api/client';

/**
 * Sends an error the app hit to the office (api.views.AppErrorView), so a
 * failure on a phone in the field isn't lost. Never throws, never waits:
 * reporting must not cause a second problem.
 */
export function reportError(error: unknown, screen = '') {
  const err = error instanceof Error ? error : new Error(String(error));
  apiRequest<void>('/api/app-errors/', {
    method: 'POST',
    body: {
      message: err.message, stack: err.stack ?? '', screen,
      app_version: Constants.expoConfig?.version ?? '', platform: `${Platform.OS} ${Platform.Version}`,
    },
  }).catch(() => {
    // Offline or the server down: nothing more to do.
  });
}

type GlobalHandler = (error: Error, isFatal?: boolean) => void;
type ErrorUtilsType = { getGlobalHandler: () => GlobalHandler; setGlobalHandler: (handler: GlobalHandler) => void };

let installed = false;

/** Every error nobody caught goes to the office too, then on as before (the red screen in development). */
export function installErrorReporting() {
  const errorUtils = (globalThis as unknown as { ErrorUtils?: ErrorUtilsType }).ErrorUtils;
  if (installed || !errorUtils) {
    return;
  }
  installed = true;
  const previous = errorUtils.getGlobalHandler();
  errorUtils.setGlobalHandler((error, isFatal) => {
    reportError(error, isFatal ? 'fatal' : 'uncaught');
    previous(error, isFatal);
  });
}
