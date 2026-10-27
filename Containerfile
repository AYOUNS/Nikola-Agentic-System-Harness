FROM python:3.12-slim-bookworm

# Create a non-root user for security
RUN useradd -m -u 1001 agentuser

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY --chown=agentuser:agentuser . .

# Workspace directory the agent is sandboxed to.
# chmod 777 ensures the bind-mounted host directory is writable by uid 1001
# regardless of host ownership (works on Docker and rootful Podman).
RUN mkdir -p /workspace && chmod 777 /workspace

USER 1001

ENTRYPOINT ["python", "-m", "agent.cli"]
