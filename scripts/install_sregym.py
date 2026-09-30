"""Install/update Blackboard and enable a configurable host results directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import textwrap
from pathlib import Path

import yaml

ORIGINAL_RESULTS = 'base_dir = Path("results") / get_current_datetime_formatted()'
CONFIGURED_RESULTS = 'base_dir = Path(os.environ.get("SREGYM_RESULTS_DIR", "results")) / get_current_datetime_formatted()'
ORIGINAL_DOCKER_BIND = '''def get_container_host_bind_address() -> str:
    """Return a host bind address reachable from the agent container."""
    if platform.system() != "Linux":'''
CONFIGURED_DOCKER_BIND = '''def get_container_host_bind_address() -> str:
    """Return a host bind address reachable from the agent container."""
    if _docker_uses_separate_host():'''
ORIGINAL_CUSTOM_PROVIDER = '''    raise ValueError(
        f"Filtered internet access does not know the model provider for agent '{policy.agent_name}'. "
        "Use a supported agent or run with --internet-access open."
    )'''
CONFIGURED_CUSTOM_PROVIDER = '''    custom_base = _configured_url(environment, "AGENT_API_BASE")
    if custom_base:
        return (EndpointRule.host_from_url(custom_base),)
    return _provider_rules(_provider_from_model(model), environment)'''


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(checkout: Path):
    source = Path(__file__).resolve().parents[1]
    checkout = checkout.resolve()
    if not (checkout / "sregym/agent_registry.py").is_file():
        raise ValueError("Expected an SREGym checkout")
    registry = checkout / "agents.yaml"
    data = yaml.safe_load(registry.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("agents"), list):
        raise ValueError("Unexpected SREGym agents.yaml format")
    entry = yaml.safe_load((source / "agents.yaml").read_text())["agents"][0]
    existing = next((a for a in data["agents"] if a["name"] == entry["name"]), None)
    if existing is not None and existing != entry:
        raise ValueError("A different graphstate registration exists; review it before replacing")
    main = checkout / "main.py"
    main_text = main.read_text()
    if ORIGINAL_RESULTS not in main_text and CONFIGURED_RESULTS not in main_text:
        raise ValueError("Upstream results-directory code changed; review compatibility before installing")
    if main_text.count(ORIGINAL_RESULTS) > 1:
        raise ValueError("Ambiguous upstream results-directory code")
    container_runner = checkout / "sregym/service/container_runner.py"
    container_runner_text = container_runner.read_text()
    if ORIGINAL_DOCKER_BIND not in container_runner_text and CONFIGURED_DOCKER_BIND not in container_runner_text:
        raise ValueError("Upstream Docker host-address code changed; review compatibility before installing")
    if container_runner_text.count(ORIGINAL_DOCKER_BIND) > 1:
        raise ValueError("Ambiguous upstream Docker host-address code")
    provider_endpoints = checkout / "sregym/service/provider_endpoints.py"
    provider_text = provider_endpoints.read_text()
    if ORIGINAL_CUSTOM_PROVIDER not in provider_text and CONFIGURED_CUSTOM_PROVIDER not in provider_text:
        raise ValueError("Upstream custom-agent provider code changed; review compatibility before installing")
    if provider_text.count(ORIGINAL_CUSTOM_PROVIDER) > 1:
        raise ValueError("Ambiguous upstream custom-agent provider code")
    destination = checkout / "clients/graphstate"
    manifest_path = checkout / ".graphstate-install.json"
    previous = json.loads(manifest_path.read_text()).get("files", {}) if manifest_path.exists() else {}
    if destination.exists():
        for path in destination.rglob("*.py"):
            relative = str(path.relative_to(destination))
            original = source / "clients/graphstate" / relative
            unchanged_previous = previous.get(relative) == digest(path)
            identical_source = original.exists() and path.read_bytes() == original.read_bytes()
            if not unchanged_previous and not identical_source:
                raise ValueError(f"Destination has untracked edits: {path}. Review before reinstalling.")
    shutil.copytree(source / "clients/graphstate", destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.Identifier"))
    for relative in previous:
        path = destination / relative
        if not (source / "clients/graphstate" / relative).exists() and path.is_file():
            path.unlink()
    if existing is None:
        data["agents"].append(entry)
        original_text = registry.read_text()
        if set(data) == {"agents"} and len(data["agents"]) > 1:
            node = yaml.compose(original_text).value[0][1]
            addition = textwrap.indent(yaml.safe_dump([entry], sort_keys=False), " " * node.start_mark.column)
            registry.write_text(original_text.rstrip() + "\n" + addition)
        else:
            registry.write_text(yaml.safe_dump(data, sort_keys=False))
    if ORIGINAL_RESULTS in main_text:
        main.write_text(main_text.replace(ORIGINAL_RESULTS, CONFIGURED_RESULTS))
    if ORIGINAL_DOCKER_BIND in container_runner_text:
        container_runner.write_text(container_runner_text.replace(ORIGINAL_DOCKER_BIND, CONFIGURED_DOCKER_BIND))
    if ORIGINAL_CUSTOM_PROVIDER in provider_text:
        provider_endpoints.write_text(provider_text.replace(ORIGINAL_CUSTOM_PROVIDER, CONFIGURED_CUSTOM_PROVIDER))
    manifest_path.write_text(json.dumps({"files": {
        str(path.relative_to(destination)): digest(path) for path in destination.rglob("*.py")
    }}, indent=2))
    print(f"Installed blackboard-sre in {checkout}; rebuild the agent image before live runs.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    install(parser.parse_args().checkout)
