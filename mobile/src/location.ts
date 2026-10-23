/**
 * Where the phone is at a technician's tap — sent along with it and
 * checked against the site on the server (tasks/location.py). Taken only
 * at the tap itself, never tracked in between. Never blocks the tap: no
 * permission, no fix in time, or any error just sends no location, and
 * the server records "No location sent".
 */
import * as Location from 'expo-location';

export interface TapLocation {
  latitude: number;
  longitude: number;
  accuracy: number | null;
}

const TIMEOUT_MS = 10000;
// Only when no fresh fix comes in time (indoors, a basement gym): the
// phone's last known position, if it's this recent.
const FALLBACK_MAX_AGE_MS = 5 * 60000;

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T | null> {
  return Promise.race([promise, new Promise<null>((resolve) => setTimeout(() => resolve(null), ms))]);
}

export async function currentLocation(): Promise<TapLocation | null> {
  try {
    const permission = await Location.requestForegroundPermissionsAsync();
    if (!permission.granted) {
      return null;
    }
    const position = (await withTimeout(
      Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High }), TIMEOUT_MS,
    )) ?? (await Location.getLastKnownPositionAsync({ maxAge: FALLBACK_MAX_AGE_MS }));
    if (!position) {
      return null;
    }
    return {
      latitude: position.coords.latitude,
      longitude: position.coords.longitude,
      accuracy: position.coords.accuracy === null ? null : Math.round(position.coords.accuracy),
    };
  } catch {
    return null;
  }
}
