"""
Kytran Server Manager — Theme System
Config-driven theming: load theme.json, merge with defaults, generate CSS vars.
"""

import json
import os

THEMES_DIR = os.path.join(os.path.dirname(__file__), "themes")
DEFAULT_THEME = "kytran"

_DEFAULT_CONFIG = {
    "product_name": "Kytran Server Manager",
    "product_short": "KSM",
    "logo": "/static/img/kytran-logo.svg",
    "favicon": "/static/img/favicon.ico",
    "frame_style": "modern",
    "colors": {
        "accent": "#2563eb",
        "accent_bright": "#3b82f6",
        "accent_rgb": "37, 99, 235",
        "danger": "#ef4444",
        "success": "#22c55e",
        "warning": "#f59e0b",
        "bg_void": "#0f172a",
        "bg_primary": "#1e293b",
        "bg_secondary": "#334155",
        "bg_tertiary": "#475569",
        "text_primary": "#f8fafc",
        "text_secondary": "#cbd5e1",
        "text_muted": "#94a3b8",
        "border_default": "#334155",
    },
    "fonts": {
        "heading": "Inter",
        "body": "Inter",
        "mono": "JetBrains Mono",
    },
    "layout": {
        "border_radius": "8px",
        "nav_position": "top",
        "sidebar_width": "240px",
    },
    "custom_css": None,
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
