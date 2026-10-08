FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so code changes don't invalidate this layer.
COPY pyproject.toml README.md ./
COPY prpilot/__init__.py prpilot/__init__.py
RUN pip install ".[tools,ui,gemini]"

COPY prpilot ./prpilot
COPY app_streamlit.py ./
RUN pip install --no-deps .

# Reviewed repositories are untrusted: don't run as root.
RUN useradd --create-home prpilot
USER prpilot

EXPOSE 8501
ENTRYPOINT ["prpilot"]
CMD ["--help"]
