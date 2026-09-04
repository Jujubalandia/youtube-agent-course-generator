# Frontend image: builds the CRA (react-scripts 5) app and serves the static
# bundle with nginx. Publish this container's port 80 to the host port that
# matches the origin CORS allows (default 3000, see docker-compose.yml).

# ---------- Build stage ----------
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
# --legacy-peer-deps is required: upstream pairs react@19 with
# @testing-library/react@13.4.0 whose peer range is react ^18; without the flag
# `npm ci` fails with ERESOLVE. The committed lockfile is installed as-is.
RUN npm ci --legacy-peer-deps

COPY public ./public
COPY src ./src
COPY tsconfig.json ./

# Base URL of the backend API, baked into the bundle at build time. Defaults to
# the original localhost workflow (http://localhost:8000).
ARG REACT_APP_API_BASE_URL=http://localhost:8000
ENV REACT_APP_API_BASE_URL=${REACT_APP_API_BASE_URL}

RUN npm run build

# ---------- Runtime stage ----------
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/build /usr/share/nginx/html

EXPOSE 80
