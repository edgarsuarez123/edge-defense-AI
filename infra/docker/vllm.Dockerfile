# vLLM OpenAI-compatible server — Qwen2.5-7B-Instruct-AWQ
#
# Why vllm/vllm-openai and not nvidia/cuda + pip install vllm:
#   The official image ships vLLM pre-built with the correct CUDA kernels:
#   FlashAttention, PagedAttention, and AWQ fused dequantization kernels.
#   Building these from a cuda base requires hours of C++ compilation (nvcc).
#   The official image is ~6GB but adds zero custom code — env vars and CMD
#   args configure everything.
#
# Why volume-mount the model instead of baking it into the image:
#   Qwen2.5-7B-AWQ is ~4.2GB. Baking it means every image rebuild re-downloads
#   4.2GB and produces a ~10GB image. Volume-mount: download once to
#   models/weights/qwen2.5-7b-awq/ on the host, mount read-only at /models/qwen.
#   Image stays at ~6GB and rebuilds in seconds.
#
# This Dockerfile is used by docker-compose.edge.yml (resource-constrained build).
# docker-compose.dev.yml references the image directly — no build step during dev.

FROM vllm/vllm-openai:v0.32.0

# Configuration is passed via environment variables set in docker-compose:
#
#   VLLM_MODEL                  — absolute path to weights dir inside container
#                                 (mounted volume, e.g. /models/qwen)
#   VLLM_SERVED_MODEL_NAME      — name the API advertises; must match the `model`
#                                 field in LLM_CLIENT_CONFIG (vllm_config.py)
#   VLLM_GPU_MEMORY_UTILIZATION — fraction of total GPU VRAM reserved for vLLM
#                                 0.75 leaves ~2.5GB for TensorRT YOLO on 10GB card
#   VLLM_MAX_MODEL_LEN          — max context length in tokens
#                                 8192 fits within 0.75 util on 10GB (see decision-log)

EXPOSE 8000

# Override the base image entrypoint with bash so Docker env vars are expanded
# before the server process starts. Without this, $VLLM_MODEL is passed literally.
ENTRYPOINT ["/bin/bash", "-c"]
CMD ["python -m vllm.entrypoints.openai.api_server \
    --model \"${VLLM_MODEL}\" \
    --served-model-name \"${VLLM_SERVED_MODEL_NAME:-Qwen/Qwen2.5-7B-Instruct-AWQ}\" \
    --gpu-memory-utilization \"${VLLM_GPU_MEMORY_UTILIZATION:-0.75}\" \
    --max-model-len \"${VLLM_MAX_MODEL_LEN:-8192}\" \
    --max-num-seqs 4 \
    --enable-prefix-caching \
    --dtype float16 \
    --quantization awq \
    --host 0.0.0.0 \
    --port 8000"]
