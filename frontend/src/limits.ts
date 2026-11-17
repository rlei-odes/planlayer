// How long a typed text may be: the same bounds as the API's (backend planlayer/api/limits.py,
// checked by tests/test_limits.py), so a field stops taking characters where the server would
// refuse them. Fields with a rule of their own use its number where they are.

export const LIMIT = {
  code: 100,
  name: 200,
  email: 254,
  longText: 2000,
  number: 40,
  query: 100_000,
} as const
