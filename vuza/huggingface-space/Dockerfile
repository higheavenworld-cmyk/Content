# VUZA as a web app on Hugging Face Spaces (Docker SDK).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ffmpeg fonts-noto-cjk fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Spaces run the container as user 1000
RUN useradd -m -u 1000 user
WORKDIR /home/user

ARG VUZA_REPO=https://github.com/AliRash3ed/VUZA-Free-AI-Video-Creator-and-Pinterest-Video-Scraper.git
RUN git clone --depth 1 "$VUZA_REPO" app \
    && pip install -r app/requirements.txt \
    && playwright install --with-deps chromium \
    && chown -R user:user /home/user/app

USER user
WORKDIR /home/user/app
EXPOSE 7860
CMD ["python", "-c", "import uvicorn, app; uvicorn.run(app.app, host='0.0.0.0', port=7860)"]
