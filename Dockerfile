FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Switch to root directory in container
WORKDIR /app

# Install Python requirements first for layer caching
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser binaries
RUN playwright install chromium

# Copy the entire workspace into container to preserve the frontend/backend sibling structure
COPY . /app

# Switch to backend dir to run app.py
WORKDIR /app/backend

# Run with gunicorn on the dynamic PORT provided by the host, defaulting to 5000
CMD ["sh", "-c", "gunicorn -w 2 --timeout 300 -b 0.0.0.0:${PORT:-5000} app:app"]
