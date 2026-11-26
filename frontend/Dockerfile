# Credify web: build the SPA with Node, serve the static files with nginx.
# nginx also proxies /api and /health to the API, so the browser talks to one origin and the
# SameSite=Strict refresh cookie needs no CORS.

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --retries=10 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
