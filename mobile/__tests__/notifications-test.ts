import { describe, expect, it, jest } from '@jest/globals';

import { routeForNotification } from '@/push';

// The phone's notification service doesn't run in a test; only the routing is tested here.
jest.mock('expo-notifications', () => ({ setNotificationHandler: jest.fn() }));

// Every notification opens its own screen when tapped (people/push.py, tasks/updates.py).
describe('tapping a notification', () => {
  it('opens the task: the technician his own screen, the office the planning one', () => {
    expect(routeForNotification({ type: 'my_task', task_id: 7 }, 'technician')).toBe('/task/7');
    expect(routeForNotification({ type: 'team_task', task_id: 7 }, 'supervisor')).toBe('/team-task/7');
    expect(routeForNotification({ type: 'task_update', task_id: 7 }, 'technician')).toBe('/task/7');
    expect(routeForNotification({ type: 'task_update', task_id: 7 }, 'supervisor')).toBe('/team-task/7');
    expect(routeForNotification({ type: 'task_message', task_id: 7 }, 'manager')).toBe('/team-task/7');
  });

  it('opens a request in the app, an internal task on its page', () => {
    expect(routeForNotification({ type: 'ticket', ticket_id: 3 }, 'support_manager')).toBe('/ticket/3');
    expect(routeForNotification({ type: 'ticket' }, 'support_manager')).toBe('/tickets');
    expect(routeForNotification({ type: 'internal_ticket', ticket_id: 4 }, 'manager'))
      .toBe('/internal-task/4');
  });

  it('sends the field team to their list when a task is no longer theirs', () => {
    expect(routeForNotification({ type: 'task_removed', task_id: 7 }, 'technician')).toBe('/tasks');
    expect(routeForNotification({ type: 'task_cancelled', task_id: 7 }, 'technician')).toBe('/tasks');
    expect(routeForNotification({ type: 'task_cancelled', task_id: 7 }, 'manager')).toBe('/team-task/7');
  });

  it('never does nothing: the test opens Profile, anything unknown the bell', () => {
    expect(routeForNotification({ type: 'test' }, 'technician')).toBe('/profile');
    expect(routeForNotification({ type: 'something_new' }, 'technician')).toBe('/bell');
    expect(routeForNotification({}, null)).toBe('/bell');
  });
});
