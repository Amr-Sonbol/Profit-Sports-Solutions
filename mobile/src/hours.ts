/** Hours as h:mm — 1.53 -> "1:32", 2.5 -> "2:30" — how hours read everywhere. */
export function hm(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') {
    return '';
  }
  const minutes = Math.round(Number(value) * 60);
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}`;
}
