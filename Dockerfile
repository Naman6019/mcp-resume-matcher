# Filesystem MCP server, Streamable HTTP transport.
#   docker build -t filesystem-mcp .
#   docker run -p 8765:8765 -v "$PWD/resumes:/app/resumes" filesystem-mcp
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir "jsonschema>=4.20" "pypdf>=4,<7" "python-docx>=1.1,<2"
COPY fs_tools.py filesystem_mcp_server.py ./
COPY resumes ./resumes
RUN useradd --create-home mcp && mkdir -p output && chown -R mcp /app
USER mcp
ENV MCP_FS_ROOT=/app MCP_FS_TRANSPORT=http MCP_FS_HOST=0.0.0.0 MCP_FS_PORT=8765
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz')"
CMD ["python", "filesystem_mcp_server.py"]
