FROM node:24-alpine AS build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web ./
RUN npm run build

FROM node:24-alpine
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /web
RUN adduser -D -u 10001 app
COPY --from=build /web/.next/standalone ./
COPY --from=build /web/.next/static ./.next/static
USER app
EXPOSE 3000
CMD ["node", "server.js"]
