#!/usr/bin/env python3
"""Validate the model route for a closed network; never claims runtime isolation.

Static validation does not prove model availability or DNS/network isolation.
Docker internal networking in compose.offline.yml provides a separate boundary.
"""
import argparse
import ipaddress
import json
from pathlib import Path
from urllib.parse import urlsplit
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_env(path):
    values = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def local_url(value):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return False
        if host in {"localhost", "local-model", "host.docker.internal"}:
            return True
        address = ipaddress.ip_address(host)
        return address.is_loopback or (address.is_private and not address.is_unspecified and not address.is_link_local and not address.is_multicast)
    except ValueError:
        return False


def validate(config, env):
    errors, aliases = [], set()
    if str(env.get("OFFLINE_MODE", "0")) != "1":
        errors.append("OFFLINE_MODE must be 1")
    def resolved(value):
        if isinstance(value, str) and value.startswith("os.environ/"):
            return env.get(value.split("/", 1)[1], "")
        return value
    routes = config.get("model_list", [])
    if not routes:
        errors.append("No model routes configured")
    for route in routes:
        alias = route.get("model_name", "")
        aliases.add(alias)
        params = route.get("litellm_params", {})
        if not local_url(resolved(params.get("api_base", ""))):
            errors.append("Non-local or missing upstream for alias: " + alias)
        model = resolved(params.get("model", ""))
        if not isinstance(model, str) or not model.startswith("openai/") or model == "openai/REPLACE_WITH_SERVED_MODEL":
            errors.append("Set a local OpenAI-compatible model ID for alias: " + alias)
    # Do not silently permit another provider through fallbacks/router overrides.
    if any(config.get(key) for key in ("router_settings", "fallbacks", "default_fallbacks")):
        errors.append("Router/fallback configuration requires an explicit offline review")
    for role in ("LEGAL", "FINANCE", "GENERAL"):
        if env.get(role + "_LLM_MODEL", "legal-chat") not in aliases:
            errors.append("Selected model is missing for role: " + role)
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--config", type=Path, default=ROOT / "config/litellm/offline.yaml")
    args = parser.parse_args()
    errors = validate(yaml.safe_load(args.config.read_text()), load_env(args.env_file))
    print(json.dumps({"static_configuration_ok": not errors, "errors": errors,
                      "runtime_verified": False,
                      "next_check": "Verify the model is reachable on the internal Docker network and run Matrix/Flowise questions without external connectivity."}, ensure_ascii=False, indent=2))
    raise SystemExit(bool(errors))


if __name__ == "__main__":
    main()
