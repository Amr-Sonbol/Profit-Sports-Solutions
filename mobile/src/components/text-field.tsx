import { type ComponentProps } from 'react';
import { TextInput as NativeTextInput } from 'react-native';

import { isRTL, t } from '@/i18n';

/**
 * React Native's TextInput in the app's language: its placeholder translated,
 * and typed from the right in Arabic. Every screen's text boxes use it (a ref
 * passes straight through — an ordinary prop since React 19).
 */
export function TextInput({ placeholder, style, ...rest }: ComponentProps<typeof NativeTextInput>) {
  return (
    <NativeTextInput
      placeholder={placeholder === undefined ? undefined : t(placeholder)}
      style={[isRTL() && { textAlign: 'right', writingDirection: 'rtl' }, style]}
      {...rest}
    />
  );
}
