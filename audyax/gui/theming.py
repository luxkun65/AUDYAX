"""Changing the colors of the whole interface while it's open (color schemes, dynamic colors).

customtkinter widgets keep the colors they were given. These functions walk the widgets and
replace every color that belongs to the old palette with the same key's color of the new
one; style.C is updated too, so whatever is created afterwards already uses the new colors."""

import customtkinter as ctk

from audyax.core import colors
from audyax.gui.style import BASE_C, C

_CTK_TYPES = (ctk.CTkBaseClass, ctk.CTk, ctk.CTkToplevel)

COLOR_ATTRS = ("fg_color", "bg_color", "hover_color", "text_color", "text_color_disabled", "border_color",
               "button_color", "button_hover_color", "progress_color", "selected_color",
               "selected_hover_color", "unselected_color", "unselected_hover_color",
               "scrollbar_button_color", "scrollbar_button_hover_color", "placeholder_text_color",
               "dropdown_fg_color", "dropdown_hover_color", "dropdown_text_color")


def color_mapping(old: dict, new: dict) -> dict:
    """old color -> new color, key by key (only the ones that change)."""
    return {old[k]: new[k] for k in old if k in new and old[k] != new[k]}


def recolor(widget, mapping: dict) -> int:
    """Replaces, in `widget` and everything inside it, the colors found in `mapping`.
    Returns how many widgets changed."""
    if not mapping:
        return 0
    changed = 0
    stack = [widget]
    while stack:
        w = stack.pop()
        stack.extend(w.winfo_children())
        if not isinstance(w, _CTK_TYPES):   # only customtkinter widgets (and windows)
            continue
        updates = {}
        for attr in COLOR_ATTRS:
            try:
                value = w.cget(attr)
            except Exception:
                continue
            key = tuple(value) if isinstance(value, list) else value
            if key in mapping:
                updates[attr] = mapping[key]
        if updates:
            try:
                w.configure(**updates)
                changed += 1
            except Exception:
                pass
    return changed


def apply_scheme(root, scheme: str) -> dict:
    """Changes the color scheme of everything under `root` and of style.C.
    Returns the mapping used (old -> new)."""
    new = colors.scheme_palette(BASE_C, scheme)
    mapping = color_mapping(C, new)
    C.update(new)
    recolor(root, mapping)
    return mapping


def palette_for(hue) -> dict:
    """The palette of the current scheme turned to `hue` (dynamic colors); None = the scheme."""
    if hue is None:
        return dict(C)
    return colors.palette_for_hue(BASE_C, hue)
