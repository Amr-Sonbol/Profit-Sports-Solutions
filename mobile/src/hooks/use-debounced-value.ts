import { useEffect, useState } from 'react';

/** `value`, but only once it has stopped changing for `delayMs` — so a
 * search box asks the server once per pause in typing, not per letter. */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}
