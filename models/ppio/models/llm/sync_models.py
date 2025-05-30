#!/usr/bin/env python3
import os
from typing import Any, Dict

import requests
import yaml


def load_yaml_file(file_path: str) -> Dict[str, Any]:
    """Load a YAML file and return its contents as a dictionary."""
    try:
        with open(file_path) as f:
            data = yaml.safe_load(f)
            if not isinstance(data, dict):
                print(f"⚠️ Warning: {file_path} does not contain a valid YAML dictionary")
                return {}
            return data
    except Exception as e:
        print(f"⚠️ Error loading {file_path}: {str(e)}")
        return {}


def save_yaml_file(file_path: str, data: Dict[str, Any]):
    """Save dictionary as YAML file."""
    class IndentDumper(yaml.Dumper):
        def increase_indent(self, flow=False, indentless=False):
            return super().increase_indent(flow, False)

    with open(file_path, "w") as f:
        yaml.dump(data, f, sort_keys=False, allow_unicode=True, Dumper=IndentDumper)


def get_api_data() -> Dict[str, Any]:
    """Fetch data from the PPIO API."""
    url = "https://api.ppinfra.com/v3/openai/models"
    response = requests.get(url)
    return response.json()


def convert_price(price_per_m: int) -> str:
    """Convert price per million tokens to the format used in YAML files.

    API returns price per million tokens, we need to convert to the YAML format:
    1. API price is per million tokens (e.g., 8900 means $8.9 per million tokens)
    2. YAML uses unit of 0.0001, so we need to:
       a. First convert API price to per-token price: 8900/1000000 = 0.0089 per token
       b. Then express it in units of 0.0001: 0.0089/0.0001 = 89
       c. Finally format it as a string with proper decimal places
    """
    per_token_price = price_per_m / 1000000  # Convert to price per token
    return f"{per_token_price:.6f}".rstrip("0").rstrip(".")


def create_yaml_template(model_id: str, api_model: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new YAML template for a model."""
    return {
        "model": model_id,
        "label": {
            "zh_Hans": api_model.get("display_name", "").strip() or api_model["id"],
            "en_US": api_model.get("display_name", "").strip() or api_model["id"]
        },
        "model_type": "llm",
        "features": ["agent-thought"],
        "model_properties": {"mode": "chat", "context_size": api_model["context_size"]},
        "parameter_rules": [
            {"name": "temperature", "use_template": "temperature", "min": 0, "max": 2, "default": 1},
            {"name": "top_p", "use_template": "top_p", "min": 0, "max": 1, "default": 1},
            {"name": "max_tokens", "use_template": "max_tokens", "min": 1, "max": 2048, "default": 512},
            {"name": "frequency_penalty", "use_template": "frequency_penalty", "min": -2, "max": 2, "default": 0},
            {"name": "presence_penalty", "use_template": "presence_penalty", "min": -2, "max": 2, "default": 0},
        ],
        "pricing": {
            "input": convert_price(api_model["input_token_price_per_m"]),
            "output": convert_price(api_model["output_token_price_per_m"]),
            "unit": "0.0001",
            "currency": "CNY",
        },
    }

def create_position_yaml(yaml_dir: str, api_models: list[Dict[str, Any]]):
    """Create _position.yaml file with model IDs in the order they appear in API response."""
    position_file = os.path.join(yaml_dir, "_position.yaml")
    model_ids = [model["id"] for model in api_models]
    
    # Group models by their provider/family
    model_groups = {}
    for model_id in model_ids:
        provider = model_id.split("/")[0]
        if provider not in model_groups:
            model_groups[provider] = []
        model_groups[provider].append(model_id)
    
    # Create content with comments for each group
    content = []
    for provider, models in model_groups.items():
        content.append(f"# {provider.title()} Models")
        content.extend([f"- {model_id}" for model_id in models])
        content.append("")  # Add empty line between groups
    
    # Write to file
    with open(position_file, "w") as f:
        f.write("\n".join(content).rstrip() + "\n")


def sync_yaml_files(yaml_dir: str):
    """Sync YAML files with API data - update existing files and create new ones."""
    # Get API data
    api_data = get_api_data()
    api_models = {model["id"]: model for model in api_data["data"]}

    # Create _position.yaml file
    create_position_yaml(yaml_dir, api_data["data"])

    # Track existing YAML files
    existing_files = {}
    for filename in os.listdir(yaml_dir):
        if not filename.endswith(".yaml") or filename in ["check_yaml_consistency.py", "fix_yaml_files.py"]:
            continue

        file_path = os.path.join(yaml_dir, filename)
        print(f"Loading {file_path}")
        yaml_data = load_yaml_file(file_path)
        if not yaml_data:
            continue

        model_id = yaml_data.get("model")
        if model_id:
            existing_files[model_id] = filename

    # Process each API model
    for model_id, api_model in api_models.items():
        if model_id in existing_files:
            # Update existing file
            filename = existing_files[model_id]
            file_path = os.path.join(yaml_dir, filename)
            yaml_data = load_yaml_file(file_path)
            changes = []

            # Update context size
            yaml_context_size = yaml_data.get("model_properties", {}).get("context_size")
            api_context_size = api_model.get("context_size")
            if yaml_context_size != api_context_size:
                yaml_data["model_properties"]["context_size"] = api_context_size
                changes.append(f"context_size: {yaml_context_size} -> {api_context_size}")

            # Update pricing
            api_input_price = convert_price(api_model.get("input_token_price_per_m", 0))
            api_output_price = convert_price(api_model.get("output_token_price_per_m", 0))

            if yaml_data["pricing"]["input"] != api_input_price:
                old_price = yaml_data["pricing"]["input"]
                yaml_data["pricing"]["input"] = api_input_price
                changes.append(f"input_price: {old_price} -> {api_input_price}")

            if yaml_data["pricing"]["output"] != api_output_price:
                old_price = yaml_data["pricing"]["output"]
                yaml_data["pricing"]["output"] = api_output_price
                changes.append(f"output_price: {old_price} -> {api_output_price}")

            # Update labels
            api_title = api_model.get("display_name", "").strip() or api_model.get("id")
            if yaml_data["label"]["zh_Hans"] != api_title or yaml_data["label"]["en_US"] != api_title:
                old_labels = f"zh_Hans: {yaml_data['label']['zh_Hans']}, en_US: {yaml_data['label']['en_US']}"
                yaml_data["label"]["zh_Hans"] = api_title
                yaml_data["label"]["en_US"] = api_title
                changes.append(f"labels: {old_labels} -> {api_title}")

            # Save changes
            if changes:
                save_yaml_file(file_path, yaml_data)
                print(f"✏️ Updated {filename}:")
                for change in changes:
                    print(f"  - {change}")
            else:
                print(f"✅ No changes needed for {filename}")
        else:
            # Create new file
            new_filename = "-".join(model_id.split("/")[1:]) + ".yaml"
            new_file_path = os.path.join(yaml_dir, new_filename)

            # Create new YAML data
            new_yaml_data = create_yaml_template(model_id, api_model)

            # Save new file
            save_yaml_file(new_file_path, new_yaml_data)
            print(f"➕ Created new file {new_filename}")

    # Delete files for models that no longer exist in API
    for model_id, filename in existing_files.items():
        if model_id not in api_models:
            file_path = os.path.join(yaml_dir, filename)
            os.remove(file_path)
            print(f"🗑️ Deleted {filename} (model no longer exists in API)")


if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    sync_yaml_files(current_dir)