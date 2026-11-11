import { Children, type ReactNode } from 'react';
import { Platform, StyleSheet, Text, type TextProps } from 'react-native';

import { Fonts, ThemeColor } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { isRTL, t } from '@/i18n';

/** Each piece of text in the app's language (i18n.t); numbers, names and everything else as they are. */
function translated(children: ReactNode): ReactNode {
  if (typeof children === 'string') {
    return t(children);
  }
  return Array.isArray(children)
    ? Children.map(children, (child) => (typeof child === 'string' ? t(child) : child))
    : children;
}

export type ThemedTextProps = TextProps & {
  type?: 'default' | 'title' | 'small' | 'smallBold' | 'subtitle' | 'link' | 'linkPrimary' | 'code';
  themeColor?: ThemeColor;
};

export function ThemedText({ style, type = 'default', themeColor, children, ...rest }: ThemedTextProps) {
  const theme = useTheme();

  return (
    <Text
      style={[
        // Arabic reads from the right; a screen's own textAlign (centred, say) still wins.
        isRTL() && styles.rtl,
        { color: theme[themeColor ?? 'text'] },
        type === 'default' && styles.default,
        type === 'title' && styles.title,
        type === 'small' && styles.small,
        type === 'smallBold' && styles.smallBold,
        type === 'subtitle' && styles.subtitle,
        type === 'link' && styles.link,
        type === 'linkPrimary' && styles.linkPrimary,
        type === 'code' && styles.code,
        style,
      ]}
      {...rest}
    >
      {translated(children)}
    </Text>
  );
}

const styles = StyleSheet.create({
  rtl: { textAlign: 'right', writingDirection: 'rtl' },
  small: {
    fontSize: 14,
    lineHeight: 20,
    fontWeight: 500,
  },
  smallBold: {
    fontSize: 14,
    lineHeight: 20,
    fontWeight: 700,
  },
  default: {
    fontSize: 16,
    lineHeight: 24,
    fontWeight: 500,
  },
  title: {
    fontSize: 48,
    fontWeight: 600,
    lineHeight: 52,
  },
  subtitle: {
    fontSize: 32,
    lineHeight: 44,
    fontWeight: 600,
  },
  link: {
    lineHeight: 30,
    fontSize: 14,
  },
  linkPrimary: {
    lineHeight: 30,
    fontSize: 14,
    color: '#3c87f7',
  },
  code: {
    fontFamily: Fonts.mono,
    fontWeight: Platform.select({ android: 700 }) ?? 500,
    fontSize: 12,
  },
});
