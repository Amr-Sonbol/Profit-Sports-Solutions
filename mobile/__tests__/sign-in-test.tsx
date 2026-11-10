import { describe, expect, it, jest } from '@jest/globals';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';

import SignInScreen from '@/app/sign-in';

const mockSignIn = jest.fn<(username: string, password: string) => Promise<void>>();
jest.mock('@/context/auth', () => ({ useSession: () => ({ signIn: mockSignIn }) }));
jest.mock('@/api/client', () => {
  class ApiRequestError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }
  return { ApiRequestError };
});

async function fill(username: string, password: string) {
  await render(<SignInScreen />);
  await fireEvent.changeText(screen.getByPlaceholderText('Username'), username);
  await fireEvent.changeText(screen.getByPlaceholderText('Password'), password);
}

describe('signing in', () => {
  it('sends the username without stray spaces', async () => {
    mockSignIn.mockResolvedValueOnce();
    await fill('  phone_test_tech ', 'secret');
    await fireEvent.press(screen.getByText('Sign in'));
    await waitFor(() => expect(mockSignIn).toHaveBeenCalledWith('phone_test_tech', 'secret'));
  });

  it("shows the server's message when it says no, and a plain one with no connection", async () => {
    const { ApiRequestError } = jest.requireMock<{ ApiRequestError: new (s: number, m: string) => Error }>('@/api/client');
    mockSignIn.mockRejectedValueOnce(new ApiRequestError(400, 'Unable to log in with provided credentials.'));
    await fill('someone', 'wrong');
    await fireEvent.press(screen.getByText('Sign in'));
    expect(await screen.findByText('Unable to log in with provided credentials.')).toBeTruthy();

    mockSignIn.mockRejectedValueOnce(new TypeError('Network request failed'));
    await fireEvent.press(screen.getByText('Sign in'));
    expect(await screen.findByText('Unable to sign in. Please check your connection.')).toBeTruthy();
  });
});
