# syntax=docker/dockerfile:1
# Two-stage build for the real SPA front-door (H5). The bundle is built with
# empty API/WS bases so the browser talks to its own origin; nginx then
# reverse-proxies /api and /ws to the api / gateway services (see nginx.conf).
FROM node:20-alpine AS build
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
CMD ["nginx", "-g", "daemon off;"]
