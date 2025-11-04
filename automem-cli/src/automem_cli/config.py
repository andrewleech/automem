"""Configuration management for AutoMem CLI"""

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel, HttpUrl, field_validator


class Config(BaseModel):
    """AutoMem CLI configuration"""

    endpoint: str = "http://localhost:8001"
    api_token: Optional[str] = None
    project_id: str = "default"
    auto_consolidate: bool = True
    session_tracking: bool = True

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        """Validate endpoint is a valid HTTP/HTTPS URL without trailing slash"""
        # Validate URL format to prevent injection attacks
        from pydantic import TypeAdapter
        url_validator = TypeAdapter(HttpUrl)
        try:
            url_validator.validate_python(v)
        except Exception as e:
            raise ValueError(f"Invalid endpoint URL: {e}")
        return v.rstrip("/")

    @staticmethod
    def _expand_env_vars(data: Dict[str, Any]) -> Dict[str, Any]:
        """Expand environment variable placeholders like ${VAR_NAME} in config values

        Args:
            data: Config dictionary that may contain ${VAR_NAME} placeholders

        Returns:
            Config dictionary with placeholders expanded to actual env var values
        """
        expanded = {}
        env_var_pattern = re.compile(r'\$\{([^}]+)\}')

        for key, value in data.items():
            if isinstance(value, str):
                # Check if value is a placeholder like ${VAR_NAME}
                match = env_var_pattern.fullmatch(value)
                if match:
                    # Full string is a placeholder, replace with env var value or None
                    var_name = match.group(1)
                    expanded[key] = os.getenv(var_name)
                else:
                    # String may contain embedded placeholders, expand them
                    def replacer(m):
                        var_name = m.group(1)
                        return os.getenv(var_name, m.group(0))
                    expanded[key] = env_var_pattern.sub(replacer, value)
            else:
                expanded[key] = value

        return expanded

    @classmethod
    def load(cls, config_dir: Optional[Path] = None) -> "Config":
        """Load configuration from file and environment variables

        Priority (highest to lowest):
        1. Environment variables
        2. .automem/config.yml (searches upward from CWD like git)
        3. Defaults

        Searches for .automem/config.yml starting from current directory
        and walking up to filesystem root, stopping at first match.
        """
        if config_dir is None:
            # Search upward from CWD for .automem directory (git-style)
            config_dir = cls._find_config_dir()

        if config_dir is not None:
            # Resolve path and validate to prevent traversal attacks
            config_dir = config_dir.resolve()
            if ".." in str(config_dir):
                raise ValueError("Config directory path contains invalid components")

            config_file = config_dir / "config.yml"
            # Ensure config file is actually within config_dir
            try:
                config_file.resolve().relative_to(config_dir)
            except ValueError:
                raise ValueError("Config file path escapes config directory")
        else:
            config_file = None

        data = {}

        # Load from file if it exists
        if config_file and config_file.exists():
            try:
                file_data = yaml.safe_load(config_file.read_text())
                if file_data:
                    # Expand environment variable placeholders like ${VAR_NAME}
                    data = cls._expand_env_vars(file_data)
            except Exception as e:
                # Don't fail on bad config, just use defaults
                pass

        # Override with environment variables (highest priority)
        if token := os.getenv("AUTOMEM_API_TOKEN"):
            data["api_token"] = token
        if endpoint := os.getenv("AUTOMEM_ENDPOINT"):
            data["endpoint"] = endpoint
        if project := os.getenv("AUTOMEM_PROJECT_ID"):
            data["project_id"] = project

        return cls(**data)

    @staticmethod
    def _find_config_dir() -> Optional[Path]:
        """Search upward from CWD for .automem directory (git-style discovery)

        Returns:
            Path to .automem directory if found, None otherwise
        """
        current = Path.cwd().resolve()
        root = Path(current.root)

        # Walk up directory tree until we find .automem or hit root
        while current != root.parent:
            candidate = current / ".automem"
            if candidate.is_dir() and (candidate / "config.yml").exists():
                return candidate

            # Move up one directory
            parent = current.parent
            if parent == current:
                # Reached filesystem root
                break
            current = parent

        return None

    def save(self, config_dir: Optional[Path] = None) -> Path:
        """Save configuration to file"""
        if config_dir is None:
            config_dir = Path.cwd() / ".automem"

        # Resolve path and validate to prevent traversal attacks
        config_dir = config_dir.resolve()
        if ".." in str(config_dir):
            raise ValueError("Config directory path contains invalid components")

        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yml"
        # Ensure config file is actually within config_dir
        try:
            config_file.resolve().relative_to(config_dir)
        except ValueError:
            raise ValueError("Config file path escapes config directory")

        # Convert to dict, using env var placeholders for sensitive data
        # NEVER write actual API token to disk for security
        data = {
            "endpoint": self.endpoint,
            "api_token": "${AUTOMEM_API_TOKEN}",
            "project_id": self.project_id,
            "auto_consolidate": self.auto_consolidate,
            "session_tracking": self.session_tracking,
        }

        config_file.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        return config_file
