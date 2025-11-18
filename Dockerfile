# Multi-stage build for smaller image size and better caching
FROM --platform=linux/amd64 python:3.12-alpine AS builder

# Install build dependencies
RUN apk add --no-cache gcc musl-dev libffi-dev

# Set the working directory
WORKDIR /usr/src/app

# Copy only requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --user -r requirements.txt

# Final stage - minimal runtime image
FROM --platform=linux/amd64 python:3.12-alpine

# Install runtime dependencies only
RUN apk add --no-cache libffi

# Set the working directory
WORKDIR /usr/src/app

# Copy Python packages from builder
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY main.py .
COPY web_ui.py .
COPY templates ./templates

# Make sure scripts in .local are usable
ENV PATH=/root/.local/bin:$PATH

# Expose web UI port
EXPOSE 5000

# Add healthcheck
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health').read()" || exit 1

# Run app.py when the container launches
CMD ["python", "-u", "./main.py"]
