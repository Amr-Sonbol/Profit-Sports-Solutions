import { describe, expect, it, jest } from '@jest/globals';
import { Alert } from 'react-native';

import { confirmStep, STEP_LABELS, STEP_QUESTIONS } from '@/confirm';

type Button = { text?: string; onPress?: () => void };

/** Answers the next question as if the person tapped `label`. */
function answer(label: string) {
  jest.spyOn(Alert, 'alert').mockImplementationOnce((_title, _message, buttons) => {
    (buttons as Button[]).find((button) => button.text === label)?.onPress?.();
  });
}

// A mistaken tap on "I have arrived" records the wrong time: every step asks first.
describe("a technician's steps", () => {
  it('every step button has its question', () => {
    for (const step of Object.keys(STEP_LABELS)) {
      expect(STEP_QUESTIONS[step]).toBeDefined();
    }
    // The server's steps (tasks.views.TECHNICIAN_ACTIONS), all with a button.
    expect(Object.keys(STEP_LABELS).sort()).toEqual(['accept', 'arrive', 'en_route', 'start']);
  });

  it('goes ahead only on Confirm', async () => {
    answer('Cancel');
    await expect(confirmStep('arrive')).resolves.toBe(false);
    answer('Confirm');
    await expect(confirmStep('arrive')).resolves.toBe(true);
    expect(Alert.alert).toHaveBeenLastCalledWith(
      STEP_QUESTIONS.arrive[0], STEP_QUESTIONS.arrive[1], expect.any(Array), expect.any(Object),
    );
  });
});
