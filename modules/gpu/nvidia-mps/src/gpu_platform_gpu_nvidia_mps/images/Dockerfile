FROM ghcr.io/lupinemachines/lupine-server:v2.1.0-cuda-13.3.1-ubuntu24.04@sha256:721de895bce6a5b8645d0c2867e5cc72a2a5574d4c5086f1e5868d2de71e489b AS transport
FROM python:3.12-slim-bookworm AS cuda-libraries
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --no-deps --require-hashes --target /opt/cuda -r /tmp/requirements.txt

FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends python3 libnghttp2-14 libssl3t64 libstdc++6 libzstd1 && rm -rf /var/lib/apt/lists/*
COPY LICENSE.lupine NOTICE.lupine /usr/share/doc/lupine/
COPY --from=transport /opt/lupine/lib/liblupine-server.so /opt/lupine/lib/
COPY --from=cuda-libraries /opt/cuda/nvidia_nccl_cu12-2.27.5.dist-info/ /usr/share/doc/nvidia-nccl/
COPY --from=cuda-libraries /opt/cuda/nvidia_cuda_runtime_cu12-12.6.77.dist-info/ /usr/share/doc/nvidia-cuda-runtime/
COPY --from=cuda-libraries /opt/cuda/nvidia/nccl/lib/libnccl.so.2 /usr/local/lib/
COPY --from=cuda-libraries /opt/cuda/nvidia/cuda_runtime/lib/libcudart.so.12 /usr/local/lib/
RUN ldconfig
COPY cuda_worker.py /opt/lupine/cuda_worker.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["/usr/bin/python3", "/opt/lupine/cuda_worker.py"]
