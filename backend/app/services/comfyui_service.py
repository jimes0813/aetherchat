from __future__ import annotations

import asyncio
import base64
import json
import os
import random
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx


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
        stats = await self._get_json("/system_stats", 5)
        inventory = await self.inventory()
        return {
            "ready": True,
            "url": self.base_url,
            "system": stats.get("system", {}),
            "devices": stats.get("devices", []),
            "inventory": inventory,
        }

    async def inventory(self) -> dict[str, Any]:
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
        workflows_by_name: dict[str, dict[str, str]] = {}
        for root in self.workflow_roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*.json")):
                if path.is_file() and os.access(path, os.R_OK):
                    workflows_by_name.setdefault(path.stem, {"name": path.stem, "path": str(path)})
        workflows = list(workflows_by_name.values())
        return {
            "checkpoints": checkpoints,
            "diffusion_models": diffusion_models,
            "loras": loras,
            "vae": choices("VAELoader", "vae_name"),
            "text_encoders": choices("CLIPLoader", "clip_name"),
            "clip_vision": choices("CLIPVisionLoader", "clip_name"),
            "upscalers": choices("UpscaleModelLoader", "model_name"),
            "workflows": workflows,
            "counts": {
                "checkpoints": len(checkpoints),
                "diffusion_models": len(diffusion_models),
                "loras": len(loras),
                "workflows": len(workflows),
            },
        }

    @staticmethod
    def _dimensions(width: int, height: int) -> tuple[int, int]:
        if not 256 <= width <= 2048 or not 256 <= height <= 2048:
            raise ComfyUIError("Width and height must be between 256 and 2048")
        return width - width % 8, height - height % 8

    async def generate_image(
        self,
        prompt: str,
        *,
        model: str = "RealVisXL_V5.0_Lightning_fp16.safetensors",
        negative_prompt: str = "blurry, low quality, distorted, watermark, text, bad anatomy",
        width: int = 768,
        height: int = 768,
        steps: int = 6,
        cfg: float = 2.0,
        seed: int | None = None,
        lora: str | None = None,
        lora_strength: float = 1.0,
    ) -> dict[str, Any]:
        width, height = self._dimensions(width, height)
        inventory = await self.inventory()
        if model in inventory["checkpoints"]:
            graph = self._checkpoint_image_graph(
                prompt, negative_prompt, model, width, height, steps, cfg, seed, lora, lora_strength
            )
        elif model in inventory["diffusion_models"] and "qwen_image" in model.lower():
            graph = self._qwen_image_graph(
                prompt, negative_prompt, model, width, height, steps, cfg, seed, lora, lora_strength
            )
        else:
            raise ComfyUIError(f"Unsupported image model: {model}")
        return await self._submit_and_wait(graph, timeout=1800)

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
        input_image: str | None = None,
        width: int = 480,
        height: int = 832,
        frames: int = 17,
        fps: int = 16,
        steps: int = 8,
        cfg: float = 4.0,
        seed: int | None = None,
    ) -> dict[str, Any]:
        width, height = self._dimensions(width, height)
        frames = max(1, min(161, frames))
        if pipeline == "wan-i2v":
            if not input_image:
                raise ComfyUIError("wan-i2v requires input_image")
            image_name = await self._upload_image(input_image)
            graph = self._wan_i2v_graph(prompt, image_name, width, height, frames, fps, steps, cfg, seed)
        elif pipeline == "ltx-t2v":
            width, height = width - width % 32, height - height % 32
            graph = self._ltx_t2v_graph(prompt, width, height, frames, fps, steps, cfg, seed)
        else:
            raise ComfyUIError("pipeline must be wan-i2v or ltx-t2v")
        return await self._submit_and_wait(graph, timeout=3600)

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

    @staticmethod
    def _wan_i2v_graph(
        prompt: str, image_name: str, width: int, height: int, frames: int,
        fps: int, steps: int, cfg: float, seed: int | None,
    ) -> dict[str, Any]:
        negative = "blurry, low quality, distorted, static, frozen, watermark, text, bad anatomy"
        return {
            "1": {"class_type": "UNETLoader", "inputs": {
                "unet_name": "wan2.1_i2v_480p_14B_fp8_scaled.safetensors", "weight_dtype": "fp8_e4m3fn",
            }},
            "2": {"class_type": "CLIPLoader", "inputs": {
                "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan",
            }},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
            "4": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "clip_vision_h.safetensors"}},
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
    def _ltx_t2v_graph(
        prompt: str, width: int, height: int, frames: int, fps: int,
        steps: int, cfg: float, seed: int | None,
    ) -> dict[str, Any]:
        negative = "worst quality, blurry, distorted, jittery, flickering"
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "ltx-2.3-22b-dev.safetensors"}},
            "2": {"class_type": "LTXVGemmaCLIPModelLoader", "inputs": {
                "gemma_path": "gemma-3-12b-it/model-00001-of-00005.safetensors",
                "ltxv_path": "ltx-2.3-22b-dev.safetensors", "max_length": 1024,
            }},
            "3": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": "ltx-2.3-22b-dev.safetensors"}},
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

    async def run_workflow(self, name: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        path = self._workflow_path(name)
        try:
            source = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ComfyUIError(f"Unable to read workflow: {exc}") from exc
        graph = source if self._is_api_graph(source) else await self._litegraph_to_api(source)
        for node_id, values in (overrides or {}).items():
            if str(node_id) not in graph or not isinstance(values, dict):
                raise ComfyUIError(f"Invalid workflow override node: {node_id}")
            graph[str(node_id)]["inputs"].update(values)
        return await self._submit_and_wait(graph, timeout=3600)

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

    async def _litegraph_to_api(self, source: dict[str, Any]) -> dict[str, Any]:
        source = self._expand_subgraphs(source)
        nodes = source.get("nodes")
        if not isinstance(nodes, list):
            raise ComfyUIError("Workflow is neither API format nor ComfyUI LiteGraph format")
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

    async def _submit_and_wait(self, graph: dict[str, Any], timeout: int) -> dict[str, Any]:
        payload = {"prompt": graph, "client_id": str(uuid4())}
        try:
            async with self._client(60) as client:
                response = await client.post(f"{self.base_url}/prompt", json=payload)
                if response.status_code >= 400:
                    raise ComfyUIError(f"ComfyUI rejected workflow: {response.text[:1000]}")
                queued = response.json()
        except httpx.HTTPError as exc:
            raise ComfyUIError(f"Unable to queue ComfyUI workflow: {exc}") from exc
        if queued.get("node_errors"):
            raise ComfyUIError(f"ComfyUI node validation failed: {json.dumps(queued['node_errors'], ensure_ascii=False)[:1000]}")
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
                raise ComfyUIError(f"ComfyUI execution failed: {json.dumps(entry.get('status'), ensure_ascii=False)[:1200]}")
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
                return await self._result(prompt_id, files)
            if entry.get("status", {}).get("completed"):
                return {"prompt_id": prompt_id, "completed": True, "files": []}
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


comfyui_service = ComfyUIService()
