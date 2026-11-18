# Papertrail frontend

React, TypeScript, Vite, Tailwind CSS, and React PDF client for the document
signature API.

## Development

1. Start the backend at `http://127.0.0.1:8000`.
2. Run `npm ci`.
3. Run `npm run dev`.

Vite proxies `/api` requests to the local backend. Set `VITE_API_BASE_URL` to
override that API base. Production checks are `npm run build` and
`npm run lint`.

Guest invitations open at `/sign#token=...`. The client scrubs the fragment as
soon as the signing page initializes, keeps the token in memory, and sends it
only in the `X-Signing-Token` header to the public signing API.

## Vercel deployment

Set the project root directory to this `frontend/` folder, use `npm ci` and
`npm run build`, and set the output directory to `dist`. The included
`vercel.json` rewrites client-side routes to the SPA entry point. Configure
`VITE_API_BASE_URL` with the backend origin and `/api` prefix (for example
`https://your-api.example.com/api`) before building.

For Netlify, use this folder as the base directory, run `npm ci` and
`npm run build`, and publish `dist`. The `public/_redirects` rule is copied into
the build output and serves the SPA entry point for client-side routes.
