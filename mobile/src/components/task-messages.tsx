import { useCallback, useState } from 'react';
import * as Notifications from 'expo-notifications';
import { useFocusEffect } from 'expo-router';
import { ActivityIndicator, StyleSheet, TextInput, TouchableOpacity } from 'react-native';

import { ApiRequestError } from '@/api/client';
import { fetchTaskMessages, postTaskMessage, type TaskMessage } from '@/api/endpoints';
import { ThemedText } from '@/components/themed-text';
import { ThemedView } from '@/components/themed-view';
import { Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

/**
 * The task's team messages, as on the website's task pages: everyone on the
 * task (and the office) talking about the job. Mine on the end side.
 * Used by the technician's task screen and the team view alike.
 */
export function TaskMessages({ taskId }: { taskId: number }) {
  const theme = useTheme();
  const [thread, setThread] = useState<TaskMessage[] | null>(null);
  const [text, setText] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setThread(await fetchTaskMessages(taskId));
    } catch {
      setThread([]);
    }
  }, [taskId]);

  // On opening the task, on coming back to it, every 10 seconds while it's open, and the moment
  // a notification arrives: new messages show without leaving the task.
  useFocusEffect(useCallback(() => {
    load();
    const timer = setInterval(load, 10000);
    const subscription = Notifications.addNotificationReceivedListener(() => {
      load();
    });
    return () => {
      clearInterval(timer);
      subscription.remove();
    };
  }, [load]));

  const send = async () => {
    if (!text.trim()) {
      return;
    }
    setIsSending(true);
    setError('');
    try {
      setThread(await postTaskMessage(taskId, text.trim()));
      setText('');
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'The message could not be sent. Please try again.');
    } finally {
      setIsSending(false);
    }
  };

  return (
    <ThemedView style={styles.section}>
      <ThemedText type="smallBold">Team messages</ThemedText>
      {thread === null ? <ActivityIndicator /> : null}
      {thread?.length === 0 ? (
        <ThemedText type="small" themeColor="textSecondary">No messages yet.</ThemedText>
      ) : null}
      {thread?.map((message) => (
        <ThemedView
          key={message.id}
          style={[
            styles.bubble,
            message.mine
              ? [styles.mine, { backgroundColor: theme.backgroundSelected }]
              : { backgroundColor: theme.backgroundElement },
          ]}
        >
          <ThemedText type="small" themeColor="textSecondary">
            {message.mine ? 'You' : message.sent_by}{message.role && !message.mine ? ` · ${message.role}` : ''} ·{' '}
            {new Date(message.sent_at).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
          </ThemedText>
          <ThemedText>{message.message}</ThemedText>
        </ThemedView>
      ))}
      <TextInput
        style={[styles.input, { color: theme.text, borderColor: theme.backgroundSelected }]}
        value={text} onChangeText={setText} multiline placeholder="Write a message to the team"
        placeholderTextColor={theme.textSecondary}
      />
      {error ? <ThemedText themeColor="danger" type="small">{error}</ThemedText> : null}
      <TouchableOpacity
        style={[styles.button, { borderColor: theme.primary }, (!text.trim() || isSending) && styles.disabled]}
        onPress={send} disabled={!text.trim() || isSending}
      >
        <ThemedText themeColor="primary">{isSending ? 'Sending…' : 'Send'}</ThemedText>
      </TouchableOpacity>
    </ThemedView>
  );
}

const styles = StyleSheet.create({
  section: { gap: Spacing.two, backgroundColor: 'transparent' },
  bubble: { borderRadius: Spacing.two, padding: Spacing.two, gap: 2, maxWidth: '88%', alignSelf: 'flex-start' },
  mine: { alignSelf: 'flex-end' },
  input: { borderWidth: 1, borderRadius: Spacing.two, padding: Spacing.two, minHeight: 60, textAlignVertical: 'top' },
  button: { borderWidth: 1, borderRadius: Spacing.two, paddingVertical: Spacing.two, alignItems: 'center' },
  disabled: { opacity: 0.5 },
});
