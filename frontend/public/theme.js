// The colour theme before the first paint, so a dark page never flashes white: the choice saved
// in this browser (theme.ts), or the system's. A plain script, loaded in <head> before the app.
;(function () {
  var choice = null
  try {
    choice = localStorage.getItem('planlayer.theme')
  } catch {
    // storage unavailable (private mode): follow the system
  }
  var dark = choice === 'dark' || (choice !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
})()
