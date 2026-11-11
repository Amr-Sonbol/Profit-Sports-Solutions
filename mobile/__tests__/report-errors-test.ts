import { describe, expect, it } from '@jest/globals';

import { fieldErrorsFrom } from '@/report-errors';

// The server refusing a report: each message lands next to its field, so the technician sees what to fix.
describe("a report's errors from the server", () => {
  it('one message per field', () => {
    expect(fieldErrorsFrom({ resolved: ['Please choose Yes or No.'], customer_name: ['Required.', 'Too long.'] }))
      .toEqual({ resolved: 'Please choose Yes or No.', customer_name: 'Required.' });
  });

  it('a row of parts, machines or the checklist: the first problem found', () => {
    expect(fieldErrorsFrom({ parts: [{}, { part_code: ['Unknown part code.'] }] })).toEqual({ parts: 'Unknown part code.' });
    expect(fieldErrorsFrom({ machines: [{ serial: ['Enter the serial.'] }] })).toEqual({ machines: 'Enter the serial.' });
    expect(fieldErrorsFrom({ checklist: [{}, {}] })).toEqual({});
  });

  it('anything else: nothing to show by a field', () => {
    expect(fieldErrorsFrom(null)).toEqual({});
    expect(fieldErrorsFrom('Server error')).toEqual({});
  });
});
