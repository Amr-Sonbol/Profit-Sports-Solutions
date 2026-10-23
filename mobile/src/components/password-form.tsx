import { useState } from 'react';
import { ActivityIndicator, StyleSheet, Switch, TextInput, TouchableOpacity, View } from 'react-native';

import { ApiRequestError } from '@/api/client';
import { setMyPassword } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { Spacing } from '@/constants/theme';
import { useSession } from '@/context/auth';
import { useTheme } from '@/hooks/use-theme';

// Same wording as the website's password rules (tasks/forms.py PASSWORD_RULES).
const PASSWORD_RULES = 'At least 8 characters, not entirely numeric, and not a common password or one similar to your name.';

type FieldErrors = Partial<Record<'old_password' | 'new_password1' | 'new_password2' | 'form', string>>;

function errorsFrom(err: unknown): FieldErrors {
  if (!(err instanceof ApiRequestError)) {
    return { form: 'Unable to save. Please check your connection and try again.' };
  }
  const body = err.body;
  if (!body || typeof body !== 'object' || 'detail' in body) {
    return { form: err.message };
  }
  const errors: FieldErrors = {};
  for (const [field, messages] of Object.entries(body as Record<string, string[]>)) {
    const key = field === '__all__' ? 'form' : field;
    errors[key as keyof FieldErrors] = messages.join(' ');
  }
  return errors;
}

/**
 * New password, twice, plus the current one when `askCurrent` — the
 * "choose your password" screen and Profile > Change password share it.
 */
export function PasswordForm({ askCurrent, submitLabel, onSaved }: {
  askCurrent: boolean;
  submitLabel: string;
  onSaved?: () => void;
}) {
  const { passwordSet } = useSession();
  const theme = useTheme();
  const [current, setCurrent] = useState('');
  const [password1, setPassword1] = useState('');
  const [password2, setPassword2] = useState('');
  const [showPasswords, setShowPasswords] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async () => {
    setErrors({});
    setIsSubmitting(true);
    try {
      const response = await setMyPassword({
        ...(askCurrent ? { old_password: current } : {}),
        new_password1: password1,
        new_password2: password2,
      });
      await passwordSet(response);
      onSaved?.();
    } catch (err) {
      setErrors(errorsFrom(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  const inputStyle = [styles.input, { color: theme.text, borderColor: theme.backgroundSelected }];
  const field = (label: string, value: string, onChange: (text: string) => void, error?: string, help?: string) => (
    <View style={styles.field}>
      <ThemedText type="smallBold">{label}</ThemedText>
      <TextInput
        style={inputStyle}
        secureTextEntry={!showPasswords}
        autoCapitalize="none"
        autoCorrect={false}
        value={value}
        onChangeText={onChange}
      />
      {help ? <ThemedText type="small" themeColor="textSecondary">{help}</ThemedText> : null}
      {error ? <ThemedText type="small" themeColor="danger">{error}</ThemedText> : null}
    </View>
  );

  return (
    <View style={styles.form}>
      {askCurrent ? field('Current password', current, setCurrent, errors.old_password) : null}
      {field('New password', password1, setPassword1, errors.new_password1, PASSWORD_RULES)}
      {field('Confirm new password', password2, setPassword2, errors.new_password2)}

      <View style={styles.toggle}>
        <Switch value={showPasswords} onValueChange={setShowPasswords} />
        <ThemedText>Show passwords</ThemedText>
      </View>

      {errors.form ? <ThemedText themeColor="danger">{errors.form}</ThemedText> : null}

      <TouchableOpacity
        style={[styles.button, { backgroundColor: theme.primary }, isSubmitting && styles.buttonDisabled]}
        onPress={handleSubmit}
        disabled={isSubmitting || !password1 || !password2 || (askCurrent && !current)}
      >
        {isSubmitting ? (
          <ActivityIndicator color={theme.onPrimary} />
        ) : (
          <ThemedText style={[styles.buttonText, { color: theme.onPrimary }]}>{submitLabel}</ThemedText>
        )}
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  form: { gap: Spacing.three },
  field: { gap: Spacing.one },
  input: {
    borderWidth: 1,
    borderRadius: Spacing.two,
    paddingHorizontal: Spacing.three,
    paddingVertical: Spacing.three,
    fontSize: 16,
  },
  toggle: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  button: { borderRadius: Spacing.two, paddingVertical: Spacing.three, alignItems: 'center' },
  buttonDisabled: { opacity: 0.6 },
  buttonText: { fontSize: 16, fontWeight: '600' },
});
