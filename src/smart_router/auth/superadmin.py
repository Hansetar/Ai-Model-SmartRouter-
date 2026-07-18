"""Super admin management - env var priority, encrypted config, setup wizard."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.config import get_settings

# Encrypted config file path
_ADMIN_CONFIG_PATH = Path(os.environ.get(
    "SMARTROUTER_ADMIN_CONFIG",
    str(Path(__file__).parent.parent.parent.parent / "data" / "admin_config.enc"),
))

# Environment variable keys
_ENV_ADMIN_PASSWORD = "SMARTROUTER_ADMIN_PASSWORD"
_ENV_ADMIN_ROLE = "SMARTROUTER_ADMIN_ROLE"


def _derive_key(password: str) -> bytes:
    """Derive a 32-byte key from password using SHA-256."""
    return hashlib.sha256(password.encode()).digest()


def _xor_cipher(data: bytes, key: bytes) -> bytes:
    """Simple XOR cipher for config encryption (not production-grade, but prevents casual reading)."""
    key_repeated = key * (len(data) // len(key) + 1)
    return bytes(a ^ b for a, b in zip(data, key_repeated))


def encrypt_config(data: Dict[str, Any], master_key: str) -> str:
    """Encrypt config data with master key."""
    json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
    key = _derive_key(master_key)
    encrypted = _xor_cipher(json_bytes, key)
    return base64.b64encode(encrypted).decode("ascii")


def decrypt_config(encrypted_str: str, master_key: str) -> Optional[Dict[str, Any]]:
    """Decrypt config data with master key."""
    try:
        encrypted = base64.b64decode(encrypted_str)
        key = _derive_key(master_key)
        decrypted = _xor_cipher(encrypted, key)
        return json.loads(decrypted.decode("utf-8"))
    except Exception:
        return None


def save_admin_config(data: Dict[str, Any], master_key: str) -> None:
    """Save encrypted admin config to file."""
    _ADMIN_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    encrypted = encrypt_config(data, master_key)
    _ADMIN_CONFIG_PATH.write_text(encrypted)


def load_admin_config(master_key: str) -> Optional[Dict[str, Any]]:
    """Load and decrypt admin config from file."""
    if not _ADMIN_CONFIG_PATH.exists():
        return None
    encrypted = _ADMIN_CONFIG_PATH.read_text().strip()
    if not encrypted:
        return None
    return decrypt_config(encrypted, master_key)


def delete_admin_config() -> bool:
    """Delete admin config file (for reset). Returns True if file was deleted."""
    if _ADMIN_CONFIG_PATH.exists():
        _ADMIN_CONFIG_PATH.unlink()
        return True
    return False


def is_setup_required() -> bool:
    """Check if super admin setup is required.
    
    Setup is required when:
    1. No SMARTROUTER_ADMIN_PASSWORD env var
    2. No encrypted admin config file exists
    """
    if os.environ.get(_ENV_ADMIN_PASSWORD):
        return False
    if _ADMIN_CONFIG_PATH.exists():
        return False
    return True


def get_super_admin_password() -> Optional[str]:
    """Get super admin password from env var (priority) or config file."""
    # Env var takes priority
    env_pass = os.environ.get(_ENV_ADMIN_PASSWORD)
    if env_pass:
        return env_pass
    # Try loading from encrypted config (using default key)
    config = load_admin_config("smartrouter-default-key")
    if config:
        return config.get("admin_password")
    return None


def get_super_admin_role() -> str:
    """Get super admin role from env var or config."""
    env_role = os.environ.get(_ENV_ADMIN_ROLE)
    if env_role:
        return env_role
    config = load_admin_config("smartrouter-default-key")
    if config:
        return config.get("admin_role", "admin")
    return "admin"


# User management (persisted in encrypted config)
def list_users() -> List[Dict[str, Any]]:
    """List all managed users."""
    config = load_admin_config("smartrouter-default-key")
    if not config:
        return []
    return config.get("users", [])


def add_user(username: str, password: str, role: str = "user", tenant_id: str = "") -> bool:
    """Add a new user to the system."""
    config = load_admin_config("smartrouter-default-key") or {
        "admin_password": get_super_admin_password() or "",
        "users": [],
    }
    users = config.get("users", [])
    # Check duplicate
    if any(u.get("username") == username for u in users):
        return False
    users.append({
        "username": username,
        "password_hash": hashlib.sha256(password.encode()).hexdigest(),
        "role": role,
        "tenant_id": tenant_id,
    })
    config["users"] = users
    save_admin_config(config, "smartrouter-default-key")
    return True


def update_user(username: str, role: Optional[str] = None, tenant_id: Optional[str] = None,
                password: Optional[str] = None) -> bool:
    """Update an existing user."""
    config = load_admin_config("smartrouter-default-key")
    if not config:
        return False
    users = config.get("users", [])
    for u in users:
        if u.get("username") == username:
            if role is not None:
                u["role"] = role
            if tenant_id is not None:
                u["tenant_id"] = tenant_id
            if password is not None:
                u["password_hash"] = hashlib.sha256(password.encode()).hexdigest()
            save_admin_config(config, "smartrouter-default-key")
            return True
    return False


def delete_user(username: str) -> bool:
    """Delete a user from the system."""
    config = load_admin_config("smartrouter-default-key")
    if not config:
        return False
    users = config.get("users", [])
    new_users = [u for u in users if u.get("username") != username]
    if len(new_users) == len(users):
        return False
    config["users"] = new_users
    save_admin_config(config, "smartrouter-default-key")
    return True


def verify_user_password(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Verify a user's password and return user info if valid."""
    config = load_admin_config("smartrouter-default-key")
    if not config:
        return None
    users = config.get("users", [])
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    for u in users:
        if u.get("username") == username and u.get("password_hash") == password_hash:
            return {"username": u["username"], "role": u.get("role", "user"), "tenant_id": u.get("tenant_id", "")}
    return None
