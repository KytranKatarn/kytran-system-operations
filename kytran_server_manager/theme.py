"""
System Operations — Theme System
Config-driven theming: load theme.json, merge with defaults, generate CSS vars.
"""

import json
import os

THEMES_DIR = os.path.join(os.path.dirname(__file__), "themes")
DEFAULT_THEME = "lcars"

_DEFAULT_CONFIG = {
    "product_name": "System Operations",
    "product_short": "SYSOPS",
    "logo": "/static/img/archie-logo.svg",
    "favicon": "/static/img/favicon.ico",
    "frame_style": "lcars",
    "colors": {
        "accent": "#00e5ff",
        "accent_bright": "#4df0ff",
        "accent_rgb": "0, 229, 255",
        "danger": "#ef4444",
        "success": "#22c55e",
        "warning": "#f59e0b",
        "bg_void": "#000000",
        "bg_primary": "#0a0a1a",
        "bg_secondary": "#1a1a2e",
        "bg_tertiary": "#2a2a3e",
        "text_primary": "#e0e0e0",
        "text_secondary": "#b0b0b0",
        "text_muted": "#808080",
        "border_default": "#2a2a3e",
    },
    "fonts": {
        "heading": "Orbitron",
        "body": "IBM Plex Mono",
        "mono": "IBM Plex Mono",
    },
    "layout": {
        "border_radius": "20px",
        "nav_position": "top",
        "sidebar_width": "240px",
    },
    "custom_css": "/static/css/lcars-frames.css",
}


def load_theme(theme_name=None):
    if theme_name is None:
        theme_name = os.environ.get("SYSOPS_THEME", DEFAULT_THEME)
    theme_path = os.path.join(THEMES_DIR, f"{theme_name}.json")
    if not os.path.exists(theme_path):
        return dict(_DEFAULT_CONFIG)
    with open(theme_path, "r") as f:
        override = json.load(f)
    merged = {}
    for key in _DEFAULT_CONFIG:
        if key in override:
            if isinstance(_DEFAULT_CONFIG[key], dict) and isinstance(override[key], dict):
                merged[key] = {**_DEFAULT_CONFIG[key], **override[key]}
            else:
                merged[key] = override[key]
        else:
            merged[key] = (
                _DEFAULT_CONFIG[key] if not isinstance(_DEFAULT_CONFIG[key], dict) else dict(_DEFAULT_CONFIG[key])
            )
    # Include any extra keys from override not in defaults
    for key in override:
        if key not in merged:
            merged[key] = override[key]
    return merged


def generate_theme_css(theme):
    lines = [":root {"]
    for key, value in theme.get("colors", {}).items():
        lines.append(f"  --sysops-{key.replace('_', '-')}: {value};")
    for key, value in theme.get("fonts", {}).items():
        lines.append(f"  --sysops-font-{key}: '{value}', sans-serif;")
    for key, value in theme.get("layout", {}).items():
        lines.append(f"  --sysops-{key.replace('_', '-')}: {value};")
    lines.append("}")
    return "\n".join(lines)


def write_theme_css(theme, output_path):
    css = generate_theme_css(theme)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(css)


def init_theme(app):
    theme = load_theme()
    css_path = os.path.join(app.static_folder, "css", "system-operations-theme-vars.css")
    write_theme_css(theme, css_path)

    @app.context_processor
    def inject_sysops_theme():
        return {"sysops_theme": theme}
