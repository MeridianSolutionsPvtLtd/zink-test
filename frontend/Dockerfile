# Build stage
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies  
RUN npm ci

# Copy source code
COPY . .

ARG VITE_API_URL=
ARG VITE_ENABLE_ANALYTICS=false

# Empty = same-origin /api (nginx proxies to backend). Pass a full https URL only for split hosting.
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_ENABLE_ANALYTICS=$VITE_ENABLE_ANALYTICS

# Build the app
RUN npm run build

# ---------- Runtime stage ----------
FROM nginx:1.25-alpine

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy custom nginx config (SPA routing)
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy Vite build output
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]