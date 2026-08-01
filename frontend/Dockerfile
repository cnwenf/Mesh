# syntax=docker/dockerfile:1
# Two-stage build for the real SPA front-door (H5). The bundle is built with
# empty API/WS bases so the browser talks to its own origin; nginx then
# reverse-proxies /api and /ws to the api / gateway services (see nginx.conf).
FROM node:22.22.0-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
# Same-origin deployment: the SPA must not hard-code a cross-origin backend.
ENV VITE_MESH_API_BASE_URL=""
ENV VITE_MESH_WS_BASE_URL=""
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
# When a shared dist volume is mounted at /dist (compose: the api service
# mounts it read-only as the HTML entry's frontend_dist_dir), publish the
# build into it before serving so the API can render the personalized shell.
CMD ["/bin/sh", "-c", "if [ -d /dist ]; then cp -r /usr/share/nginx/html/. /dist/; fi && exec nginx -g 'daemon off;'"]
