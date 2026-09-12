FROM node:22-alpine
WORKDIR /app
ARG LAKO_API_INTERNAL_URL=http://localhost:8000
ENV LAKO_API_INTERNAL_URL=$LAKO_API_INTERNAL_URL
RUN corepack enable && corepack prepare pnpm@11.24.0 --activate
COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile=false --ignore-scripts
COPY . .
RUN pnpm build
CMD ["pnpm", "start"]
