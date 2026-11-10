/** The report form's errors: one message per field. */
export type FieldErrors = Record<string, string>;

/** DRF's {"field": ["message"]} errors, flattened to one message per field. */
export function fieldErrorsFrom(body: unknown): FieldErrors {
  const errors: FieldErrors = {};
  if (!body || typeof body !== 'object') {
    return errors;
  }
  for (const [field, value] of Object.entries(body as Record<string, unknown>)) {
    if ((field === 'parts' || field === 'machines' || field === 'checklist') && Array.isArray(value)) {
      const first = value.find((rowErrors) => rowErrors && Object.keys(rowErrors).length > 0);
      if (first) {
        const messages = Object.values(first as Record<string, string[]>).flat();
        errors[field] = messages[0] ?? `Please review the ${field} entries.`;
      }
    } else if (Array.isArray(value)) {
      errors[field] = String(value[0]);
    }
  }
  return errors;
}
