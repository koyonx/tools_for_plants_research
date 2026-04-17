# syntax=docker/dockerfile:1.7
FROM node:20-alpine AS base
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

# ---- deps ---------------------------------------------------------------
FROM base AS deps
COPY package.json ./
RUN npm install --no-audit --no-fund

# ---- dev (used by docker compose) ---------------------------------------
FROM deps AS dev
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

# ---- build (for prod image) ---------------------------------------------
FROM deps AS build
COPY . .
RUN npm run build

# ---- runtime (prod) -----------------------------------------------------
FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
