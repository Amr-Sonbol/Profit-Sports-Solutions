import { StyleSheet, TextInput, TouchableOpacity, View } from 'react-native';

import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

/** The search field above a list — with a clear button once there's text. */
export function SearchBox({
  value, onChangeText, placeholder,
}: { value: string; onChangeText: (text: string) => void; placeholder: string }) {
  const theme = useTheme();
  return (
    <View style={[styles.box, { borderColor: theme.backgroundSelected, backgroundColor: theme.backgroundElement }]}>
      <TextInput
        style={[styles.input, { color: theme.text }]}
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={theme.textSecondary}
        autoCorrect={false}
        autoCapitalize="none"
        clearButtonMode="never"
        returnKeyType="search"
        accessibilityLabel={placeholder}
      />
      {value ? (
        <TouchableOpacity onPress={() => onChangeText('')} style={styles.clear} accessibilityLabel="Clear search">
          <ThemedText themeColor="textSecondary">✕</ThemedText>
        </TouchableOpacity>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  box: {
    flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderRadius: Spacing.two,
    marginHorizontal: Spacing.three, marginBottom: Spacing.two,
  },
  input: { flex: 1, fontSize: 16, paddingHorizontal: Spacing.three, paddingVertical: Spacing.two, minHeight: 44 },
  clear: { paddingHorizontal: Spacing.three, minHeight: 44, justifyContent: 'center' },
});
