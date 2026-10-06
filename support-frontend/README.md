# Support Operator Interface

Frontend for the Matrix support operator built with **React + Vite**.

## Prerequisites

- Node.js (>=18) and npm
- The backend service exposing the following REST endpoints (example URLs):
  - `GET /api/tickets` – list tickets
  - `POST /api/tickets` – create ticket `{title, description}`
  - `GET /api/tickets/:id/messages` – list messages of a ticket
  - `POST /api/tickets/:id/messages` – add a message `{body}`
  - `PATCH /api/tickets/:id` – update ticket fields, e.g., `{status}`

## Setup

```bash
cd support-frontend
npm install
```

Create a `.env` file (or copy `.env.example`) and set the backend URL if it differs from the default `http://localhost:3000`:

```bash
cp .env.example .env
# edit .env as needed
```

## Development

```bash
npm run dev
```

The app will be available at **http://localhost:5173**. Vite proxies `/api/*` requests to the backend URL defined in `VITE_BACKEND_URL`.

## Build

```bash
npm run build
# preview the build
npm run preview
```

## Example Element Web configuration

```env
ELEMENT_HOMESERVER_URL=https://matrix.example.com
ELEMENT_USER_ID=@operator:example.com
ELEMENT_ACCESS_TOKEN=YOUR_ACCESS_TOKEN
```

These variables are provided as a reference for integrating the operator UI with Element Web; the UI itself does **not** directly use them but they are useful for developers.
