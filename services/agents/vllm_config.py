"""
vLLM connection config — all LLM parameters in one place.

Why this exists:
  The agents service calls vLLM via the OpenAI-compatible REST API
  (langchain-openai's ChatOpenAI with a custom base_url). Having the model
  config as a single dict here means changing quantization or context length
  is a one-line edit that shows up clearly in git diff — not buried in a
  constructor call inside an agent node.

Why vLLM and not direct HuggingFace transformers:
  vLLM provides continuous batching — the threat analyst and ops agent run in
  parallel and vLLM handles both requests efficiently without blocking. Direct
  transformers would serialize them. vLLM also supports AWQ quantization natively
  and exposes the OpenAI-compatible API so the agent code doesn't need to change
  if the model changes.

These args match the docker-compose.edge.yml vllm service config (commented out,
to be uncommented in Week 6 when the full stack is wired together).
"""

import os

# VRAM budget on 10GB GPU (RTX 3080) — from architecture-design.md §4.8:
#   Qwen2.5-7B AWQ 4-bit:   ~4.0 GB
#   vLLM KV cache (0.75):   ~3.5 GB
#   TensorRT INT8 YOLO:      ~1.5 GB
#   System headroom:         ~1.0 GB
#   Total:                  ~10.0 GB
# On 16GB+ GPU: no VRAM pressure, gpu_memory_utilization can be raised.

VLLM_MODEL_ARGS = {
    "model": "Qwen/Qwen2.5-7B-Instruct-AWQ",
    "quantization": "awq",
    "max_model_len": 8192,          # safe headroom for military manuals + incident context
    "gpu_memory_utilization": 0.75, # leaves 25% for PostgreSQL, Redis, OS
    "max_num_seqs": 4,              # analyst + ops run in parallel → need >= 2 concurrent slots
    "tensor_parallel_size": 1,      # single GPU on Jetson/edge node
    "dtype": "float16",
    "enable_prefix_caching": True,  # cache system prompt KV across requests — big win when
                                    # all three agents share identical system prompt prefixes
}

# Base URL for the vLLM OpenAI-compatible API.
# In Docker Compose: service name "vllm" resolves via the internal bridge network.
# Override via VLLM_BASE_URL env var for local dev (e.g., http://localhost:8000/v1).
VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "http://vllm:8000/v1")

# API key is unused by vLLM (it doesn't enforce auth by default) but langchain-openai
# requires a non-empty value for the OpenAI client constructor. Set anything.
VLLM_API_KEY = os.environ.get("VLLM_API_KEY", "not-used")

# LLM client parameters passed to ChatOpenAI in graph.py
LLM_CLIENT_CONFIG = {
    "model": VLLM_MODEL_ARGS["model"],
    "base_url": VLLM_BASE_URL,
    "api_key": VLLM_API_KEY,
    "temperature": 0.1,     # low temperature: threat analysis should be deterministic
    "max_tokens": 1024,     # per-agent response cap; reporting agent's JSON is ~400 tokens
    "timeout": 30,          # 30s timeout matches error handling policy (architecture §4.5)
}
