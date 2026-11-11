FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-telecharge les modeles ONNX RapidOCR au build et ouvre les permissions pour UID 1000 (HF Spaces)
RUN python -c "import numpy as np; from src.ocr_engine import BannerOCREngine; e = BannerOCREngine(); e.read_crop(np.zeros((60, 200, 3), dtype=np.uint8))" \
    && mkdir -p /app/data /app/docs/data /.cache \
    && chmod -R 777 /app /usr/local/lib/python3.11/site-packages/rapidocr /.cache

ENV HOME=/app
ENV TIMEZONE=Europe/Paris
ENV GEMINI_MODEL=gemini-3.5-flash
ENV ANALYSIS_INTERVAL_SECONDS=300

EXPOSE 7860

CMD ["python", "-m", "src.main", "--serve", "--port", "7860", "--interval", "5"]
