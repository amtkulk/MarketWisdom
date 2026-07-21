FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Switch to root directory in container
WORKDIR /app

# Install Python requirements first for layer caching
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser binaries
RUN playwright install chromium

# Copy the entire workspace into container to preserve the frontend/backend sibling structure
# (.dockerignore keeps scratch test scripts, caches and local db files out of the image)
COPY . /app

# Run as a non-root user. The app launches Chromium on partially user-influenced input,
# so we don't want any browser/scraper bug executing with root privileges.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Switch to backend dir to run app.py
WORKDIR /app/backend

# Run with waitress on the dynamic PORT provided by the host, defaulting to 5000.
# --threads raised from the default 4 to 24: every endpoint is I/O-bound (waiting on
# yfinance / NSE / Gemini / Playwright), so extra threads let many users be served at
# once instead of queueing behind 4 slots. Tune down if the instance runs low on memory.
CMD ["sh", "-c", "waitress-serve --port=${PORT:-5000} --threads=24 app:app"]
