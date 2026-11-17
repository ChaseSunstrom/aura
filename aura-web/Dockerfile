# jarvis-web: SvelteKit HUD (thin client for HA Assist), adapter-node.
FROM node:22-alpine AS build
RUN apk upgrade --no-cache
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# The house's GitLab for Devices → Get the apps and the CSP (T-0841): CI passes its server's URL,
# deploy-here the host's; unset, the console reads a placeholder and says the section is offline.
ARG VITE_GITLAB_URL=
RUN npm run build

FROM node:22-alpine AS runtime
# Alpine's security updates (OpenSSL criticals in the base image, pipeline 14).
RUN apk upgrade --no-cache
WORKDIR /app
ENV NODE_ENV=production \
    PORT=8199 \
    AURA_PIPELINE=Jarvis \
    AURA_TTS_VOICE=en_GB-alan-medium
# HA_URL and HA_TOKEN must be provided at runtime (compose env / secrets).
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund \
 && rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx /usr/local/lib/node_modules/corepack /usr/local/bin/corepack
# npm is gone from the runtime image: `node build` never needs it, and npm's bundled
# `tar` was a critical finding of its own.
COPY --from=build /app/build ./build
EXPOSE 8199
# busybox wget ships with alpine
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD wget -qO- http://localhost:8199/healthz || exit 1
# Not root. The image had no USER and the console ran as uid 0 with a bind
# mount of its own .storage (the services audit, 27 Aug 2026); `node` is the
# base image's unprivileged user, uid 1000, which is also what owns the
# mount on the host. The matrix in docs/explanation/security.md says `node`; now it is.
# The web-storage volume mounts here. Without the directory in the image Docker made
# it root-owned, so `node` could not write console-sessions.json and every restart
# signed everyone out (T-0387). Docker copies this ownership into an empty volume.
RUN mkdir -p /app/.storage && chown node:node /app/.storage
USER node
CMD ["node", "build"]
