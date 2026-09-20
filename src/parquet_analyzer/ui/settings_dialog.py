from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QKeySequenceEdit,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..io.settings import (
    DEFAULT_SHORTCUTS,
    FFT_ENABLED,
    LANGUAGES,
    SCROLL_ACTIONS,
    SCROLL_MODIFIERS,
    Settings,
    normalize_scroll_bindings,
)
from .i18n import LANGUAGE_LABELS, scroll_action_label, scroll_modifier_label, shortcut_label, tr


class SettingsDialog(QDialog):
    """Settings menu: auto-redraw, scroll-zoom assignment, shortcuts, language
    (specification.md 5.10)."""

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("toolbar.settings"))
        self._settings = settings

        self.auto_redraw_check = QCheckBox(tr("settings.auto_redraw_checkbox"))
        self.auto_redraw_check.setChecked(settings.auto_redraw)

        self.downsample_spin = QSpinBox()
        self.downsample_spin.setRange(1, 20)
        self.downsample_spin.setValue(settings.downsample_pixel_ratio)

        self.eager_load_limit_spin = QSpinBox()
        self.eager_load_limit_spin.setRange(1, 100_000)
        self.eager_load_limit_spin.setSuffix(" MB")
        self.eager_load_limit_spin.setValue(settings.eager_load_limit_mb)
        self.eager_load_limit_spin.setToolTip(tr("settings.eager_load_limit_tooltip"))

        self.language_combo = QComboBox()
        for language in LANGUAGES:
            self.language_combo.addItem(LANGUAGE_LABELS[language], language)
        self.language_combo.setCurrentIndex(LANGUAGES.index(settings.language))
        self.language_combo.setToolTip(tr("settings.language_restart_note"))

        general_form = QFormLayout()
        general_form.addRow(self.auto_redraw_check)
        general_form.addRow(tr("settings.downsample_density_label"), self.downsample_spin)
        general_form.addRow(tr("settings.eager_load_limit_label"), self.eager_load_limit_spin)
        general_form.addRow(tr("settings.language_label"), self.language_combo)

        # scroll_bindings is a modifier -> action permutation ({"none", "ctrl",
        # "shift"} each assigned exactly one of x_zoom/y_zoom/pan) — one combo per
        # modifier, each offering every action. Picking an action already assigned to
        # another modifier swaps the two (_on_scroll_binding_changed) rather than
        # leaving two modifiers bound to the same action, which would make the third
        # action unreachable by scroll at all.
        self._scroll_bindings = normalize_scroll_bindings(settings.scroll_bindings)
        self._scroll_combos: dict[str, QComboBox] = {}
        scroll_form = QFormLayout()
        for modifier in SCROLL_MODIFIERS:
            combo = QComboBox()
            for action in SCROLL_ACTIONS:
                combo.addItem(scroll_action_label(action), action)
            combo.setCurrentIndex(SCROLL_ACTIONS.index(self._scroll_bindings[modifier]))
            combo.currentIndexChanged.connect(lambda _idx, m=modifier: self._on_scroll_binding_changed(m))
            self._scroll_combos[modifier] = combo
            scroll_form.addRow(scroll_modifier_label(modifier), combo)

        self._shortcut_edits: dict[str, QKeySequenceEdit] = {}
        shortcuts_form = QFormLayout()
        for key in DEFAULT_SHORTCUTS:
            if key == "fft" and not FFT_ENABLED:
                continue  # hidden from the GUI while still under debugging
            edit = QKeySequenceEdit()
            seq = settings.shortcuts.get(key, "")
            if seq:
                edit.setKeySequence(seq)
            self._shortcut_edits[key] = edit
            shortcuts_form.addRow(shortcut_label(key), edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("settings.section_general")))
        layout.addLayout(general_form)
        layout.addWidget(QLabel(tr("settings.section_scroll")))
        layout.addLayout(scroll_form)
        layout.addWidget(QLabel(tr("settings.section_shortcuts")))
        layout.addLayout(shortcuts_form)
        layout.addWidget(buttons)

    def _on_scroll_binding_changed(self, modifier: str) -> None:
        new_action = self._scroll_combos[modifier].currentData()
        old_action = self._scroll_bindings[modifier]
        if new_action == old_action:
            return
        # Keep the mapping a permutation: whichever other modifier currently owns
        # new_action swaps down to this modifier's previous action, rather than two
        # modifiers ending up bound to the same action (which would leave the third
        # action unreachable by scroll at all).
        other_modifier = next(m for m, a in self._scroll_bindings.items() if a == new_action and m != modifier)
        self._scroll_bindings[modifier] = new_action
        self._scroll_bindings[other_modifier] = old_action
        other_combo = self._scroll_combos[other_modifier]
        other_combo.blockSignals(True)
        other_combo.setCurrentIndex(SCROLL_ACTIONS.index(old_action))
        other_combo.blockSignals(False)

    def result_settings(self) -> Settings:
        shortcuts = {key: edit.keySequence().toString() for key, edit in self._shortcut_edits.items()}
        return replace(
            self._settings,
            auto_redraw=self.auto_redraw_check.isChecked(),
            downsample_pixel_ratio=self.downsample_spin.value(),
            eager_load_limit_mb=self.eager_load_limit_spin.value(),
            scroll_bindings=dict(self._scroll_bindings),
            shortcuts=shortcuts,
            language=self.language_combo.currentData(),
        )
