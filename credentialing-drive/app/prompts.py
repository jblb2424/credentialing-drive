"""Load documented, version-controlled prompt templates for the AI layer."""

from functools import lru_cache
from pathlib import Path
import re

import yaml


PROMPT_DIRECTORY = Path(__file__).with_name("prompt_config")
PROMPT_FILES = {
    "ingestion": "ingestion.yaml",
    "validation": "validation.yaml",
}
PLACEHOLDER_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")


@lru_cache(maxsize=None)
def load_prompt_config(prompt_group):
    """Return one validated prompt configuration document."""
    try:
        file_name = PROMPT_FILES[prompt_group]
    except KeyError as exc:
        raise ValueError(f"Unknown prompt group: {prompt_group}") from exc

    with (PROMPT_DIRECTORY / file_name).open(encoding="utf-8") as source:
        config = yaml.safe_load(source)

    if not isinstance(config, dict) or not isinstance(config.get("description"), str):
        raise ValueError(f"Prompt configuration {file_name} requires a description")
    if not isinstance(config.get("prompts"), dict):
        raise ValueError(f"Prompt configuration {file_name} requires a prompts mapping")

    for prompt_name, prompt_config in config["prompts"].items():
        if not isinstance(prompt_config, dict):
            raise ValueError(f"Prompt {prompt_name} in {file_name} must be a mapping")
        if not isinstance(prompt_config.get("description"), str) or not prompt_config["description"].strip():
            raise ValueError(f"Prompt {prompt_name} in {file_name} requires a description")
        if not isinstance(prompt_config.get("prompt"), str) or not prompt_config["prompt"].strip():
            raise ValueError(f"Prompt {prompt_name} in {file_name} requires prompt text")
    return config


def prompt_template(prompt_group, prompt_name):
    """Return a documented prompt template without runtime data appended to it."""
    prompts = load_prompt_config(prompt_group)["prompts"]
    try:
        return prompts[prompt_name]["prompt"]
    except KeyError as exc:
        raise ValueError(f"Unknown {prompt_group} prompt: {prompt_name}") from exc


def render_prompt(prompt_group, prompt_name, **variables):
    """Render only explicit ``{{ variable }}`` placeholders in a prompt template."""
    template = prompt_template(prompt_group, prompt_name)
    placeholders = set(PLACEHOLDER_PATTERN.findall(template))
    missing = placeholders - variables.keys()
    if missing:
        raise ValueError(
            f"Prompt {prompt_group}/{prompt_name} is missing variables: {', '.join(sorted(missing))}"
        )

    return PLACEHOLDER_PATTERN.sub(lambda match: str(variables[match.group(1)]), template)
