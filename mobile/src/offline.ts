import { File, Paths } from 'expo-file-system';

import { ApiRequestError } from '@/api/client';
import { submitMyReport } from '@/api/endpoints';
import type { WorkReportInput } from '@/types';

/**
 * Offline support for filing a work report with no signal (a basement gym,
 * a remote site): the last-loaded copy of each screen's data is kept on
 * the phone, and a report that couldn't be sent waits here until it can.
 * Plain JSON files in the app's document directory — they survive an app
 * restart, and a signature (a PNG data URL) is too big for secure-store.
 */

export interface PendingReport {
  taskId: number;
  taskNumber: string;
  input: WorkReportInput;
  savedAt: string;
  // Set when the server refused it (e.g. a part code no longer in the
  // list) — it stays queued until the technician opens it and fixes it.
  error: string | null;
}

const PENDING_FILE = 'pending-reports.json';

function readJson<T>(name: string, fallback: T): T {
  try {
    const file = new File(Paths.document, name);
    return file.exists ? (JSON.parse(file.textSync()) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(name: string, value: unknown) {
  try {
    const file = new File(Paths.document, name);
    if (!file.exists) {
      file.create();
    }
    file.write(JSON.stringify(value));
  } catch {
    // Storage full or unavailable — offline support just doesn't kick in.
  }
}

/** fetch itself throwing (not an HTTP error response) means no connection. */
export function isNetworkError(err: unknown): boolean {
  return !(err instanceof ApiRequestError);
}

// ---- last-loaded copies of screen data ----

export function cacheScreenData(key: string, value: unknown) {
  writeJson(`cache-${key}.json`, value);
}

export function cachedScreenData<T>(key: string): T | null {
  return readJson<T | null>(`cache-${key}.json`, null);
}

/**
 * Runs `load`, keeping a copy of what it returns; with no connection,
 * hands back the last copy instead (and `offline: true`). Any other error
 * — the server saying no — is rethrown as usual.
 */
export async function withOfflineCopy<T>(key: string, load: () => Promise<T>): Promise<{ data: T; offline: boolean }> {
  try {
    const data = await load();
    cacheScreenData(key, data);
    return { data, offline: false };
  } catch (err) {
    const cached = isNetworkError(err) ? cachedScreenData<T>(key) : null;
    if (cached === null) {
      throw err;
    }
    return { data: cached, offline: true };
  }
}

// ---- reports waiting to be sent ----

export function pendingReports(): PendingReport[] {
  return readJson<PendingReport[]>(PENDING_FILE, []);
}

export function pendingReportFor(taskId: number): PendingReport | undefined {
  return pendingReports().find((report) => report.taskId === taskId);
}

export function savePendingReport(taskId: number, taskNumber: string, input: WorkReportInput) {
  const others = pendingReports().filter((report) => report.taskId !== taskId);
  writeJson(PENDING_FILE, [...others, { taskId, taskNumber, input, savedAt: new Date().toISOString(), error: null }]);
}

export function removePendingReport(taskId: number) {
  writeJson(PENDING_FILE, pendingReports().filter((report) => report.taskId !== taskId));
}

/**
 * Tries to send every waiting report. Sent ones are removed; one the
 * server refuses keeps its error for the technician to fix; no connection
 * stops the attempt until next time. Returns what's still waiting.
 */
export async function sendPendingReports(): Promise<PendingReport[]> {
  for (const report of pendingReports()) {
    try {
      await submitMyReport(report.taskId, report.input);
      removePendingReport(report.taskId);
    } catch (err) {
      if (isNetworkError(err)) {
        break;
      }
      const refused = pendingReports().map((pending) => (
        pending.taskId === report.taskId
          ? { ...pending, error: err instanceof ApiRequestError ? err.message : 'Could not be sent.' }
          : pending
      ));
      writeJson(PENDING_FILE, refused);
    }
  }
  return pendingReports();
}
