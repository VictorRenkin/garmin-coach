from __future__ import annotations

import json
import re
import time
from typing import Any, Literal, TypeVar

from openai import OpenAI
from pydantic import BaseModel

from .config import Settings
from .ai_usage import record_usage


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


def routed_openai_model(
    settings: Settings, quality: Literal["economy", "balanced", "deep"] = "deep"
) -> str:
    """Route les tÃ¢ches courantes vers un modÃ¨le moins coÃ»teux de la mÃªme famille."""
    configured = settings.ai_model
    if (
        settings.ai_provider != "openai"
        or settings.ai_base_url
        or not configured.startswith("gpt-5.6")
    ):
        return configured
    if quality == "economy":
        return "gpt-5.6-luna"
    if quality == "balanced":
        return "gpt-5.6-terra"
    return configured


def _json_from_text(text: str) -> Any:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("L'IA n'a pas renvoyé de JSON exploitable.")
        return json.loads(cleaned[start : end + 1])


def structured_completion(
    settings: Settings,
    *,
    instructions: str,
    payload: Any,
    result_type: type[StructuredModel],
    reasoning_effort: str = "none",
    category: str = "coach",
    max_output_tokens: int = 700,
    quality: Literal["economy", "balanced", "deep"] = "deep",
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> StructuredModel:
    """Appelle OpenAI Responses ou toute API compatible Chat Completions."""
    if not settings.ai_api_key:
        raise RuntimeError("Aucune clé API d'IA n'est configurée.")

    if settings.ai_provider == "openai" and not settings.ai_base_url:
        model = routed_openai_model(settings, quality)
        # Keep interactive coaching bounded: the SDK default can otherwise
        # retry a slow request for several minutes while the dashboard waits.
        encoded_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        client = OpenAI(
            api_key=settings.ai_api_key,
            timeout=timeout_seconds if timeout_seconds is not None else 60.0,
            max_retries=max_retries if max_retries is not None else 0,
        )
        started = time.perf_counter()
        response = client.responses.parse(
            model=model,
            reasoning={"effort": reasoning_effort},
            instructions=instructions,
            input=encoded_payload,
            text_format=result_type,
            max_output_tokens=max(64, max_output_tokens),
        )
        record_usage(
            response,
            category,
            model,
            duration_seconds=round(time.perf_counter() - started, 6),
            payload_bytes=len(encoded_payload.encode("utf-8")),
            cache_status="miss",
        )
        if response.output_parsed is None:
            raise RuntimeError("L'IA n'a pas produit de réponse structurée.")
        return response.output_parsed

    client = OpenAI(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        timeout=timeout_seconds if timeout_seconds is not None else 300.0,
        max_retries=max_retries if max_retries is not None else 1,
    )
    schema = json.dumps(result_type.model_json_schema(), ensure_ascii=False)
    user_content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    started = time.perf_counter()
    completion = client.chat.completions.create(
        model=settings.ai_model,
        messages=[
            {
                "role": "system",
                "content": instructions
                + "\n\nRéponds uniquement avec un objet JSON valide conforme à ce schéma :\n"
                + schema,
            },
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
        max_tokens=1800,
    )
    record_usage(
        completion,
        category,
        settings.ai_model,
        duration_seconds=round(time.perf_counter() - started, 6),
        payload_bytes=len(user_content.encode("utf-8")),
        cache_status="miss",
    )
    content = completion.choices[0].message.content or ""
    return result_type.model_validate(_json_from_text(content))
