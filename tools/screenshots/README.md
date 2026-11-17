# Screenshots

Screenshots of the main pages of a running development instance, taken with Playwright. They
serve layout reviews and the website.

```
cd tools/screenshots && npm ci && npm run install-browser
npm run shots                      # against http://localhost:5173
node shots.mjs http://localhost:8000
```

The script logs in through the development login (`PLANLAYER_DEV_LOGIN`), so it needs the dev
users (`planlayer seed-dev-users`) and a round with data. Each page is captured at 1440×900, the
viewport only, into `out/` (not tracked). Pages are listed per user in `shots.mjs`.
