import { beforeEach, describe, expect, it, jest } from '@jest/globals';

import { ApiRequestError } from '@/api/client';
import { clearOfflineData, withCountryCopy, withOfflineCopy } from '@/offline';

// The phone's storage, in memory.
const mockFiles = new Map<string, string>();
jest.mock('expo-file-system', () => {
  class File {
    name: string;
    constructor(_dir: unknown, name: string) {
      this.name = name;
    }
    get exists() {
      return mockFiles.has(this.name);
    }
    create() {
      mockFiles.set(this.name, '');
    }
    textSync() {
      return mockFiles.get(this.name) ?? '';
    }
    writeSync(text: string) {
      mockFiles.set(this.name, text);
    }
    delete() {
      mockFiles.delete(this.name);
    }
  }
  class Directory {
    list() {
      return [...mockFiles.keys()].map((name) => new File(null, name));
    }
  }
  return { File, Directory, Paths: { document: 'documents' } };
});

let mockCountry: string | null = '1';
jest.mock('@/api/client', () => {
  class ApiRequestError extends Error {
    status: number;
    constructor(status: number) {
      super(`HTTP ${status}`);
      this.status = status;
    }
  }
  return { ApiRequestError, getActiveCountry: async () => mockCountry };
});
jest.mock('@/api/endpoints', () => ({ submitMyReport: jest.fn() }));

const noSignal = () => Promise.reject(new TypeError('Network request failed'));

describe('the last copy kept on the phone', () => {
  beforeEach(() => {
    mockFiles.clear();
    mockCountry = '1';
  });

  it('with no signal, shows the last copy and says so', async () => {
    await expect(withOfflineCopy('team', async () => ['Omar'])).resolves.toEqual({ data: ['Omar'], offline: false });
    await expect(withOfflineCopy('team', noSignal)).resolves.toEqual({ data: ['Omar'], offline: true });
  });

  it('never hides the server saying no, and has nothing to show the first time', async () => {
    await withOfflineCopy('team', async () => ['Omar']);
    await expect(withOfflineCopy('team', () => Promise.reject(new ApiRequestError(403, {})))).rejects.toBeInstanceOf(ApiRequestError);
    await expect(withOfflineCopy('never-loaded', noSignal)).rejects.toThrow('Network request failed');
  });

  it("keeps one copy per country: never the other country's", async () => {
    await withCountryCopy('dashboard', async () => 'UAE');
    mockCountry = '2';
    await expect(withCountryCopy('dashboard', noSignal)).rejects.toThrow();
    await withCountryCopy('dashboard', async () => 'Egypt');
    mockCountry = '1';
    await expect(withCountryCopy('dashboard', noSignal)).resolves.toEqual({ data: 'UAE', offline: true });
  });

  it('signing out leaves nothing of theirs on the phone', async () => {
    await withOfflineCopy('team', async () => ['Omar']);
    clearOfflineData();
    await expect(withOfflineCopy('team', noSignal)).rejects.toThrow();
  });
});
