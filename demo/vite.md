# Vite: Getting Started

Local demo copy, adapted from the Vite guide
(vitejs/vite, docs/guide/index.md, commit 7e221fc61e3f4c57371ce7dc24c51e887ae33157).
The guide offers one command per package manager; this copy keeps the npm form, drops the
`$` prompts and passes the template on the command line so no step is interactive.

## Overview

Vite is a build tool that aims to provide a faster and leaner development experience for
modern web projects. It consists of a dev server with hot module replacement and a build
command that bundles your code for production.

Vite requires Node.js version 20.19+ or 22.12+.

## Scaffolding Your First Vite Project

You can directly specify the project name and the template you want to use via command
line options. To scaffold a Vite + React project without interactive prompts, run:

```bash
npm create vite@latest my-react-app -- --template react --no-interactive
```

Then install the dependencies and start the dev server:

```bash
cd my-react-app && npm install
```

```bash
cd my-react-app && npm run dev
```

The dev server prints a local address such as `Local: http://localhost:5173/`.

Open http://localhost:5173/ in your browser. The starter page shows the heading "Vite + React"
and a button labelled "count is 0".

Click the "count is 0" button once. Its label changes to "count is 1".

## Next steps

Edit `src/App.jsx` and save: the page updates without a reload thanks to hot module replacement.
See the Features Guide for what else is possible.
