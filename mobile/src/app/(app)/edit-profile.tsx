import Ionicons from '@expo/vector-icons/Ionicons';
import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import {
  ActivityIndicator, Alert, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, TextInput, TouchableOpacity,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiRequestError } from '@/api/client';
import { updateMe } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useSession } from '@/context/auth';
import { useTheme } from '@/hooks/use-theme';

type Photo = { uri: string; name: string; type: string };

/**
 * Profile > Edit profile — the website's My profile: photo (camera or
 * gallery), phone, email and language. Role, country and employment are
 * set by the office.
 */
export default function EditProfileScreen() {
  const router = useRouter();
  const theme = useTheme();
  const { me, profileSaved } = useSession();
  const [phone, setPhone] = useState(me?.phone ?? '');
  const [email, setEmail] = useState(me?.email ?? '');
  const [language, setLanguage] = useState(me?.language ?? 'en');
  const [photo, setPhoto] = useState<Photo | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isBusy, setIsBusy] = useState(false);

  const pickPhoto = async (fromCamera: boolean) => {
    const permission = fromCamera
      ? await ImagePicker.requestCameraPermissionsAsync()
      : await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      Alert.alert('Permission required', fromCamera ? 'Camera access is needed.' : 'Photo library access is needed.');
      return;
    }
    const options: ImagePicker.ImagePickerOptions = {
      mediaTypes: ['images'], quality: 0.7, allowsEditing: true, aspect: [1, 1],
    };
    const result = fromCamera
      ? await ImagePicker.launchCameraAsync(options)
      : await ImagePicker.launchImageLibraryAsync(options);
    const asset = result.canceled ? null : result.assets[0];
    if (asset) {
      setPhoto({ uri: asset.uri, name: asset.fileName ?? 'photo.jpg', type: asset.mimeType ?? 'image/jpeg' });
    }
  };

  const save = async () => {
    setIsBusy(true);
    setErrors({});
    try {
      profileSaved(await updateMe({ phone: phone.trim(), email: email.trim(), language }, photo ?? undefined));
      router.navigate('/profile');
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 400 && err.body && typeof err.body === 'object') {
        const body = err.body as Record<string, string[]>;
        setErrors(Object.fromEntries(Object.entries(body).map(([field, messages]) => [field, messages.join(' ')])));
      } else {
        setErrors({ form: err instanceof ApiRequestError ? err.message : 'Not saved. Please try again.' });
      }
    } finally {
      setIsBusy(false);
    }
  };

  const inputStyle = [styles.input, { color: theme.text, borderColor: theme.backgroundSelected }];
  const shownPhoto = photo?.uri ?? me?.photo_url;

  return (
    <ThemedView style={styles.container}>
      <SafeAreaView style={styles.safeArea} edges={['top']}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            <TouchableOpacity onPress={() => router.navigate('/profile')}>
              <ThemedText themeColor="textSecondary">‹ Profile</ThemedText>
            </TouchableOpacity>
            <ThemedText type="title">Edit profile</ThemedText>

            <ThemedView style={styles.photoBlock}>
              {shownPhoto ? (
                <Image source={{ uri: shownPhoto }} style={styles.photo} contentFit="cover" />
              ) : (
                <ThemedView style={[styles.photo, styles.noPhoto, { backgroundColor: theme.backgroundElement }]}>
                  <Ionicons name="person" size={44} color={theme.textSecondary} />
                </ThemedView>
              )}
              <ThemedView style={styles.row}>
                <TouchableOpacity style={[styles.smallButton, { borderColor: theme.primary }]} onPress={() => pickPhoto(true)}>
                  <ThemedText type="small" themeColor="primary">Take photo</ThemedText>
                </TouchableOpacity>
                <TouchableOpacity style={[styles.smallButton, { borderColor: theme.primary }]} onPress={() => pickPhoto(false)}>
                  <ThemedText type="small" themeColor="primary">Choose from gallery</ThemedText>
                </TouchableOpacity>
              </ThemedView>
              <ThemedText type="small" themeColor="textSecondary">
                Shown to your team on schedules and tasks. Up to 5 MB.
              </ThemedText>
              {errors.photo ? <ThemedText type="small" themeColor="danger">{errors.photo}</ThemedText> : null}
            </ThemedView>

            <ThemedText type="smallBold">Phone</ThemedText>
            <TextInput style={inputStyle} value={phone} onChangeText={setPhone} keyboardType="phone-pad" />
            {errors.phone ? <ThemedText type="small" themeColor="danger">{errors.phone}</ThemedText> : null}

            <ThemedText type="smallBold">Email</ThemedText>
            <TextInput
              style={inputStyle} value={email} onChangeText={setEmail}
              keyboardType="email-address" autoCapitalize="none" autoCorrect={false}
            />
            {errors.email ? <ThemedText type="small" themeColor="danger">{errors.email}</ThemedText> : null}

            <ThemedText type="smallBold">Language</ThemedText>
            <ThemedText type="small" themeColor="textSecondary">
              For your notifications and the website.
            </ThemedText>
            <ThemedView style={styles.row}>
              {(me?.language_choices ?? [{ value: 'en', label: 'English' }, { value: 'ar', label: 'Arabic' }]).map((choice) => {
                const on = language === choice.value;
                return (
                  <TouchableOpacity
                    key={choice.value}
                    style={[styles.chip, { borderColor: on ? theme.primary : theme.backgroundSelected },
                      on && { backgroundColor: theme.primary }]}
                    onPress={() => setLanguage(choice.value)}
                  >
                    <ThemedText style={on ? { color: theme.onPrimary } : undefined}>{choice.label}</ThemedText>
                  </TouchableOpacity>
                );
              })}
            </ThemedView>

            {errors.form ? <ThemedText themeColor="danger">{errors.form}</ThemedText> : null}
            <TouchableOpacity
              style={[styles.saveButton, { backgroundColor: theme.primary }]} onPress={save} disabled={isBusy}
            >
              {isBusy ? <ActivityIndicator color={theme.onPrimary} /> : (
                <ThemedText type="smallBold" style={{ color: theme.onPrimary }}>Save</ThemedText>
              )}
            </TouchableOpacity>
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  safeArea: { flex: 1 },
  flex: { flex: 1 },
  content: { paddingHorizontal: Spacing.three, paddingBottom: Spacing.six, gap: Spacing.two },
  photoBlock: { alignItems: 'center', gap: Spacing.two, backgroundColor: 'transparent', marginBottom: Spacing.two },
  photo: { width: 112, height: 112, borderRadius: 56 },
  noPhoto: { alignItems: 'center', justifyContent: 'center' },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.two, backgroundColor: 'transparent' },
  smallButton: { borderWidth: 1, borderRadius: Spacing.two, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.three, fontSize: 16 },
  chip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: Spacing.three, paddingVertical: Spacing.one },
  saveButton: { borderRadius: Spacing.two, padding: Spacing.three, alignItems: 'center', marginTop: Spacing.three },
});
