FROM node:24-alpine

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY src ./src

USER node
ENV NODE_ENV=production \
    PI_RUNTIME_HOST=0.0.0.0

EXPOSE 3000
CMD ["node", "src/server.mjs"]
