FROM node:22-bookworm-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY bridge.cjs judge.yaml ./
ENV CONFIG_FILE=/app/judge.yaml
ENV NODE_OPTIONS=--max-old-space-size=128
CMD ["node", "bridge.cjs"]
