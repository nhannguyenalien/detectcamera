FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/mpl \
    MODEL_ROOT=/data/models/insightface \
    INSIGHTFACE_MODEL=buffalo_l

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-dev build-essential \
      libgl1 libglib2.0-0 ca-certificates curl gosu unzip \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && groupadd -g 10001 app && useradd -u 10001 -g 10001 -m -s /usr/sbin/nologin app

WORKDIR /app
RUN pip install --upgrade pip

# 1) numpy + cython trước để insightface build được extension
RUN pip install numpy==1.26.4 cython
# 2) insightface (kéo theo onnxruntime CPU — sẽ ép bỏ ở bước 4)
RUN pip install insightface==0.7.3
# 3) phần còn lại của service
RUN pip install \
      faiss-cpu==1.9.0.post1 \
      "fastapi>=0.115,<0.116" \
      "uvicorn[standard]>=0.32,<0.35" \
      "httpx>=0.27,<0.29" \
      "pillow>=10.4" \
      "python-multipart>=0.0.12" \
      "opencv-python-headless==4.10.0.84" \
      "prometheus-client==0.21.1"
# 4) ép onnxruntime-gpu là bản cuối cùng import được (CUDA 12.x + cuDNN 9)
RUN pip uninstall -y onnxruntime onnxruntime-gpu || true \
 && pip install "onnxruntime-gpu==1.19.2"

# 5) VENDOR model vào image (build-time, chỉ tải+giải nén — KHÔNG init ORT vì build không có GPU).
#    entrypoint sẽ copy sang volume /data/models nếu volume trống.
RUN mkdir -p /opt/models/insightface/models/buffalo_l \
 && curl -fsSL -o /tmp/buffalo_l.zip \
      https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip \
 && unzip -q /tmp/buffalo_l.zip -d /opt/models/insightface/models/buffalo_l \
 && rm /tmp/buffalo_l.zip \
 && ls -la /opt/models/insightface/models/buffalo_l

# 5b) DINOv2-S ONNX cho product visual search (~88MB). Load thẳng từ /opt/models.
RUN mkdir -p /opt/models/dinov2 \
 && ( curl -fsSL -o /opt/models/dinov2/model.onnx \
        https://huggingface.co/Xenova/dinov2-small/resolve/main/onnx/model.onnx \
   || curl -fsSL -o /opt/models/dinov2/model.onnx \
        https://huggingface.co/onnx-community/dinov2-small-ONNX/resolve/main/onnx/model.onnx ) \
 && ls -la /opt/models/dinov2

COPY app ./app
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 9000
# healthcheck = /health (liveness). Readiness thật ở /ready (model load + FAISS).
HEALTHCHECK --interval=30s --timeout=5s --start-period=150s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9000/health',timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "1"]
