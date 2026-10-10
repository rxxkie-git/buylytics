# React frontend: build the Vite bundle, serve it with nginx.
#
# Two stages because the toolchain is ~400 MB and the output is ~2 MB of static
# files. Nothing from the build stage — not node, not node_modules, not the
# sources — reaches the running container.
#
# The bundle is served on port 80 and nginx proxies /api, /health and
# /product-analysis to the backend container by service name, so the JavaScript
# issues same-origin relative requests and contains no hostname at all. That is
# why VITE_API_BASE_URL stays empty: a value like http://localhost:8000 compiled
# into the bundle would make every visitor's browser call *its own* machine.

# ---------------------------------------------------------------------------
# Stage 1 — build
# ---------------------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

# `npm ci` from the lockfile, before the sources, so editing a component does not
# reinstall the dependency tree. `--omit=dev` is wrong here: vite and
# @vitejs/plugin-react are devDependencies and are exactly what does the building.
COPY package.json package-lock.json ./
RUN npm ci

# `.env*` rather than `.env`: it matches .env.example too, so the COPY still
# succeeds on a fresh clone where the untracked .env does not exist. Only
# VITE_-prefixed, publishable values belong in those files — Vite compiles them
# into the bundle, where anyone can read them.
COPY .env* ./
COPY index.html vite.config.js ./
COPY public ./public
COPY src ./src

# Build-time configuration. Empty defaults so `docker compose up --build` works
# with no setup; supply real values through compose's `build.args` to override
# whatever is in .env.
ARG VITE_CLERK_PUBLISHABLE_KEY=
ARG VITE_API_BASE_URL=
ARG VITE_CURRENCY=
ARG VITE_LOCALE=

# An ARG with an empty default still arrives in this shell as an empty environment
# variable, and Vite deliberately prioritises real environment variables over .env
# files. Left as-is, four empty ARGs would blank out the values in .env and produce
# a bundle with no Clerk key — which renders as a configuration notice and looks
# like a broken build. So: unset the ones that were not supplied, then build.
RUN set -eu; \
    for name in VITE_CLERK_PUBLISHABLE_KEY VITE_API_BASE_URL VITE_CURRENCY VITE_LOCALE; do \
      eval "value=\${$name:-}"; \
      if [ -z "$value" ]; then unset "$name"; fi; \
    done; \
    npm run build

# ---------------------------------------------------------------------------
# Stage 2 — serve
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine

# Replaces the image's default server block. See the file for why the API paths
# have to match the Vite dev proxy.
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# Checks that nginx serves the app, not that the backend is up: those are separate
# failures and conflating them would have Docker restart a working web server
# because Supabase was slow. wget is already in the alpine image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
