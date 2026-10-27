FROM node:22-alpine AS frontend-build

WORKDIR /build/react-dashboard

COPY react-dashboard/package.json react-dashboard/package-lock.json ./
RUN npm ci

COPY react-dashboard ./
# The React app imports the existing design system so both implementations
# remain visually identical during the migration.
COPY static ../static
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY app ./app
COPY static ./static
COPY --from=frontend-build /build/react-dashboard/dist ./react-dashboard/dist

EXPOSE 8080

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
