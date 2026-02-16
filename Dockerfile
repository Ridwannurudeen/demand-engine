FROM python:3.11-slim

# Install Node.js 20 for ACP CLI
RUN apt-get update && apt-get install -y curl git && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Clone and install ACP CLI
RUN git clone https://github.com/Virtual-Protocol/openclaw-acp.git openclaw-acp && \
    cd openclaw-acp && npm install

# Copy source code
COPY src/ src/
COPY tests/ tests/
COPY pyproject.toml .

# Copy ACP offerings from tracked directory into cloned repo
COPY seller-offerings/ openclaw-acp/src/seller/offerings/

# Start script runs both services
COPY start.sh .
RUN chmod +x start.sh

CMD ["./start.sh"]
