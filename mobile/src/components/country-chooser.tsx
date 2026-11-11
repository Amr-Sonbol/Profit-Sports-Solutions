import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { StyleSheet } from 'react-native';

import { getActiveCountry, setActiveCountry } from '@/api/client';
import { ChoicePicker } from '@/components/choice-picker';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useSession } from '@/context/auth';

/**
 * Which country the app shows — a manager's or the admin's, like the
 * website's header switcher (their own country never changes). Nothing for
 * anyone else. `onChange` reloads the screen it's on; every other screen
 * picks it up the next time it opens.
 */
export function CountryChooser({ onChange, inset = true }: { onChange: () => void; inset?: boolean }) {
  const { me } = useSession();
  const [chosen, setChosen] = useState('');

  useFocusEffect(useCallback(() => {
    getActiveCountry().then((id) => setChosen(id ?? me?.home_country_id ?? ''));
  }, [me?.home_country_id]));

  if (!me?.countries?.length) {
    return null;
  }

  const choose = async (id: string) => {
    setChosen(id);
    await setActiveCountry(id === me.home_country_id ? null : id);
    onChange();
  };

  return (
    <ThemedView style={[styles.wrap, inset && styles.inset]}>
      <ChoicePicker label="Country" choices={me.countries} value={chosen} onChange={choose} />
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  wrap: { paddingBottom: Spacing.two, backgroundColor: 'transparent' },
  inset: { paddingHorizontal: Spacing.three },
});
