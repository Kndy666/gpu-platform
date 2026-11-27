ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginx:1.27-alpine

FROM ${NODE_IMAGE} AS builder
WORKDIR /app
COPY packages/panel/package.json packages/panel/package-lock.json ./
RUN npm ci
COPY packages/panel/ ./
RUN npm run build

FROM ${NGINX_IMAGE}
COPY docker/panel/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
