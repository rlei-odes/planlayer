// How long a typed text may be: the same bounds as the API's (backend planlayer/api/limits.py,
// checked by tests/test_limits.py), so a field stops taking characters where the server would
// refuse them. Fields with a rule of their own use its number where they are.

export const LIMIT = {
  code: 100,
  name: 200,
  email: 254,
  longText: 2000,
  number: 40,
  password: 200,
  pem: 20_000,
  query: 100_000,
} as const

// How long a text typed in the app should be: tighter than LIMIT, so names fit the grids and
// lists they are shown in. Texts from the source or an upload may be longer; the API takes them.
export const FIELD = {
  code: 30, // a code typed in: a member, a forwarding, an account
  title: 60, // a name or title: a position, an investment, an item, a rule, a round
  person: 80, // a person's name
  unit: 20, // a unit of measure
  comment: 300, // one line: a reason, a comment
  text: 1000, // several lines: a description, a note
  search: 60, // a filter or search box
} as const
