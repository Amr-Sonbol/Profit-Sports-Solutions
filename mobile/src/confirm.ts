import { Alert } from 'react-native';

/**
 * "Are you sure?" before a step that changes a task — a mistaken tap on
 * "I have arrived" or "Start work" would record the wrong time. Resolves
 * true only on the confirm button; Cancel, or tapping outside, is false.
 */
export function confirmAction(title: string, message: string, confirmLabel = 'Confirm'): Promise<boolean> {
  return new Promise((resolve) => {
    Alert.alert(
      title,
      message,
      [
        { text: 'Cancel', style: 'cancel', onPress: () => resolve(false) },
        { text: confirmLabel, onPress: () => resolve(true) },
      ],
      { cancelable: true, onDismiss: () => resolve(false) },
    );
  });
}

/** The button for each of a technician's steps (tasks.views.TECHNICIAN_ACTIONS) — each asks first (STEP_QUESTIONS). */
export const STEP_LABELS: Record<string, string> = {
  accept: 'Confirm task',
  en_route: 'Heading to site',
  arrive: 'I have arrived',
  start: 'Start work',
};

/** The question for each of a task's steps (tasks.views.TECHNICIAN_ACTIONS, and undo). */
export const STEP_QUESTIONS: Record<string, [string, string]> = {
  accept: ['Confirm this task?', 'You confirm you will do this visit. The office sees it straight away.'],
  en_route: ['On your way?', 'This records that you are heading to the site, with the time.'],
  arrive: ['Arrived at the site?', 'This records your arrival time and where you are.'],
  start: ['Start work?', 'This records the time you start working.'],
  undo: ['Undo the last step?', 'The last step you recorded will be removed, as if it never happened.'],
};

/** Asks the step's question; true when confirmed (a step without one goes ahead). */
export function confirmStep(action: string): Promise<boolean> {
  const question = STEP_QUESTIONS[action];
  return question ? confirmAction(question[0], question[1]) : Promise.resolve(true);
}
