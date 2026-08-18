from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import random
import re
from collections.abc import Callable, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from .comfyui_runtime import ComfyUIRuntimeError, comfyui_runtime

DEFAULT_IMAGE_MODEL = "RealVisXL_V5.0_Lightning_fp16.safetensors"
BIGASP_V2_MODEL = "bigaspv2.safetensors"
WAN_I2V_MODEL = "wan2.1_i2v_480p_14B_fp8_scaled.safetensors"
WAN_TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
WAN_VAE = "wan_2.1_vae.safetensors"
WAN_CLIP_VISION = "clip_vision_h.safetensors"
WAN22_REMIX_HIGH_MODEL = (
    "Wan2.2_Remix_NSFW_i2v_14b_high_lighting_fp8_e4m3fn_v3.0.safetensors"
)
WAN22_REMIX_LOW_MODEL = (
    "Wan2.2_Remix_NSFW_i2v_14b_low_lighting_fp8_e4m3fn_v3.0.safetensors"
)
WAN22_REMIX_TEXT_ENCODER = "nsfw_wan_umt5-xxl_fp8_scaled.safetensors"
WAN22_REMIX_MODELS = frozenset({WAN22_REMIX_HIGH_MODEL, WAN22_REMIX_LOW_MODEL})
LTX_CHECKPOINT = "ltx-2.3-22b-dev.safetensors"
LTX_TEXT_ENCODER = "gemma-3-12b-it/model-00001-of-00005.safetensors"
LTX_MIN_FREE_MEMORY = 96 * 1024**3
MINIMAX_H3_MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
MINIMAX_H3_TEXT_ENCODER = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
MINIMAX_H3_VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
MINIMAX_H3_AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
_MODEL_NAME_TRAILING = re.compile(r"\s*模型$")
_MODEL_FILE_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin")
_MIN_MODEL_PREFIX_LENGTH = 6


class ComfyUIError(RuntimeError):
    pass


class ComfyUIService:
    def __init__(self) -> None:
        self.base_url = os.getenv("AETHERCHAT_COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
        roots = os.getenv(
            "AETHERCHAT_COMFYUI_WORKFLOW_ROOTS",
            "/home/jimes0813/comfy-spark/ComfyUI/user/default/workflows",
        )
        self.workflow_roots = [Path(item).expanduser().resolve() for item in roots.split(":") if item]
        output_roots = os.getenv("AETHERCHAT_COMFYUI_OUTPUT_ROOTS", "")
        self.output_roots = [
            Path(item).expanduser().resolve()
            for item in output_roots.split(os.pathsep)
            if item
        ]
        if not self.output_roots:
            configured_root = os.getenv("AETHERCHAT_COMFYUI_ROOT")
            candidates = [
                Path(configured_root).expanduser().resolve()
                if configured_root
                else None,
                *(candidate for root in self.workflow_roots for candidate in root.parents
                  if candidate.name.lower() == "comfyui"),
                (Path.home() / "comfy-spark" / "ComfyUI").resolve(),
            ]
            root = next(
                (
                    candidate for candidate in candidates
                    if candidate is not None and (candidate / "main.py").is_file()
                ),
                None,
            )
            if root is not None:
                self.output_roots = [(root / "output").resolve()]

    async def ensure_ready(self) -> dict[str, Any]:
        try:
            return await comfyui_runtime.ensure_ready(
                self.base_url,
                self.workflow_roots,
            )
        except ComfyUIRuntimeError as exc:
            raise ComfyUIError(str(exc)) from exc

    def _client(self, timeout: float = 30.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, trust_env=False)

    async def _get_json(self, path: str, timeout: float = 30.0) -> dict[str, Any]:
        try:
            async with self._client(timeout) as client:
                response = await client.get(f"{self.base_url}{path}")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ComfyUIError("ComfyUI returned a non-object JSON response")
                return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise ComfyUIError(f"ComfyUI request failed: {exc}") from exc

    async def status(self) -> dict[str, Any]:
        runtime = await self.ensure_ready()
        stats = await self._get_json("/system_stats", 5)
        # Status is fed back into model context. Keep workflow entries compact;
        # callers that need node maps and input contracts use workflow_schema.
        inventory = await self.inventory(
            include_workflow_details=False,
            _ensure_runtime=False,
        )
        catalog = await self.model_catalog(
            _ensure_runtime=False,
            _inventory=inventory,
            _stats=stats,
        )
        return {
            "ready": True,
            "url": self.base_url,
            "runtime": runtime,
            "system": stats.get("system", {}),
            "devices": stats.get("devices", []),
            "inventory": inventory,
            # This compact, policy-aware view is authoritative for generation
            # availability. Raw GPU memory is shared on GB10 and cannot prove
            # that a high-memory video pipeline is safe to run.
            "generation": {
                "image_models": catalog["image_models"],
                "video_pipelines": catalog["video_pipelines"],
            },
        }

    async def inventory(
        self,
        *,
        include_workflow_details: bool = True,
        _ensure_runtime: bool = True,
    ) -> dict[str, Any]:
        if _ensure_runtime:
            await self.ensure_ready()
        info = await self._get_json("/object_info", 20)

        def choices(node: str, field: str) -> list[str]:
            raw = info.get(node, {}).get("input", {}).get("required", {}).get(field, [])
            if not raw:
                return []
            value = raw[0]
            if isinstance(value, list):
                return [str(item) for item in value]
            if value == "COMBO" and len(raw) > 1:
                return [str(item) for item in raw[1].get("options", [])]
            return []

        checkpoints = choices("CheckpointLoaderSimple", "ckpt_name")
        diffusion_models = choices("UNETLoader", "unet_name")
        loras = choices("LoraLoader", "lora_name")
        workflows_by_name: dict[str, dict[str, Any]] = {}
        for root in self.workflow_roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*.json")):
                if path.is_file() and os.access(path, os.R_OK):
                    if path.stem in workflows_by_name:
                        continue
                    if not include_workflow_details:
                        workflows_by_name[path.stem] = {
                            "name": path.stem,
                            "path": str(path),
                        }
                        continue
                    try:
                        graph = await self._read_workflow_graph(path, info)
                        descriptor = self._describe_graph(graph, info)
                        workflows_by_name[path.stem] = {
                            "name": path.stem,
                            "path": str(path),
                            **descriptor,
                        }
                    except ComfyUIError as exc:
                        workflows_by_name[path.stem] = {
                            "name": path.stem,
                            "path": str(path),
                            "error": str(exc),
                            "node_count": 0,
                            "node_map": {},
                            "dependencies": [],
                            "dependency_text": [],
                            "overridable_inputs": [],
                        }
        workflows = list(workflows_by_name.values())
        return {
            "checkpoints": checkpoints,
            "diffusion_models": diffusion_models,
            "loras": loras,
            "vae": choices("VAELoader", "vae_name"),
            "text_encoders": choices("CLIPLoader", "clip_name"),
            "clip_vision": choices("CLIPVisionLoader", "clip_name"),
            "upscalers": choices("UpscaleModelLoader", "model_name"),
            "capabilities": {
                name: name in info
                for name in (
                    "WanImageToVideo",
                    "SaveWEBM",
                    "ModelSamplingSD3",
                    "KSamplerAdvanced",
                    "CreateVideo",
                    "LTXVGemmaCLIPModelLoader",
                    "LTXVAudioVAELoader",
                    "LTXVConditioning",
                    "SaveVideo",
                    "MiniMaxH3ImageToVideo",
                    "VAEDecodeAudio",
                )
            },
            "workflows": workflows,
            "counts": {
                "checkpoints": len(checkpoints),
                "diffusion_models": len(diffusion_models),
                "loras": len(loras),
                "workflows": len(workflows),
            },
        }

    async def model_catalog(
        self,
        *,
        _ensure_runtime: bool = True,
        _inventory: dict[str, Any] | None = None,
        _stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        inventory = _inventory
        if inventory is None:
            inventory = await self.inventory(
                include_workflow_details=False,
                _ensure_runtime=_ensure_runtime,
            )
        stats = _stats if _stats is not None else await self._get_json(
            "/system_stats", 5
        )
        checkpoints = [str(item) for item in inventory.get("checkpoints", [])]
        diffusion_models = [
            str(item) for item in inventory.get("diffusion_models", [])
        ]
        image_models: list[dict[str, Any]] = []
        for name in checkpoints:
            lowered = name.casefold()
            if (
                "/" in name
                or "\\" in name
                or "ltx" in lowered
                or "echo" in lowered
            ):
                continue
            image_models.append({
                "name": name,
                "kind": "checkpoint",
                "recommended": lowered == DEFAULT_IMAGE_MODEL.casefold(),
            })
        for name in diffusion_models:
            if "qwen_image" not in name.casefold():
                continue
            image_models.append({
                "name": name,
                "kind": "diffusion_model",
                "recommended": not image_models,
            })

        capabilities = inventory.get("capabilities") or {}

        def missing_assets(requirements: list[tuple[str, str]]) -> list[str]:
            missing: list[str] = []
            for group, name in requirements:
                if group == "capabilities":
                    if not capabilities.get(name):
                        missing.append(f"节点 {name}")
                elif name not in inventory.get(group, []):
                    missing.append(name)
            return missing

        wan21_missing = missing_assets([
            ("diffusion_models", WAN_I2V_MODEL),
            ("text_encoders", WAN_TEXT_ENCODER),
            ("vae", WAN_VAE),
            ("clip_vision", WAN_CLIP_VISION),
            ("capabilities", "WanImageToVideo"),
            ("capabilities", "SaveWEBM"),
        ])
        wan22_remix_missing = missing_assets([
            ("diffusion_models", WAN22_REMIX_HIGH_MODEL),
            ("diffusion_models", WAN22_REMIX_LOW_MODEL),
            ("text_encoders", WAN22_REMIX_TEXT_ENCODER),
            ("vae", WAN_VAE),
            ("capabilities", "WanImageToVideo"),
            ("capabilities", "ModelSamplingSD3"),
            ("capabilities", "KSamplerAdvanced"),
            ("capabilities", "CreateVideo"),
            ("capabilities", "SaveVideo"),
        ])
        wan_models = [
            {
                "name": WAN_I2V_MODEL,
                "label": "Wan 2.1 I2V 14B",
                "installed": not wan21_missing,
                "missing": wan21_missing,
                "recommended": not wan21_missing,
            },
            {
                "name": WAN22_REMIX_HIGH_MODEL,
                "label": "Wan 2.2 Remix I2V 14B（双阶段）",
                "installed": not wan22_remix_missing,
                "missing": wan22_remix_missing,
                "companion_models": [WAN22_REMIX_LOW_MODEL],
                "recommended": bool(wan21_missing) and not wan22_remix_missing,
            },
        ]
        wan_installed = any(item["installed"] for item in wan_models)
        wan_missing = list(dict.fromkeys(wan21_missing + wan22_remix_missing))
        ltx_missing = missing_assets([
            ("checkpoints", LTX_CHECKPOINT),
            ("text_encoders", LTX_TEXT_ENCODER),
            ("capabilities", "LTXVGemmaCLIPModelLoader"),
            ("capabilities", "LTXVAudioVAELoader"),
            ("capabilities", "LTXVConditioning"),
            ("capabilities", "SaveVideo"),
        ])
        raw_system = stats.get("system")
        system: dict[str, Any] = raw_system if isinstance(raw_system, dict) else {}
        free_memory = int(system.get("ram_free") or 0)
        ltx_memory_ready = free_memory >= LTX_MIN_FREE_MEMORY
        ltx_installed = not ltx_missing
        ltx_warning = (
            "LTX 2.3 22B 会占用大量统一内存；当前可用内存低于 96 GiB。"
            "请先停止本地大语言模型，否则 ComfyUI 可能被 OOM killer 终止。"
            if ltx_installed and not ltx_memory_ready
            else "LTX 2.3 22B 属于高内存管线，生成前请确认没有大型本地模型并行占用内存。"
        )
        h3_missing = missing_assets([
            ("diffusion_models", MINIMAX_H3_MODEL),
            ("text_encoders", MINIMAX_H3_TEXT_ENCODER),
            ("vae", MINIMAX_H3_VIDEO_VAE),
            ("vae", MINIMAX_H3_AUDIO_VAE),
            ("capabilities", "MiniMaxH3ImageToVideo"),
            ("capabilities", "VAEDecodeAudio"),
            ("capabilities", "CreateVideo"),
            ("capabilities", "SaveVideo"),
        ])
        h3_installed = not h3_missing
        return {
            "image_models": image_models,
            "video_pipelines": [
                {
                    "id": "wan-i2v",
                    "label": "Wan I2V（图生视频）",
                    "installed": wan_installed,
                    "available": wan_installed,
                    "missing": [] if wan_installed else wan_missing,
                    "requires_input_image": False,
                    "accepts_input_image": True,
                    "auto_generates_input_image": True,
                    "models": wan_models,
                    "warning": "可使用本机图片；省略时自动生成起始图。",
                },
                {
                    "id": "ltx-t2v",
                    "label": "LTX 2.3 22B（文生视频）",
                    "installed": ltx_installed,
                    "available": ltx_installed and ltx_memory_ready,
                    "missing": ltx_missing,
                    "requires_input_image": False,
                    "minimum_free_memory_bytes": LTX_MIN_FREE_MEMORY,
                    "current_free_memory_bytes": free_memory,
                    "warning": ltx_warning,
                },
                {
                    "id": "minimax-h3",
                    "label": "MiniMax H3（文生/图生视频）",
                    "installed": h3_installed,
                    "available": h3_installed,
                    "missing": h3_missing,
                    "requires_input_image": False,
                    "accepts_input_image": True,
                    "auto_generates_input_image": False,
                    "models": [{
                        "name": MINIMAX_H3_MODEL,
                        "label": "MiniMax H3",
                        "installed": h3_installed,
                        "missing": h3_missing,
                        "recommended": h3_installed,
                    }],
                    "warning": "本地 MiniMax H3 可直接文生视频；附图时作为首帧。",
                },
            ],
            "assets": {
                key: inventory.get(key, [])
                for key in (
                    "checkpoints",
                    "diffusion_models",
                    "loras",
                    "vae",
                    "text_encoders",
                    "clip_vision",
                    "upscalers",
                    "workflows",
                )
            },
            "system": system,
            "devices": stats.get("devices", []),
        }

    @staticmethod
    def _dimensions(width: int, height: int) -> tuple[int, int]:
        if not 256 <= width <= 2048 or not 256 <= height <= 2048:
            raise ComfyUIError("Width and height must be between 256 and 2048")
        return width - width % 8, height - height % 8

    @staticmethod
    def _model_lookup_key(name: str) -> tuple[str, str]:
        key = _MODEL_NAME_TRAILING.sub("", str(name or "").strip()).casefold()
        stem = key
        for extension in _MODEL_FILE_EXTENSIONS:
            if stem.endswith(extension):
                stem = stem[: -len(extension)]
                break
        return key, stem

    @classmethod
    def _resolve_installed_model(
        cls,
        requested: str,
        candidates: Sequence[str],
        *,
        min_prefix_length: int = _MIN_MODEL_PREFIX_LENGTH,
        group_of: Callable[[str], str] | None = None,
    ) -> str | None:
        """Resolve a user/LLM model alias to exactly one installed filename.

        Accepts a full filename, a stem, or a unique prefix of either. Short
        or ambiguous prefixes stay unresolved so the caller can raise with the
        original requested name. ``group_of`` collapses companion files that
        belong to the same catalog option into one match.
        """
        requested_key, requested_stem = cls._model_lookup_key(requested)
        if not requested_key:
            return None
        available = [str(item) for item in candidates if str(item).strip()]
        for candidate in available:
            candidate_key, candidate_stem = cls._model_lookup_key(candidate)
            if candidate_key == requested_key or candidate_stem == requested_stem:
                return candidate
        if len(requested_stem) < min_prefix_length:
            return None
        prefix_hits = [
            candidate
            for candidate in available
            if (
                cls._model_lookup_key(candidate)[0].startswith(requested_key)
                or cls._model_lookup_key(candidate)[1].startswith(requested_stem)
            )
        ]
        if not prefix_hits:
            return None
        if group_of is not None:
            grouped: list[str] = []
            seen: set[str] = set()
            for candidate in prefix_hits:
                group_key = str(group_of(candidate) or candidate)
                if group_key in seen:
                    continue
                seen.add(group_key)
                grouped.append(candidate)
            prefix_hits = grouped
        if len(prefix_hits) == 1:
            return prefix_hits[0]
        return None

    @classmethod
    def _resolve_video_pipeline(
        cls,
        catalog: dict[str, Any],
        requested: str,
        fallback: str,
    ) -> str:
        fallback = fallback or "wan-i2v"
        requested_model = str(requested or "").strip()
        if requested_model.casefold() in {"", "auto", "default"}:
            return fallback
        names: list[str] = []
        owner: dict[str, str] = {}
        for pipe in catalog.get("video_pipelines") or []:
            pipe_id = str(pipe.get("id") or "").strip()
            if pipe_id:
                owner[pipe_id.casefold()] = pipe_id
            for item in pipe.get("models") or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if name:
                    names.append(name)
                    owner[name.casefold()] = pipe_id
                for companion in item.get("companion_models") or []:
                    companion_name = str(companion).strip()
                    if not companion_name:
                        continue
                    names.append(companion_name)
                    owner[companion_name.casefold()] = pipe_id
        resolved = cls._resolve_installed_model(requested_model, names)
        if resolved:
            return owner.get(resolved.casefold(), fallback)
        key = requested_model.casefold()
        if key in owner:
            return owner[key]
        if "minimax" in key or re.search(r"(?:^|[^a-z0-9])h3(?:[^a-z0-9]|$)", key):
            return "minimax-h3"
        return fallback

    async def generate_image(
        self,
        prompt: str,
        *,
        model: str | None = None,
        negative_prompt: str = "blurry, low quality, distorted, watermark, text, bad anatomy",
        width: int = 768,
        height: int = 768,
        steps: int | None = None,
        cfg: float | None = None,
        seed: int | None = None,
        lora: str | None = None,
        lora_strength: float = 1.0,
    ) -> dict[str, Any]:
        width, height = self._dimensions(width, height)
        inventory = await self.inventory(include_workflow_details=False)
        requested_model = str(model or "").strip()
        if requested_model.casefold() in {"", "auto", "default"}:
            checkpoints = [
                str(item)
                for item in inventory.get("checkpoints", [])
                if str(item).strip()
            ]
            diffusion_models = [
                str(item)
                for item in inventory.get("diffusion_models", [])
                if str(item).strip()
            ]
            model = next(
                (
                    candidate
                    for candidate in checkpoints
                    if candidate.casefold() == DEFAULT_IMAGE_MODEL.casefold()
                ),
                None,
            )
            if model is None:
                model = next(
                    (
                        candidate
                        for candidate in checkpoints
                        if "/" not in candidate
                        and "\\" not in candidate
                        and "ltx" not in candidate.casefold()
                    ),
                    None,
                )
            if model is None:
                model = next(
                    (
                        candidate
                        for candidate in diffusion_models
                        if "qwen_image" in candidate.casefold()
                    ),
                    None,
                )
            if model is None:
                raise ComfyUIError(
                    "No supported image checkpoint or Qwen-Image model is installed"
                )
        else:
            available_models = [
                str(item)
                for group in ("checkpoints", "diffusion_models")
                for item in inventory.get(group, [])
                if str(item).strip()
            ]
            model = (
                self._resolve_installed_model(requested_model, available_models)
                or requested_model
            )
        is_bigasp_v2 = Path(model).name.casefold() == BIGASP_V2_MODEL.casefold()
        resolved_steps = steps if steps is not None else (40 if is_bigasp_v2 else 6)
        resolved_cfg = cfg if cfg is not None else (3.4 if is_bigasp_v2 else 2.0)
        if lora and lora not in inventory.get("loras", []):
            available_loras = ", ".join(inventory.get("loras", [])) or "(none)"
            raise ComfyUIError(
                f"Unsupported LoRA: {lora}. Available LoRAs: {available_loras}"
            )
        if model in inventory["checkpoints"]:
            graph = self._checkpoint_image_graph(
                prompt, negative_prompt, model, width, height, resolved_steps,
                resolved_cfg, seed, lora, lora_strength,
            )
        elif model in inventory["diffusion_models"] and "qwen_image" in model.lower():
            graph = self._qwen_image_graph(
                prompt, negative_prompt, model, width, height, resolved_steps,
                resolved_cfg, seed, lora, lora_strength,
            )
        else:
            raise ComfyUIError(f"Unsupported image model: {model}")
        resolved_seed = self._first_graph_input(graph, "seed", "noise_seed")
        return await self._submit_and_wait(
            graph,
            timeout=1800,
            metadata={
                "operation": "generate_image",
                "parameters": {
                    "prompt": prompt,
                    "negative_prompt": negative_prompt,
                    "model": model,
                    "width": width,
                    "height": height,
                    "steps": max(1, min(100, resolved_steps)),
                    "cfg": max(0.1, min(30.0, resolved_cfg)),
                    "seed": resolved_seed,
                    "lora": lora,
                    "lora_strength": lora_strength if lora else None,
                },
            },
        )

    @staticmethod
    def _checkpoint_image_graph(
        prompt: str,
        negative: str,
        model: str,
        width: int,
        height: int,
        steps: int,
        cfg: float,
        seed: int | None,
        lora: str | None,
        lora_strength: float,
    ) -> dict[str, Any]:
        model_ref: list[Any] = ["1", 0]
        clip_ref: list[Any] = ["1", 1]
        graph: dict[str, Any] = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}},
        }
        if lora:
            graph["2"] = {"class_type": "LoraLoader", "inputs": {
                "model": model_ref, "clip": clip_ref, "lora_name": lora,
                "strength_model": lora_strength, "strength_clip": lora_strength,
            }}
            model_ref, clip_ref = ["2", 0], ["2", 1]
        graph.update({
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": clip_ref}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": clip_ref}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {
                "width": width, "height": height, "batch_size": 1,
            }},
            "6": {"class_type": "KSampler", "inputs": {
                "model": model_ref, "positive": ["3", 0], "negative": ["4", 0],
                "latent_image": ["5", 0], "seed": seed if seed is not None else random.getrandbits(63),
                "steps": max(1, min(100, steps)), "cfg": max(0.1, min(30.0, cfg)),
                "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0,
            }},
            "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
            "8": {"class_type": "SaveImage", "inputs": {
                "filename_prefix": "AetherChat/image", "images": ["7", 0],
            }},
        })
        return graph

    @staticmethod
    def _qwen_image_graph(
        prompt: str,
        negative: str,
        model: str,
        width: int,
        height: int,
        steps: int,
        cfg: float,
        seed: int | None,
        lora: str | None,
        lora_strength: float,
    ) -> dict[str, Any]:
        model_ref: list[Any] = ["1", 0]
        graph: dict[str, Any] = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": model, "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {
                "clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image",
            }},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        }
        if lora:
            graph["9"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
                "model": model_ref, "lora_name": lora, "strength_model": lora_strength,
            }}
            model_ref = ["9", 0]
        graph.update({
            "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": model_ref, "shift": 3.1}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 0]}},
            "7": {"class_type": "EmptySD3LatentImage", "inputs": {
                "width": width, "height": height, "batch_size": 1,
            }},
            "10": {"class_type": "KSampler", "inputs": {
                "model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0],
                "latent_image": ["7", 0], "seed": seed if seed is not None else random.getrandbits(63),
                "steps": max(1, min(100, steps)), "cfg": max(0.1, min(30.0, cfg)),
                "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            }},
            "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 0]}},
            "12": {"class_type": "SaveImage", "inputs": {
                "filename_prefix": "AetherChat/qwen_image", "images": ["11", 0],
            }},
        })
        return graph

    async def generate_video(
        self,
        prompt: str,
        *,
        pipeline: str = "wan-i2v",
        model: str | None = None,
        input_image: str | None = None,
        width: int = 480,
        height: int = 832,
        frames: int = 17,
        fps: int = 16,
        steps: int = 8,
        cfg: float = 4.0,
        seed: int | None = None,
    ) -> dict[str, Any]:
        await self.ensure_ready()
        catalog = await self.model_catalog(_ensure_runtime=False)
        pipeline = self._resolve_video_pipeline(catalog, str(model or ""), pipeline)
        pipeline_option = next(
            (
                item
                for item in catalog["video_pipelines"]
                if item["id"] == pipeline
            ),
            None,
        )
        if pipeline_option is None:
            raise ComfyUIError("pipeline must be wan-i2v, ltx-t2v, or minimax-h3")
        if not pipeline_option["installed"]:
            raise ComfyUIError(
                f"ComfyUI pipeline {pipeline} is missing required models or nodes: "
                + ", ".join(pipeline_option["missing"])
            )
        if not pipeline_option["available"]:
            raise ComfyUIError(str(pipeline_option["warning"]))
        width, height = self._dimensions(width, height)
        frames = max(1, min(161, frames))
        resolved_model: str | None = None
        starter_image_prompt_id: str | None = None
        if pipeline == "wan-i2v":
            model_options = [
                item
                for item in pipeline_option.get("models") or []
                if isinstance(item, dict)
            ]
            model_by_name: dict[str, dict[str, Any]] = {}
            lookup_names: list[str] = []
            for item in model_options:
                option_name = str(item.get("name") or "").strip()
                if not option_name:
                    continue
                lookup_names.append(option_name)
                model_by_name[option_name.casefold()] = item
                for companion in item.get("companion_models") or []:
                    companion_name = str(companion).strip()
                    if not companion_name:
                        continue
                    lookup_names.append(companion_name)
                    model_by_name[companion_name.casefold()] = item
            requested_model = str(model or "").strip()
            if requested_model and requested_model.casefold() != "auto":
                resolved_name = self._resolve_installed_model(
                    requested_model,
                    lookup_names,
                    group_of=lambda name: str(
                        model_by_name.get(name.casefold(), {}).get("name") or name
                    ),
                )
                selected_model = (
                    model_by_name.get(resolved_name.casefold())
                    if resolved_name
                    else None
                )
                if selected_model is None:
                    available = ", ".join(
                        str(item.get("name") or "")
                        for item in model_options
                        if item.get("installed")
                    ) or WAN_I2V_MODEL
                    raise ComfyUIError(
                        f"Unsupported wan-i2v model: {requested_model}. "
                        f"Available models: {available}"
                    )
            else:
                selected_model = next(
                    (
                        item
                        for item in model_options
                        if item.get("installed") and item.get("recommended")
                    ),
                    None,
                ) or next(
                    (item for item in model_options if item.get("installed")),
                    None,
                )
                # Compatibility with callers that mock the pre-model-catalog
                # pipeline shape.
                selected_model = selected_model or {
                    "name": WAN_I2V_MODEL,
                    "installed": pipeline_option["installed"],
                    "missing": pipeline_option.get("missing") or [],
                }
            if not selected_model.get("installed"):
                raise ComfyUIError(
                    f"ComfyUI video model {selected_model.get('name')} is missing: "
                    + ", ".join(selected_model.get("missing") or [])
                )
            resolved_model = str(selected_model["name"])

            if input_image:
                image_name = await self._upload_image(input_image)
                input_image_source = "local-file"
            else:
                starter = await self.generate_image(
                    prompt,
                    width=width,
                    height=height,
                    seed=seed,
                )
                starter_image_prompt_id = str(starter.get("prompt_id") or "") or None
                image_name = await self._upload_generated_image(starter)
                input_image_source = "auto-generated"

            if resolved_model in WAN22_REMIX_MODELS:
                graph = self._wan22_remix_i2v_graph(
                    prompt,
                    image_name,
                    width,
                    height,
                    frames,
                    fps,
                    steps,
                    cfg,
                    seed,
                )
            else:
                graph = self._wan_i2v_graph(
                    prompt,
                    image_name,
                    width,
                    height,
                    frames,
                    fps,
                    steps,
                    cfg,
                    seed,
                )
        elif pipeline == "ltx-t2v":
            width, height = width - width % 32, height - height % 32
            resolved_model = LTX_CHECKPOINT
            input_image_source = None
            graph = self._ltx_t2v_graph(prompt, width, height, frames, fps, steps, cfg, seed)
        elif pipeline == "minimax-h3":
            width, height = max(32, width - width % 32), max(32, height - height % 32)
            frames = max(5, frames)
            resolved_model = MINIMAX_H3_MODEL
            image_name = None
            input_image_source = None
            if input_image:
                image_name = await self._upload_image(input_image)
                input_image_source = "local-file"
            graph = self._minimax_h3_graph(
                prompt, image_name, width, height, frames, steps, seed,
            )
            fps = 24
        else:
            raise ComfyUIError("pipeline must be wan-i2v, ltx-t2v, or minimax-h3")
        return await self._submit_and_wait(
            graph,
            timeout=3600,
            metadata={
                "operation": "generate_video",
                "parameters": {
                    "prompt": prompt,
                    "pipeline": pipeline,
                    "model": resolved_model,
                    "input_image": input_image,
                    "input_image_source": input_image_source,
                    "starter_image_prompt_id": starter_image_prompt_id,
                    "width": width,
                    "height": height,
                    "frames": frames,
                    "fps": fps,
                    "steps": max(1, min(100, steps)),
                    "cfg": max(0.1, min(30.0, cfg)),
                    "seed": self._first_graph_input(graph, "seed", "noise_seed"),
                },
            },
        )

    async def _upload_image(self, path_value: str) -> str:
        path = Path(path_value).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ComfyUIError("Input image must be an existing PNG, JPEG, or WebP file")
        if path.stat().st_size > 50 * 1024 * 1024:
            raise ComfyUIError("Input image exceeds 50 MB")
        async with self._client(120) as client:
            with path.open("rb") as handle:
                response = await client.post(
                    f"{self.base_url}/upload/image",
                    files={"image": (path.name, handle, "application/octet-stream")},
                    data={"overwrite": "true"},
                )
            response.raise_for_status()
            result = response.json()
        return f"{result.get('subfolder', '').strip('/')}/{result['name']}".lstrip("/")

    async def _upload_generated_image(self, result: dict[str, Any]) -> str:
        for raw_file in result.get("files") or []:
            if not isinstance(raw_file, dict):
                continue
            filename = Path(str(raw_file.get("filename") or "starter.png")).name
            suffix = Path(filename).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            local_path = str(raw_file.get("_local_path") or "").strip()
            if local_path:
                return await self._upload_image(local_path)
            data_url = str(raw_file.get("image_data_url") or "")
            if not data_url.startswith("data:image/") or ";base64," not in data_url:
                continue
            header, encoded = data_url.split(",", 1)
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError, binascii.Error) as exc:
                raise ComfyUIError("Generated starter image data is invalid") from exc
            if not data or len(data) > 50 * 1024 * 1024:
                raise ComfyUIError("Generated starter image is empty or exceeds 50 MB")
            mime = header[5:].split(";", 1)[0]
            async with self._client(120) as client:
                response = await client.post(
                    f"{self.base_url}/upload/image",
                    files={"image": (filename, data, mime)},
                    data={"overwrite": "true"},
                )
                response.raise_for_status()
                uploaded = response.json()
            return (
                f"{uploaded.get('subfolder', '').strip('/')}/{uploaded['name']}"
            ).lstrip("/")
        raise ComfyUIError(
            "Automatic starter image generation completed without a usable image output"
        )

    @staticmethod
    def _wan_i2v_graph(
        prompt: str, image_name: str, width: int, height: int, frames: int,
        fps: int, steps: int, cfg: float, seed: int | None,
    ) -> dict[str, Any]:
        negative = "blurry, low quality, distorted, static, frozen, watermark, text, bad anatomy"
        return {
            "1": {"class_type": "UNETLoader", "inputs": {
                "unet_name": WAN_I2V_MODEL, "weight_dtype": "fp8_e4m3fn",
            }},
            "2": {"class_type": "CLIPLoader", "inputs": {
                "clip_name": WAN_TEXT_ENCODER, "type": "wan",
            }},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": WAN_VAE}},
            "4": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": WAN_CLIP_VISION}},
            "5": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 0]}},
            "8": {"class_type": "CLIPVisionEncode", "inputs": {
                "clip_vision": ["4", 0], "image": ["5", 0], "crop": "center",
            }},
            "9": {"class_type": "WanImageToVideo", "inputs": {
                "positive": ["6", 0], "negative": ["7", 0], "vae": ["3", 0],
                "width": width, "height": height, "length": frames, "batch_size": 1,
                "clip_vision_output": ["8", 0], "start_image": ["5", 0],
            }},
            "10": {"class_type": "KSampler", "inputs": {
                "model": ["1", 0], "positive": ["9", 0], "negative": ["9", 1],
                "latent_image": ["9", 2], "seed": seed if seed is not None else random.getrandbits(63),
                "steps": max(1, min(100, steps)), "cfg": max(0.1, min(30.0, cfg)),
                "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            }},
            "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["3", 0]}},
            "12": {"class_type": "SaveWEBM", "inputs": {
                "images": ["11", 0], "filename_prefix": "AetherChat/wan_i2v",
                "codec": "vp9", "fps": float(max(1, min(60, fps))), "crf": 28.0,
            }},
        }

    @staticmethod
    def _wan22_remix_i2v_graph(
        prompt: str,
        image_name: str,
        width: int,
        height: int,
        frames: int,
        fps: int,
        steps: int,
        cfg: float,
        seed: int | None,
    ) -> dict[str, Any]:
        """Build the installed Wan 2.2 Remix high/low two-stage graph."""

        negative = (
            "static, blurry, worst quality, low quality, deformed, watermark, "
            "text, logo, extra fingers, bad anatomy"
        )
        bounded_steps = max(2, min(100, steps))
        switch_step = max(1, bounded_steps // 2)
        noise_seed = seed if seed is not None else random.getrandbits(63)
        bounded_cfg = max(0.1, min(30.0, cfg))
        return {
            "1": {"class_type": "UNETLoader", "inputs": {
                "unet_name": WAN22_REMIX_HIGH_MODEL,
                "weight_dtype": "fp8_e4m3fn",
            }},
            "2": {"class_type": "UNETLoader", "inputs": {
                "unet_name": WAN22_REMIX_LOW_MODEL,
                "weight_dtype": "fp8_e4m3fn",
            }},
            "3": {"class_type": "ModelSamplingSD3", "inputs": {
                "model": ["1", 0], "shift": 8.0,
            }},
            "4": {"class_type": "ModelSamplingSD3", "inputs": {
                "model": ["2", 0], "shift": 8.0,
            }},
            "5": {"class_type": "CLIPLoader", "inputs": {
                "clip_name": WAN22_REMIX_TEXT_ENCODER,
                "type": "wan",
                "device": "default",
            }},
            "6": {"class_type": "CLIPTextEncode", "inputs": {
                "text": prompt, "clip": ["5", 0],
            }},
            "7": {"class_type": "CLIPTextEncode", "inputs": {
                "text": negative, "clip": ["5", 0],
            }},
            "8": {"class_type": "VAELoader", "inputs": {
                "vae_name": WAN_VAE,
            }},
            "9": {"class_type": "LoadImage", "inputs": {
                "image": image_name,
            }},
            "10": {"class_type": "WanImageToVideo", "inputs": {
                "positive": ["6", 0],
                "negative": ["7", 0],
                "vae": ["8", 0],
                "start_image": ["9", 0],
                "width": width,
                "height": height,
                "length": frames,
                "batch_size": 1,
            }},
            "11": {"class_type": "KSamplerAdvanced", "inputs": {
                "model": ["3", 0],
                "add_noise": "enable",
                "noise_seed": noise_seed,
                "steps": bounded_steps,
                "cfg": bounded_cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["10", 0],
                "negative": ["10", 1],
                "latent_image": ["10", 2],
                "start_at_step": 0,
                "end_at_step": switch_step,
                "return_with_leftover_noise": "enable",
            }},
            "12": {"class_type": "KSamplerAdvanced", "inputs": {
                "model": ["4", 0],
                "add_noise": "disable",
                "noise_seed": 0,
                "steps": bounded_steps,
                "cfg": bounded_cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["10", 0],
                "negative": ["10", 1],
                "latent_image": ["11", 0],
                "start_at_step": switch_step,
                "end_at_step": 10_000,
                "return_with_leftover_noise": "disable",
            }},
            "13": {"class_type": "VAEDecode", "inputs": {
                "samples": ["12", 0], "vae": ["8", 0],
            }},
            "14": {"class_type": "CreateVideo", "inputs": {
                "images": ["13", 0], "fps": float(max(1, min(60, fps))),
            }},
            "15": {"class_type": "SaveVideo", "inputs": {
                "video": ["14", 0],
                "filename_prefix": "AetherChat/wan22_remix_i2v",
                "format": "auto",
                "codec": "auto",
            }},
        }

    @staticmethod
    def _ltx_t2v_graph(
        prompt: str, width: int, height: int, frames: int, fps: int,
        steps: int, cfg: float, seed: int | None,
    ) -> dict[str, Any]:
        negative = "worst quality, blurry, distorted, jittery, flickering"
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": LTX_CHECKPOINT}},
            "2": {"class_type": "LTXVGemmaCLIPModelLoader", "inputs": {
                "gemma_path": LTX_TEXT_ENCODER,
                "ltxv_path": LTX_CHECKPOINT, "max_length": 1024,
            }},
            "3": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": LTX_CHECKPOINT}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
            "6": {"class_type": "LTXVConditioning", "inputs": {
                "positive": ["4", 0], "negative": ["5", 0], "frame_rate": float(fps),
            }},
            "7": {"class_type": "EmptyLTXVLatentVideo", "inputs": {
                "width": width, "height": height, "length": frames, "batch_size": 1,
            }},
            "8": {"class_type": "LTXVEmptyLatentAudio", "inputs": {
                "frames_number": frames, "frame_rate": fps, "batch_size": 1, "audio_vae": ["3", 0],
            }},
            "9": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["7", 0], "audio_latent": ["8", 0]}},
            "10": {"class_type": "CFGGuider", "inputs": {
                "model": ["1", 0], "positive": ["6", 0], "negative": ["6", 1], "cfg": cfg,
            }},
            "11": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "12": {"class_type": "LTXVScheduler", "inputs": {
                "steps": steps, "max_shift": 2.05, "base_shift": 0.95, "stretch": True,
                "terminal": 0.1, "latent": ["9", 0],
            }},
            "13": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed if seed is not None else random.getrandbits(63)}},
            "14": {"class_type": "SamplerCustomAdvanced", "inputs": {
                "noise": ["13", 0], "guider": ["10", 0], "sampler": ["11", 0],
                "sigmas": ["12", 0], "latent_image": ["9", 0],
            }},
            "15": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["14", 0]}},
            "16": {"class_type": "LTXVTiledVAEDecode", "inputs": {
                "vae": ["1", 2], "latents": ["15", 0], "horizontal_tiles": 1,
                "vertical_tiles": 1, "overlap": 1, "last_frame_fix": False,
                "working_device": "auto", "working_dtype": "auto",
            }},
            "17": {"class_type": "LTXVAudioVAEDecode", "inputs": {"samples": ["15", 1], "audio_vae": ["3", 0]}},
            "18": {"class_type": "CreateVideo", "inputs": {"images": ["16", 0], "fps": float(fps), "audio": ["17", 0]}},
            "19": {"class_type": "SaveVideo", "inputs": {
                "video": ["18", 0], "filename_prefix": "AetherChat/ltx_t2v", "format": "auto", "codec": "auto",
            }},
        }

    @staticmethod
    def _minimax_h3_graph(
        prompt: str,
        start_image: str | None,
        width: int,
        height: int,
        frames: int,
        steps: int,
        seed: int | None,
    ) -> dict[str, Any]:
        graph: dict[str, Any] = {
            "1": {"class_type": "UNETLoader", "inputs": {
                "unet_name": MINIMAX_H3_MODEL, "weight_dtype": "default",
            }},
            "2": {"class_type": "CLIPLoader", "inputs": {
                "clip_name": MINIMAX_H3_TEXT_ENCODER, "type": "minimax", "device": "default",
            }},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": MINIMAX_H3_VIDEO_VAE}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": MINIMAX_H3_AUDIO_VAE}},
            "5": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
                "clip": ["2", 0], "vae": ["3", 0], "prompt": prompt,
                "width": width, "height": height, "length": max(5, frames),
            }},
            "6": {"class_type": "RandomNoise", "inputs": {
                "noise_seed": seed if seed is not None else random.getrandbits(63),
            }},
            "7": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
            "8": {"class_type": "BasicScheduler", "inputs": {
                "model": ["1", 0], "scheduler": "simple",
                "steps": max(1, min(100, steps)), "denoise": 1.0,
            }},
            "9": {"class_type": "BasicGuider", "inputs": {
                "model": ["1", 0], "conditioning": ["5", 0],
            }},
            "10": {"class_type": "SamplerCustomAdvanced", "inputs": {
                "noise": ["6", 0], "guider": ["9", 0], "sampler": ["7", 0],
                "sigmas": ["8", 0], "latent_image": ["5", 1],
            }},
            "11": {"class_type": "VAEDecode", "inputs": {
                "samples": ["10", 0], "vae": ["3", 0],
            }},
            "12": {"class_type": "VAEDecodeAudio", "inputs": {
                "samples": ["10", 0], "vae": ["4", 0],
            }},
            "13": {"class_type": "CreateVideo", "inputs": {
                "images": ["11", 0], "audio": ["12", 0], "fps": 24.0,
            }},
            "14": {"class_type": "SaveVideo", "inputs": {
                "video": ["13", 0], "filename_prefix": "AetherChat/minimax_h3",
                "format": "auto", "codec": "auto",
            }},
        }
        if start_image:
            graph["15"] = {"class_type": "LoadImage", "inputs": {"image": start_image}}
            graph["5"]["inputs"]["first_frame"] = ["15", 0]
        return graph

    async def run_workflow(self, name: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.ensure_ready()
        path = self._workflow_path(name)
        info = await self._get_json("/object_info", 20)
        graph = await self._read_workflow_graph(path, info)
        descriptor = self._describe_graph(graph, info)
        allowed = {
            (str(item["node_id"]), str(item["input"])): item
            for item in descriptor["overridable_inputs"]
        }
        normalized_overrides: dict[str, dict[str, Any]] = {}
        for node_id, values in (overrides or {}).items():
            normalized_node_id = str(node_id)
            if normalized_node_id not in graph or not isinstance(values, dict):
                raise ComfyUIError(
                    f"Invalid workflow override node: {node_id}. "
                    f"Available nodes: {', '.join(descriptor['node_map'])}"
                )
            normalized_overrides[normalized_node_id] = {}
            for input_name, value in values.items():
                specification = allowed.get((normalized_node_id, str(input_name)))
                if specification is None:
                    available = [
                        item["input"]
                        for item in descriptor["overridable_inputs"]
                        if item["node_id"] == normalized_node_id
                    ]
                    raise ComfyUIError(
                        f"Node {normalized_node_id} ({descriptor['node_map'][normalized_node_id]}) "
                        f"input {input_name!r} is not overridable. Available inputs: "
                        f"{', '.join(available) or '(none)'}"
                    )
                self._validate_override_value(specification, value)
                normalized_overrides[normalized_node_id][str(input_name)] = value
        for node_id, values in normalized_overrides.items():
            graph[node_id]["inputs"].update(values)
        return await self._submit_and_wait(
            graph,
            timeout=3600,
            metadata={
                "operation": "run_workflow",
                "workflow_name": path.stem,
                "parameters": {"overrides": normalized_overrides},
                "workflow": descriptor,
            },
        )

    async def workflow_schema(self, name: str) -> dict[str, Any]:
        await self.ensure_ready()
        path = self._workflow_path(name)
        info = await self._get_json("/object_info", 20)
        graph = await self._read_workflow_graph(path, info)
        return {
            "name": path.stem,
            "path": str(path),
            **self._describe_graph(graph, info),
        }

    async def _read_workflow_graph(
        self,
        path: Path,
        info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            source = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ComfyUIError(f"Unable to read workflow: {exc}") from exc
        return source if self._is_api_graph(source) else await self._litegraph_to_api(source, info)

    def _workflow_path(self, name: str) -> Path:
        candidate_name = name[:-5] if name.endswith(".json") else name
        if not candidate_name or Path(candidate_name).name != candidate_name:
            raise ComfyUIError("Workflow name must not contain a path")
        for root in self.workflow_roots:
            candidate = (root / f"{candidate_name}.json").resolve()
            if candidate.parent == root and candidate.is_file() and os.access(candidate, os.R_OK):
                return candidate
        raise ComfyUIError(f"Unknown saved workflow: {name}")

    @staticmethod
    def _is_api_graph(source: Any) -> bool:
        return isinstance(source, dict) and bool(source) and all(
            isinstance(value, dict) and "class_type" in value for value in source.values()
        )

    @staticmethod
    def _is_link(value: Any, graph: dict[str, Any]) -> bool:
        return (
            isinstance(value, list)
            and len(value) == 2
            and str(value[0]) in graph
            and isinstance(value[1], int)
        )

    @staticmethod
    def _first_graph_input(graph: dict[str, Any], *names: str) -> Any:
        for node in graph.values():
            inputs = node.get("inputs") or {}
            for name in names:
                if name in inputs:
                    return inputs[name]
        return None

    @staticmethod
    def _input_specification(
        info: dict[str, Any],
        class_type: str,
        input_name: str,
    ) -> tuple[str, bool, list[Any], dict[str, Any]]:
        groups = info.get(class_type, {}).get("input", {})
        required = input_name in groups.get("required", {})
        raw = (
            groups.get("required", {}).get(input_name)
            if required
            else groups.get("optional", {}).get(input_name)
        )
        choices: list[Any] = []
        options: dict[str, Any] = {}
        type_name = "ANY"
        if isinstance(raw, (list, tuple)) and raw:
            raw_type = raw[0]
            if isinstance(raw_type, list):
                type_name = "COMBO"
                choices = raw_type[:200]
            else:
                type_name = str(raw_type)
            if len(raw) > 1 and isinstance(raw[1], dict):
                options = {
                    str(key): value
                    for key, value in list(raw[1].items())[:30]
                    if isinstance(value, (str, int, float, bool)) or value is None
                }
                if type_name == "COMBO" and isinstance(raw[1].get("options"), list):
                    choices = raw[1]["options"][:200]
        elif raw is not None:
            type_name = str(raw)
        return type_name, required, choices, options

    @classmethod
    def _describe_graph(
        cls,
        graph: dict[str, Any],
        info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        object_info = info or {}
        node_map: dict[str, str] = {}
        nodes: list[dict[str, Any]] = []
        dependencies: list[dict[str, str]] = []
        overridable: list[dict[str, Any]] = []
        for raw_node_id, node in graph.items():
            node_id = str(raw_node_id)
            class_type = str(node.get("class_type") or "Unknown")
            metadata = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
            label = str(
                metadata.get("title")
                or object_info.get(class_type, {}).get("display_name")
                or class_type
            )[:240]
            node_map[node_id] = label
            nodes.append({"id": node_id, "name": label, "class_type": class_type})
            node_inputs = node.get("inputs") or {}
            for input_name, value in node_inputs.items():
                if cls._is_link(value, graph):
                    dependencies.append({
                        "from": str(value[0]),
                        "to": node_id,
                        "input": str(input_name),
                    })
                    continue
                input_type, required, choices, options = cls._input_specification(
                    object_info,
                    class_type,
                    str(input_name),
                )
                current = value
                if isinstance(current, str) and len(current) > 1000:
                    current = current[:1000] + "…"
                entry: dict[str, Any] = {
                    "node_id": node_id,
                    "node_name": label,
                    "class_type": class_type,
                    "input": str(input_name),
                    "type": input_type,
                    "required": required,
                    "current": current,
                    "present": True,
                }
                if choices:
                    entry["choices"] = choices
                if options:
                    entry["options"] = options
                overridable.append(entry)
            defined_inputs = object_info.get(class_type, {}).get("input", {})
            for group_name in ("required", "optional"):
                group = defined_inputs.get(group_name, {})
                if not isinstance(group, dict):
                    continue
                for input_name in group:
                    if input_name in node_inputs:
                        continue
                    input_type, required, choices, options = cls._input_specification(
                        object_info,
                        class_type,
                        str(input_name),
                    )
                    if (
                        not choices
                        and input_type.upper()
                        not in {"INT", "FLOAT", "BOOLEAN", "STRING", "COMBO"}
                    ):
                        continue
                    entry = {
                        "node_id": node_id,
                        "node_name": label,
                        "class_type": class_type,
                        "input": str(input_name),
                        "type": input_type,
                        "required": required,
                        "current": None,
                        "present": False,
                    }
                    if choices:
                        entry["choices"] = choices
                    if options:
                        entry["options"] = options
                    overridable.append(entry)
        dependency_text = [
            (
                f"{edge['from']} ({node_map.get(edge['from'], 'Unknown')}) -> "
                f"{edge['to']} ({node_map.get(edge['to'], 'Unknown')}) "
                f"via {edge['input']}"
            )
            for edge in dependencies
        ]
        return {
            "node_count": len(nodes),
            "nodes": nodes,
            "node_map": node_map,
            "dependencies": dependencies,
            "dependency_text": dependency_text,
            "overridable_inputs": overridable,
        }

    @staticmethod
    def _validate_override_value(specification: dict[str, Any], value: Any) -> None:
        input_type = str(specification.get("type") or "ANY").upper()
        choices = specification.get("choices") or []
        label = f"{specification['node_id']}.{specification['input']}"
        if choices and value not in choices:
            preview = ", ".join(map(str, choices[:20]))
            raise ComfyUIError(f"Override {label} must be one of: {preview}")
        valid = True
        if input_type == "INT":
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif input_type == "FLOAT":
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif input_type == "BOOLEAN":
            valid = isinstance(value, bool)
        elif input_type == "STRING":
            valid = isinstance(value, str)
        if not valid:
            raise ComfyUIError(
                f"Override {label} expects {input_type}, got {type(value).__name__}"
            )
        options = specification.get("options") or {}
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = options.get("min")
            maximum = options.get("max")
            if isinstance(minimum, (int, float)) and value < minimum:
                raise ComfyUIError(f"Override {label} must be >= {minimum}")
            if isinstance(maximum, (int, float)) and value > maximum:
                raise ComfyUIError(f"Override {label} must be <= {maximum}")

    async def _litegraph_to_api(
        self,
        source: dict[str, Any],
        info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source = self._expand_subgraphs(source)
        nodes = source.get("nodes")
        if not isinstance(nodes, list):
            raise ComfyUIError("Workflow is neither API format nor ComfyUI LiteGraph format")
        if info is None:
            info = await self._get_json("/object_info", 20)
        link_sources: dict[Any, list[Any]] = {}
        for node in nodes:
            if node.get("mode", 0) == 4:
                continue
            for slot, output in enumerate(node.get("outputs", [])):
                for link_id in output.get("links") or []:
                    link_sources[link_id] = [str(node["id"]), slot]
        graph: dict[str, Any] = {}
        for node in nodes:
            if node.get("mode", 0) == 4:
                continue
            class_type = node.get("type")
            if class_type in {"Note", "MarkdownNote", "Reroute", "PrimitiveNode"}:
                continue
            if class_type not in info:
                raise ComfyUIError(f"Workflow requires unavailable node: {class_type}")
            linked = {
                item["name"]: link_sources[item["link"]]
                for item in node.get("inputs", [])
                if item.get("link") in link_sources
            }
            fixed = {
                item["name"]: item["_aether_value"]
                for item in node.get("inputs", [])
                if "_aether_value" in item
            }
            widget_values = list(node.get("widgets_values") or [])
            widget_index = 0
            named_widgets: dict[str, Any] = {}
            for input_item in node.get("inputs", []):
                if not input_item.get("widget") or widget_index >= len(widget_values):
                    continue
                named_widgets[input_item["name"]] = widget_values[widget_index]
                widget_index += 1
                if widget_index < len(widget_values) and widget_values[widget_index] in {
                    "fixed", "increment", "decrement", "randomize",
                }:
                    widget_index += 1
            inputs: dict[str, Any] = {}
            specification = info[class_type].get("input", {})
            for group in ("required", "optional"):
                for input_name, config in specification.get(group, {}).items():
                    if input_name in linked:
                        inputs[input_name] = linked[input_name]
                        continue
                    if input_name in fixed:
                        inputs[input_name] = fixed[input_name]
                        continue
                    if input_name in named_widgets:
                        inputs[input_name] = named_widgets[input_name]
                        continue
                    type_value = config[0] if isinstance(config, list) and config else config
                    is_widget = isinstance(type_value, list) or type_value in {
                        "INT", "FLOAT", "STRING", "BOOLEAN", "COMBO",
                    }
                    if is_widget and widget_index < len(widget_values):
                        inputs[input_name] = widget_values[widget_index]
                        widget_index += 1
                        if widget_index < len(widget_values) and widget_values[widget_index] in {
                            "fixed", "increment", "decrement", "randomize",
                        }:
                            widget_index += 1
            graph[str(node["id"])] = {"class_type": class_type, "inputs": inputs}
        return graph

    @staticmethod
    def _expand_subgraphs(source: dict[str, Any]) -> dict[str, Any]:
        source = ComfyUIService._rewire_bypassed_nodes(source)
        definitions = {
            item.get("id"): item
            for item in source.get("definitions", {}).get("subgraphs", [])
            if isinstance(item, dict) and item.get("id")
        }
        if not definitions:
            return source
        expanded = deepcopy(source)
        flat_nodes: list[dict[str, Any]] = []
        for instance in expanded.get("nodes", []):
            if instance.get("mode", 0) == 4:
                continue
            definition = definitions.get(instance.get("type"))
            if definition is None:
                flat_nodes.append(instance)
                continue
            instance_id = str(instance["id"])
            instance_inputs = {item.get("name"): item for item in instance.get("inputs", [])}
            widget_values = list(instance.get("widgets_values") or [])
            widget_index = 0
            incoming: dict[Any, tuple[str, Any]] = {}
            for definition_input in definition.get("inputs", []):
                name = definition_input.get("name")
                instance_input = instance_inputs.get(name, {})
                if instance_input.get("link") is not None:
                    replacement = ("link", instance_input["link"])
                elif instance_input.get("widget") and widget_index < len(widget_values):
                    replacement = ("value", widget_values[widget_index])
                    widget_index += 1
                    if widget_index < len(widget_values) and widget_values[widget_index] in {
                        "fixed", "increment", "decrement", "randomize",
                    }:
                        widget_index += 1
                else:
                    replacement = ("none", None)
                for link_id in definition_input.get("linkIds") or []:
                    incoming[link_id] = replacement

            outgoing: dict[Any, list[Any]] = {}
            instance_outputs = instance.get("outputs", [])
            for output_index, definition_output in enumerate(definition.get("outputs", [])):
                external_links = []
                if output_index < len(instance_outputs):
                    external_links = list(instance_outputs[output_index].get("links") or [])
                for link_id in definition_output.get("linkIds") or []:
                    outgoing[link_id] = external_links

            # ruff B023 误报：internal_link 在同一迭代内同步调用（下方 524），不逃逸循环。
            def internal_link(link_id: Any) -> str:
                return f"aether-subgraph-{instance_id}-{link_id}"  # noqa: B023

            for inner_source in definition.get("nodes", []):
                inner = deepcopy(inner_source)
                inner["id"] = f"{instance_id}:{inner_source['id']}"
                for input_item in inner.get("inputs", []):
                    link_id = input_item.get("link")
                    if link_id is None:
                        continue
                    if link_id in incoming:
                        replacement_type, replacement_value = incoming[link_id]
                        input_item["link"] = replacement_value if replacement_type == "link" else None
                        if replacement_type == "value":
                            input_item["_aether_value"] = replacement_value
                    else:
                        input_item["link"] = internal_link(link_id)
                for output_item in inner.get("outputs", []):
                    replaced_links = []
                    for link_id in output_item.get("links") or []:
                        if link_id in outgoing:
                            replaced_links.extend(outgoing[link_id])
                        else:
                            replaced_links.append(internal_link(link_id))
                    output_item["links"] = replaced_links
                flat_nodes.append(inner)
        expanded["nodes"] = flat_nodes
        expanded["definitions"] = {"subgraphs": []}
        return expanded

    @staticmethod
    def _rewire_bypassed_nodes(source: dict[str, Any]) -> dict[str, Any]:
        rewired = deepcopy(source)
        aliases: dict[Any, Any] = {}
        for node in rewired.get("nodes", []):
            if node.get("mode", 0) != 4:
                continue
            inputs = node.get("inputs", [])
            for output in node.get("outputs", []):
                passthrough = next(
                    (
                        item.get("link")
                        for item in inputs
                        if item.get("type") == output.get("type") and item.get("link") is not None
                    ),
                    None,
                )
                if passthrough is not None:
                    for output_link in output.get("links") or []:
                        aliases[output_link] = passthrough

        def resolved(link_id: Any) -> Any:
            visited = set()
            while link_id in aliases and link_id not in visited:
                visited.add(link_id)
                link_id = aliases[link_id]
            return link_id

        for node in rewired.get("nodes", []):
            for input_item in node.get("inputs", []):
                if input_item.get("link") is not None:
                    input_item["link"] = resolved(input_item["link"])
        return rewired

    @classmethod
    def _format_node_error(cls, payload: Any, graph: dict[str, Any]) -> str:
        descriptor = cls._describe_graph(graph)
        node_map = descriptor["node_map"]

        def annotate(value: Any) -> Any:
            if isinstance(value, dict):
                result = {str(key): annotate(child) for key, child in value.items()}
                node_id = value.get("node_id")
                if node_id is not None:
                    normalized = str(node_id)
                    result["node_label"] = node_map.get(normalized, "Unknown")
                return result
            if isinstance(value, list):
                return [annotate(child) for child in value]
            return value

        mapping = ", ".join(f"{node_id}={name}" for node_id, name in node_map.items())
        details = json.dumps(annotate(payload), ensure_ascii=False, default=str)[:4000]
        return f"{details}. Node map: {mapping}"

    @classmethod
    def _generation_metadata(
        cls,
        *,
        prompt_id: str,
        graph: dict[str, Any],
        metadata: dict[str, Any] | None,
        files: list[dict[str, Any]],
        submitted_at: str,
    ) -> dict[str, Any]:
        supplied = deepcopy(metadata or {})
        workflow = supplied.pop("workflow", None) or cls._describe_graph(graph)
        models: list[str] = []
        seeds: list[int] = []
        dimensions: list[dict[str, int]] = []
        prompts: list[dict[str, str]] = []
        node_map = workflow.get("node_map") if isinstance(workflow, dict) else {}
        for raw_node_id, node in graph.items():
            node_id = str(raw_node_id)
            node_metadata = (
                node.get("_meta")
                if isinstance(node.get("_meta"), dict)
                else {}
            )
            inputs = node.get("inputs") or {}
            for key, value in inputs.items():
                if key in {
                    "ckpt_name", "unet_name", "model_name", "lora_name",
                    "vae_name", "clip_name",
                } and isinstance(value, str) and value not in models:
                    models.append(value)
                if (
                    key in {"seed", "noise_seed"}
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                    and value not in seeds
                ):
                    seeds.append(value)
                normalized_key = str(key).casefold()
                if (
                    isinstance(value, str)
                    and (
                        "prompt" in normalized_key
                        or normalized_key in {"text", "positive", "negative"}
                    )
                ):
                    prompts.append({
                        "node_id": node_id,
                        "node_name": str(
                            (node_map or {}).get(node_id)
                            or node_metadata.get("title")
                            or node.get("class_type")
                            or "Unknown"
                        ),
                        "input": str(key),
                        "value": value[:20_000],
                    })
            width, height = inputs.get("width"), inputs.get("height")
            if isinstance(width, int) and isinstance(height, int):
                item = {"width": width, "height": height}
                if item not in dimensions:
                    dimensions.append(item)
        clean_files = [
            {
                key: value
                for key, value in file.items()
                if key != "image_data_url" and not str(key).startswith("_")
            }
            for file in files
        ]
        return {
            "schema": "aetherchat.agent-artifact/v1",
            "schema_version": "1.0",
            "artifact_type": "comfyui_generation",
            "engine": "ComfyUI",
            "operation": supplied.pop("operation", "run_workflow"),
            "prompt_id": prompt_id,
            "submitted_at": submitted_at,
            "completed_at": datetime.now(UTC).isoformat(),
            "parameters": supplied.pop("parameters", {}),
            "workflow_name": supplied.pop("workflow_name", None),
            "workflow": workflow,
            "provenance": {
                "models": models,
                "seeds": seeds,
                "dimensions": dimensions,
                "prompts": prompts,
            },
            "outputs": clean_files,
            **supplied,
        }

    async def _submit_and_wait(
        self,
        graph: dict[str, Any],
        timeout: int,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self.ensure_ready()
        submitted_at = datetime.now(UTC).isoformat()
        payload = {"prompt": graph, "client_id": str(uuid4())}
        try:
            async with self._client(60) as client:
                response = await client.post(f"{self.base_url}/prompt", json=payload)
                if response.status_code >= 400:
                    try:
                        rejection: Any = response.json()
                    except ValueError:
                        rejection = response.text[:1000]
                    raise ComfyUIError(
                        "ComfyUI rejected workflow: "
                        + self._format_node_error(rejection, graph)
                    )
                queued = response.json()
        except httpx.HTTPError as exc:
            raise ComfyUIError(f"Unable to queue ComfyUI workflow: {exc}") from exc
        if queued.get("node_errors"):
            raise ComfyUIError(
                "ComfyUI node validation failed: "
                + self._format_node_error(queued["node_errors"], graph)
            )
        prompt_id = queued.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"ComfyUI did not return a prompt id: {queued}")
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(1)
            history = await self._get_json(f"/history/{prompt_id}", 30)
            if prompt_id not in history:
                continue
            entry = history[prompt_id]
            if entry.get("status", {}).get("status_str") == "error":
                raise ComfyUIError(
                    "ComfyUI execution failed: "
                    + self._format_node_error(entry.get("status"), graph)
                )
            files = []
            for output in entry.get("outputs", {}).values():
                for kind in ("images", "gifs", "videos", "audio"):
                    for item in output.get(kind, []):
                        if isinstance(item, dict) and item.get("filename"):
                            suffix = Path(str(item["filename"])).suffix.lower()
                            normalized_kind = kind
                            if suffix in {".mp4", ".webm", ".mov", ".mkv", ".avi"}:
                                normalized_kind = "videos"
                            elif suffix in {".mp3", ".wav", ".flac", ".ogg", ".opus"}:
                                normalized_kind = "audio"
                            elif suffix == ".gif":
                                normalized_kind = "gifs"
                            files.append({**item, "kind": normalized_kind})
            if files:
                result = await self._result(prompt_id, files)
                result["generation_metadata"] = self._generation_metadata(
                    prompt_id=prompt_id,
                    graph=graph,
                    metadata=metadata,
                    files=result["files"],
                    submitted_at=submitted_at,
                )
                return result
            if entry.get("status", {}).get("completed"):
                result = {"prompt_id": prompt_id, "completed": True, "files": []}
                result["generation_metadata"] = self._generation_metadata(
                    prompt_id=prompt_id,
                    graph=graph,
                    metadata=metadata,
                    files=[],
                    submitted_at=submitted_at,
                )
                return result
        raise ComfyUIError(f"ComfyUI generation timed out after {timeout} seconds (prompt_id={prompt_id})")

    async def _result(self, prompt_id: str, files: list[dict[str, Any]]) -> dict[str, Any]:
        result_files: list[dict[str, Any]] = []
        image_data_urls: list[str] = []
        async with self._client(180) as client:
            for item in files:
                query = (
                    f"filename={quote(str(item['filename']))}&subfolder={quote(str(item.get('subfolder', '')))}"
                    f"&type={quote(str(item.get('type', 'output')))}"
                )
                url = f"{self.base_url}/view?{query}"
                record = {"filename": item["filename"], "kind": item["kind"], "url": url}
                local_path = self._local_output_path(item)
                if local_path is not None:
                    record["_local_path"] = str(local_path)
                suffix = Path(str(item["filename"])).suffix.lower()
                if item["kind"] == "images" and suffix in {".png", ".jpg", ".jpeg", ".webp"} and len(image_data_urls) < 4:
                    response = await client.get(url)
                    response.raise_for_status()
                    mime = response.headers.get("content-type", "image/png").split(";")[0]
                    data_url = f"data:{mime};base64,{base64.b64encode(response.content).decode('ascii')}"
                    image_data_urls.append(data_url)
                    record["image_data_url"] = data_url
                result_files.append(record)
        return {"prompt_id": prompt_id, "completed": True, "files": result_files, "images": image_data_urls}

    def _local_output_path(self, item: dict[str, Any]) -> Path | None:
        if str(item.get("type") or "output") != "output":
            return None
        filename = str(item.get("filename") or "").replace("\\", "/")
        subfolder = str(item.get("subfolder") or "").replace("\\", "/")
        raw_parts = [
            part
            for value in (subfolder, filename)
            for part in value.split("/")
            if part not in {"", "."}
        ]
        if (
            not raw_parts
            or any(part == ".." or ":" in part or "\x00" in part for part in raw_parts)
        ):
            return None
        for root in self.output_roots:
            candidate = (root / Path(*raw_parts)).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                continue
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
        return None


comfyui_service = ComfyUIService()
