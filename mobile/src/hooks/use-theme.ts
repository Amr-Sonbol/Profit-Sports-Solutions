/**
 * Learn more about light and dark modes:
 * https://docs.expo.dev/guides/color-schemes/
 */

import { Colors } from '@/constants/theme';
import { useColorScheme } from '@/hooks/use-color-scheme';

export function useTheme() {
  const scheme = useColorScheme();
  // null when the phone doesn't say (React Native 0.88; it used to be 'unspecified').
  const theme = scheme ?? 'light';

  return Colors[theme];
}
