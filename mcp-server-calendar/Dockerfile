FROM python:3.12-slim-bookworm

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY calendar_tools.py .
COPY server.py .

# Default environment variables
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=3000
ENV AGENT_WORKSPACE=/workspace

# Run the MCP server
CMD ["python", "server.py"]
