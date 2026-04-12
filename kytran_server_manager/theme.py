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
    colors = theme.get("colors", {})
    fonts = theme.get("fonts", {})
    layout = theme.get("layout", {})

    # Standalone --sysops-* tokens
    for key, value in colors.items():
        lines.append(f"  --sysops-{key.replace('_', '-')}: {value};")
    for key, value in fonts.items():
        lines.append(f"  --sysops-font-{key}: '{value}', sans-serif;")
    for key, value in layout.items():
        lines.append(f"  --sysops-{key.replace('_', '-')}: {value};")

    # Platform-compatible --archie-* aliases (for shared CSS like components.css)
    lines.append("")
    lines.append("  /* Platform CSS compatibility aliases */")
    for key, value in colors.items():
        lines.append(f"  --archie-{key.replace('_', '-')}: {value};")
    for key, value in fonts.items():
        lines.append(f"  --archie-font-{key}: '{value}', sans-serif;")
    accent_rgb = colors.get("accent_rgb", "0, 229, 255")
    lines.append(f"  --archie-accent-alpha-10: rgba({accent_rgb}, 0.1);")
    lines.append(f"  --archie-accent-alpha-20: rgba({accent_rgb}, 0.2);")
    lines.append(f"  --archie-cyan: {colors.get('accent', '#00e5ff')};")

    # Spacing, radius, typography tokens
    lines.append("  --archie-space-1: 4px;")
    lines.append("  --archie-space-2: 8px;")
    lines.append("  --archie-space-2-5: 10px;")
    lines.append("  --archie-space-3: 12px;")
    lines.append("  --archie-space-4: 16px;")
    lines.append("  --archie-space-5: 20px;")
    lines.append("  --archie-space-6: 24px;")
    lines.append("  --archie-radius-sm: 4px;")
    lines.append("  --archie-radius-md: 8px;")
    lines.append("  --archie-radius-lg: 12px;")
    lines.append("  --archie-radius-full: 9999px;")
    lines.append("  --archie-text-xs: 0.75rem;")
    lines.append("  --archie-text-sm: 0.875rem;")
    lines.append("  --archie-text-base: 1rem;")
    lines.append("  --archie-font-semibold: 600;")
    lines.append("  --archie-transition-fast: 150ms ease;")
    lines.append("  --archie-duration-normal: 200ms;")
    lines.append("  --archie-ease-out: ease-out;")

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
